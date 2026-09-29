"""把同一份审阅文档写为可移动的三栏报告目录。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import base64
from html import escape
from importlib.resources import files
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import quote

from .comparison import ComparisonResult
from .contract import ChangeDetail, Diagnostic, DiagnosticsDocument, PrimaryChange, ReviewDocument, SourceLocation, dumps
from .preview import render_preview
from .structure import ParsedNode, ParsedProject


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_KIND_LABELS = {"added": "新增", "removed": "删除", "modified": "修改", "moved": "移动"}
_CATEGORY_LABELS = {"text": "正文", "equation": "公式", "figure": "图", "table": "表格", "citation": "引用", "comment": "注释"}
_SEVERITY_LABELS = {"info": "提示", "warning": "警告", "error": "错误"}
_MAX_EMBEDDED_ASSET = 25 * 1024 * 1024
_MAX_EMBEDDED_TOTAL = 100 * 1024 * 1024


class ReportPathError(OSError):
    """受管报告路径包含链接或越出输出目录。"""


@dataclass(frozen=True)
class ReportResult:
    directory: Path
    document: ReviewDocument
    single_html: Path | None = None

    @property
    def html(self) -> Path:
        return self.directory / "report.html"

    @property
    def diff_json(self) -> Path:
        return self.directory / "diff.json"


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _editor_link(project: ParsedProject, location: SourceLocation | None,
                 template: str | None) -> str | None:
    if not template or not location or not location.file or not location.start_line:
        return None
    source = project.expanded.source
    if source.identity.kind == "git":
        return None
    root = source.worktree_origin or source.root
    path = (root / location.file).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return None
    return template.format(path=quote(str(path), safe="/"), line=location.start_line,
                           column=location.start_column or 1)


def _source_actions(old: ParsedProject, new: ParsedProject,
                    old_location: SourceLocation | None, new_location: SourceLocation | None,
                    template: str | None) -> str:
    parts = []
    for side, label, project, location in (("old", "旧", old, old_location), ("new", "新", new, new_location)):
        if location is None:
            continue
        copy = f'<button type="button" class="copy-source" data-location="{_e(_location(location))}">复制{label}侧位置</button>'
        link = _editor_link(project, location, template)
        if link:
            copy += f'<a class="editor-link" href="{_e(link)}" title="在编辑器中打开{label}侧源码">在编辑器打开{label}侧</a>'
        parts.append(copy)
    return '<div class="source-actions">' + " ".join(parts) + '</div>' if parts else ""


def _validate_editor_template(template: str | None) -> None:
    if template is None:
        return
    if not re.fullmatch(r"vscode://file/\{path\}:\{line\}(?::\{column\})?", template):
        raise ValueError("编辑器模板仅支持 vscode://file/{path}:{line}[:{column}]")


def _single_file_html(html: str, directory: Path) -> str:
    """仅内嵌报告自身生成的受管资源，不将任意外部路径带入单文件。"""
    embedded_total = 0
    def replace_url(match: re.Match[str]) -> str:
        nonlocal embedded_total
        attr, url = match.group(1), match.group(2)
        from urllib.parse import unquote
        relative = Path(unquote(url))
        if (relative.is_absolute() or ".." in relative.parts or
                relative.parts[0] not in {"assets", "previews"}):
            raise ReportPathError("单文件报告包含不安全的本地资源路径")
        path = directory / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory):
            raise ReportPathError(f"单文件资源无法内嵌：{relative}")
        size = path.stat().st_size
        embedded_total += size
        if size > _MAX_EMBEDDED_ASSET or embedded_total > _MAX_EMBEDDED_TOTAL:
            raise ReportPathError(f"单文件资源超出内嵌上限：{relative}")
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{attr}="data:{media};base64,{encoded}"'
    return re.sub(r'(src|href)="((?:assets|previews)/[^\"]+)"', replace_url, html)


def _location(location: SourceLocation | None) -> str:
    if location is None:
        return "该侧无内容"
    if location.file is None or location.start_line is None:
        file = f"{location.file}；" if location.file else ""
        return f"位置约略：{file}{location.uncertainty_reason or '来源未知'}"
    end = location.end_line or location.start_line
    span = str(location.start_line) if end == location.start_line else f"{location.start_line}–{end}"
    qualifier = "约略位置" if location.confidence < 1 or location.uncertainty_reason else "原文位置"
    reason = f"；{location.uncertainty_reason}" if location.uncertainty_reason else ""
    return f"{qualifier}：{location.file}，第 {span} 行{reason}"


def _asset_diagnostic(code: str, message: str, node: ParsedNode, side: str) -> Diagnostic:
    return Diagnostic(code, "warning", message,
                      source_old=node.review.source if side == "old" else None,
                      source_new=node.review.source if side == "new" else None)


def _dependency(project: ParsedProject, node: ParsedNode, asset: str) -> str | None:
    candidates = [dep for dep in project.expanded.dependencies if dep.kind == "graphic" and dep.argument == asset
                  and dep.source_start is not None
                  and any(dep.referenced_from == origin.origin.file
                          and dep.include_instance == origin.origin.include_instance
                          and origin.origin.start <= dep.source_start < origin.origin.end
                          for origin in node.origins)]
    if len({dep.file for dep in candidates}) == 1:
        return candidates[0].file
    return None


def _write_managed(directory: Path, relative: Path, *, content: bytes | None = None,
                   source: Path | None = None) -> Path:
    """逐级拒绝受管目录中的链接，临时文件写完后替换目标。"""
    if relative.is_absolute() or not relative.parts or any(part in ("..", "") for part in relative.parts):
        raise ReportPathError("报告资源路径必须位于输出目录内")
    try:
        parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ReportPathError("报告输出目录不可安全打开") from exc
    temporary = None
    try:
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, dir_fd=parent_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            except OSError as exc:
                raise ReportPathError(f"报告目录含链接或非目录：{part}") from exc
            os.close(parent_fd)
            parent_fd = next_fd
        name = relative.parts[-1]
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
                raise ReportPathError("报告目标是链接或非普通文件")
        temporary_name = f".latex-review-{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            temporary_fd = os.open(temporary_name, flags, 0o644, dir_fd=parent_fd)
        except OSError as exc:
            raise ReportPathError("报告暂存文件无法安全创建") from exc
        temporary = temporary_name
        with os.fdopen(temporary_fd, "wb") as output:
            if content is not None:
                output.write(content)
            elif source is not None:
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
            else:
                raise ValueError("缺少写入内容")
        try:
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            raise ReportPathError("报告目标无法安全替换") from exc
        temporary = None
        return directory / relative
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        os.close(parent_fd)


def _copy_asset(project: ParsedProject, relative: str, directory: Path, side: str) -> tuple[Path, str]:
    root = project.expanded.source.root.resolve()
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise OSError("资源不在比较版本目录内或已缺失")
    destination = _write_managed(directory, Path("assets") / side / source.relative_to(root), source=source)
    url = "/".join(quote(part) for part in destination.relative_to(directory).parts)
    return destination, url


def _pdf_preview(source: Path, directory: Path, relative: Path, converter: str, timeout: float) -> Path | None:
    with tempfile.TemporaryDirectory(prefix="latex-review-pdf-") as temporary:
        output = Path(temporary) / "preview.png"
        try:
            result = subprocess.run((converter, "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1200",
                                     "-png", str(source), str(output.with_suffix(""))),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if (result.returncode == 0 and not output.is_symlink() and output.is_file()
                and 0 < output.stat().st_size <= 12 * 1024 * 1024):
            return _write_managed(directory, relative, source=output)
    return None


def _figure_assets(old: ParsedProject, new: ParsedProject, directory: Path,
                   converter: str | None, timeout: float) -> tuple[dict[tuple[str, str], str], list[Diagnostic]]:
    html: dict[tuple[str, str], str] = {}
    diagnostics: list[Diagnostic] = []
    for side, project in (("old", old), ("new", new)):
        for node in project.nodes:
            if node.review.type != "figure" or not node.assets:
                continue
            cards = []
            for asset in node.assets:
                relative = _dependency(project, node, asset)
                provenance = _location(node.review.source)
                if relative is None:
                    diagnostics.append(_asset_diagnostic("report_asset_missing", f"图资源无法定位：{asset}", node, side))
                    cards.append(f'<div class="asset-card asset-missing" role="note"><strong>图缺失：{_e(asset)}</strong>'
                                 f'<p>{_e(provenance)}</p><p>尺寸：未知</p></div>')
                    continue
                try:
                    copied, url = _copy_asset(project, relative, directory, side)
                except ReportPathError:
                    raise
                except OSError:
                    diagnostics.append(_asset_diagnostic("report_asset_missing", f"图资源复制失败：{asset}", node, side))
                    cards.append(f'<div class="asset-card asset-missing" role="note"><strong>图缺失：{_e(asset)}</strong>'
                                 f'<p>{_e(provenance)}</p><p>尺寸：未知</p></div>')
                    continue
                suffix = copied.suffix.lower()
                picture = ""
                if suffix in _IMAGE_SUFFIXES:
                    picture = f'<img src="{_e(url)}" alt="图资源 {_e(asset)}" loading="lazy">'
                elif suffix == ".pdf":
                    preview_relative = Path("previews") / side / f"{copied.relative_to(directory / 'assets' / side)}.png"
                    preview = _pdf_preview(copied, directory, preview_relative, converter, timeout) if converter else None
                    if preview is not None:
                        preview_url = "/".join(quote(part) for part in preview.relative_to(directory).parts)
                        picture = f'<img src="{_e(preview_url)}" alt="PDF 图 {_e(asset)} 的第一页预览" loading="lazy">'
                    else:
                        diagnostics.append(_asset_diagnostic("report_pdf_preview_unavailable",
                                                            f"PDF 图预览不可用：{asset}", node, side))
                        picture = '<p class="asset-fallback">PDF 预览不可用，可打开原始文件。</p>'
                else:
                    diagnostics.append(_asset_diagnostic("report_asset_unsupported", f"不支持图预览：{asset}", node, side))
                    picture = '<p class="asset-fallback">该格式暂无预览，可打开原始文件。</p>'
                cards.append(f'<div class="asset-card">{picture}<p>图文件：<a href="{_e(url)}">{_e(asset)}</a></p>'
                             f'<p>{_e(provenance)}</p><p>尺寸：未知</p></div>')
            html[(side, node.review.id)] = "".join(cards)
    return html, diagnostics


def _detail_target(document: ReviewDocument, change: PrimaryChange, detail: ChangeDetail,
                   side: str, anchors: set[str]) -> str:
    parent = change.old_node_id if side == "old" else change.new_node_id
    location = detail.source_old if side == "old" else detail.source_new
    node_types = {"citation": {"citation"}, "equation": {"inline_math"}, "table": {"table"}}.get(detail.category, set())
    if parent and location and node_types:
        nodes = document.nodes_old if side == "old" else document.nodes_new
        candidates = [node.id for node in nodes if node.parent_id == parent and node.type in node_types
                      and node.source == location and f"{side}-{node.id}" in anchors]
        if len(candidates) == 1:
            return candidates[0]
    return parent or ""


def _diagnostics_html(document: ReviewDocument) -> str:
    items = []
    for diagnostic in sorted(document.diagnostics, key=lambda item: (item.code, item.message)):
        items.append(f'<li><strong>{_e(_SEVERITY_LABELS.get(diagnostic.severity, diagnostic.severity))} · '
                     f'{_e(diagnostic.code)}</strong>：{_e(diagnostic.message)}'
                     f'<small>旧：{_e(_location(diagnostic.source_old))}；新：{_e(_location(diagnostic.source_new))}</small></li>')
    body = f'<ul>{"".join(items)}</ul>' if items else '<p>无诊断。</p>'
    return f'<section class="report-diagnostics" aria-label="诊断" data-count="{len(items)}">' \
           f'<h3>诊断 {len(items)}</h3>{body}</section>'


def _change_cards(document: ReviewDocument, anchors: set[str], old: ParsedProject,
                  new: ParsedProject, editor_template: str | None) -> str:
    cards = []
    for change in document.changes:
        categories = sorted(set(change.categories) | {detail.category for detail in change.details})
        old_id, new_id = change.old_node_id or "", change.new_node_id or ""
        badges = "".join(f'<span class="badge">{_e(_CATEGORY_LABELS.get(category, category))}</span>' for category in categories)
        details = []
        for detail in change.details:
            old_source = detail.source_old or change.source_old
            new_source = detail.source_new or change.source_new
            detail_old_id = _detail_target(document, change, detail, "old", anchors)
            detail_new_id = _detail_target(document, change, detail, "new", anchors)
            details.append(f'<li><button type="button" class="detail-jump" data-old="{_e(detail_old_id)}" '
                           f'data-new="{_e(detail_new_id)}" data-old-location="{_e(_location(old_source))}" '
                           f'data-new-location="{_e(_location(new_source))}">'
                           f'{_e(_CATEGORY_LABELS.get(detail.category, detail.category))} · '
                           f'{_e(_KIND_LABELS.get(detail.kind, detail.kind))}：{_e(detail.summary)}</button>'
                           f'<small>{_e(detail.old_text or "∅")} → {_e(detail.new_text or "∅")}</small>'
                           f'{_source_actions(old, new, old_source, new_source, editor_template)}</li>')
        cards.append(f'<article class="change-card" id="{_e(change.id)}" data-kind="{_e(change.kind)}" '
                     f'data-categories="{_e(" ".join(categories))}">'
                     f'<button type="button" class="change-jump" data-old="{_e(old_id)}" data-new="{_e(new_id)}" '
                     f'data-old-location="{_e(_location(change.source_old))}" '
                     f'data-new-location="{_e(_location(change.source_new))}">'
                     f'<span class="kind kind-{_e(change.kind)}">{_e(_KIND_LABELS.get(change.kind, change.kind))}</span> '
                     f'<strong>{_e(change.summary)}</strong></button>'
                     f'<p class="card-meta">{badges} · 匹配置信度 {change.matching_confidence:.2f}</p>'
                     f'<p class="card-source">旧：{_e(_location(change.source_old))}<br>新：{_e(_location(change.source_new))}</p>'
                     f'{_source_actions(old, new, change.source_old, change.source_new, editor_template)}'
                     f'<ul class="detail-list">{"".join(details)}</ul></article>')
    return "".join(cards) or '<p class="empty-list">没有检测到主变更。</p>'


def _report_html(document: ReviewDocument, preview_html: str, old: ParsedProject,
                 new: ParsedProject, editor_template: str | None,
                 pairs: tuple[tuple[str, str], ...]) -> str:
    # 预览层拥有节点 HTML 和公式降级逻辑；报告仅将它们嵌入三栏容器。
    style = re.search(r"<style>(.*?)</style>", preview_html, re.S)
    main = re.search(r"<main>(.*?)</main>", preview_html, re.S)
    scripts = re.search(r"(<script>.*?</script>\s*<script async .*?</script>)", preview_html, re.S)
    if not (style and main and scripts):
        raise ValueError("预览页面缺少报告所需的节点或公式脚本")
    counts = document.summary
    category_counts = " · ".join(f"{_CATEGORY_LABELS.get(key, key)} {value}" for key, value in counts.category_hits.items())
    anchors = set(re.findall(r'\bid="([^"]+)"', main.group(1)))
    cards = _change_cards(document, anchors, old, new, editor_template)
    diagnostics_html = _diagnostics_html(document)
    interaction = files("latex_review").joinpath("report_interaction.js").read_text(encoding="utf-8")
    pairs_json = json.dumps(pairs, ensure_ascii=False).replace("<", "\\u003c")
    document_json = dumps(document).replace("<", "\\u003c")
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaTeX 三栏审阅报告</title><style>{style.group(1)}
body{{background:#f3f4f6;color:#17212d}}header{{padding:.8rem 1.2rem}}header h1{{font-size:1.25rem}}
.summary{{margin:.3rem 0;font-weight:650}}.summary-extra{{margin:.2rem 0;color:#374151}}
main{{grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;padding:.7rem;align-items:start}}
.preview-side,.changes-side{{height:calc(100vh - 16rem);min-height:20rem;max-height:none;overflow:auto;background:white;border:1px solid #b8c0ca;border-radius:.35rem;padding:1rem}}
.changes-side h2{{margin:0 0 .8rem}}.preview-side .is-highlighted{{outline:3px solid #9a5700;outline-offset:3px;background:#fff5d9}}
.change-card{{border:1px solid #adb7c4;border-radius:.35rem;margin:.6rem 0;padding:.7rem;background:#fff}}
.change-card[hidden]{{display:none}}.change-card:focus-within{{outline:2px solid #244e9b}}
button,select{{font:inherit}}button:focus-visible,select:focus-visible,a:focus-visible{{outline:3px solid #1d4ed8;outline-offset:2px}}
.change-jump,.detail-jump{{cursor:pointer;border:0;background:transparent;text-align:left;color:#12233b;padding:.15rem}}
.change-jump:hover,.detail-jump:hover{{text-decoration:underline}}.detail-list{{padding-left:1.3rem;margin:.35rem 0}}
.detail-list li{{margin:.3rem 0}}.detail-list small{{display:block;color:#374151;overflow-wrap:anywhere}}
.kind{{display:inline-block;border-radius:.2rem;padding:.1rem .3rem;font-weight:700;border:1px solid #53657a}}
.kind-added{{background:#d8f0df}}.kind-removed{{background:#fce2df}}.kind-modified{{background:#fff0c9}}
.badge{{display:inline-block;background:#e7edf7;padding:.05rem .25rem;border-radius:.2rem;margin-right:.2rem}}
.card-meta,.card-source{{font-size:.85rem;color:#374151;margin:.4rem 0}}
.filters{{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0}}.filters label{{font-size:.9rem;font-weight:600}}
.filters select{{min-width:7rem;padding:.2rem;border:1px solid #6b7280;background:white;color:#17212d}}
.side-empty{{display:none;padding:.6rem;background:#fff0d6;color:#4b3000;border:1px solid #9a5700;margin:.5rem 0}}
.side-empty.is-visible{{display:block}}.jump-status{{min-height:1.5rem;color:#374151;font-size:.88rem}}
.asset-card{{border:1px solid #9caaba;padding:.5rem;margin:.5rem 0;background:#f8fafc;overflow-wrap:anywhere}}
.asset-card img{{max-width:100%;height:auto;display:block}}.asset-missing{{border-color:#a33427;background:#fff0ed}}
.asset-card p{{margin:.2rem 0}}.asset-fallback{{color:#783f14}}
.report-diagnostics{{border:1px solid #a9b4c1;background:#f8fafc;padding:.5rem;margin:.6rem 0}}
.report-diagnostics h3{{margin:.1rem 0}}.report-diagnostics ul{{margin:.3rem 0;padding-left:1.2rem}}
.report-diagnostics li{{margin:.4rem 0;overflow-wrap:anywhere}}.report-diagnostics small{{display:block;color:#374151}}
.report-controls{{display:flex;gap:1rem;flex-wrap:wrap;margin:.4rem 0}}.source-actions{{display:flex;gap:.45rem;flex-wrap:wrap;margin:.25rem 0}}
.source-actions button,.source-actions a{{font-size:.8rem}}.context-hidden{{display:none!important}}
@media(max-width:1000px){{main{{grid-template-columns:1fr}}.preview-side,.changes-side{{height:auto;max-height:none}}}}
</style></head><body><header><h1>LaTeX 三栏审阅报告</h1>
<p class="notice">内容预览供审阅，不代表最终编译版式；来源位置可能为近似值。</p>
<p class="summary" id="report-summary" data-changes="{counts.changes}">主变更 {counts.changes} · 正文增加 {counts.added_words} 词 · 删除 {counts.removed_words} 词</p>
<p class="summary-extra">{_e(category_counts)}</p>
<div class="report-controls"><label><input type="checkbox" id="sync-scroll"> 同步滚动</label>
<label>阅读范围 <select id="reading-mode"><option value="full">完整文档</option><option value="context">变更上下文</option></select></label>
<button type="button" id="previous-change">上一变更</button><button type="button" id="next-change">下一变更</button></div>
<p class="notice">按 Alt+↑ / Alt+↓ 可跳到上一项 / 下一项；输入时快捷键不生效。公式排版可能需要联网。</p>
<p id="math-status" role="status">正在加载在线公式排版；原始 TeX 可直接阅读。</p></header>
<main>{main.group(1)}<section class="changes-side" aria-label="变更" id="changes-side"><h2>变更</h2>
<div class="filters"><label>操作 <select id="kind-filter"><option value="all">全部</option><option value="added">新增</option><option value="removed">删除</option><option value="modified">修改</option></select></label>
<label>类别 <select id="category-filter"><option value="all">全部</option><option value="text">正文</option><option value="equation">公式</option><option value="figure">图</option><option value="table">表格</option><option value="citation">引用</option><option value="comment">注释</option></select></label></div>
<p id="filter-count" role="status"></p><p id="jump-status" class="jump-status" role="status"></p>{diagnostics_html}{cards}</section></main>
<script type="application/json" id="review-data">{document_json}</script>
<script>window.reviewNodePairs={pairs_json};</script><script>{interaction}</script>
{scripts.group(1)}
</body></html>'''


def write_report(old: ParsedProject, new: ParsedProject, comparison: ComparisonResult,
                 output_dir: str | Path, *, pdf_converter: str | None = None,
                 conversion_timeout: float = 8.0, editor_url_template: str | None = None,
                 single_file: bool = False) -> ReportResult:
    """在来源快照有效期内写入 report.html、diff.json、diagnostics.json 和双侧资源。"""
    if conversion_timeout <= 0:
        raise ValueError("转换超时必须为正数")
    _validate_editor_template(editor_url_template)
    requested_directory = Path(output_dir)
    if requested_directory.is_symlink():
        raise ReportPathError("报告输出目录不可为符号链接")
    directory = requested_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    converter = shutil.which("pdftoppm") if pdf_converter is None else pdf_converter
    figure_html, asset_diagnostics = _figure_assets(old, new, directory, converter, conversion_timeout)
    parsed_ids = {"old": {node.review.id for node in old.nodes}, "new": {node.review.id for node in new.nodes}}
    extra_nodes = {
        "old": tuple(node for node in comparison.document.nodes_old if node.id not in parsed_ids["old"]),
        "new": tuple(node for node in comparison.document.nodes_new if node.id not in parsed_ids["new"]),
    }
    preview = render_preview(old, new, figure_assets=figure_html, extra_nodes=extra_nodes)
    diagnostics = tuple(dict.fromkeys((*comparison.document.diagnostics, *preview.diagnostics, *asset_diagnostics)))
    document = replace(comparison.document, diagnostics=diagnostics)
    diff_json = dumps(document)
    pairs = tuple((pair.old_id, pair.new_id) for pair in comparison.mapping.pairs)
    html = _report_html(document, preview.html, old, new, editor_url_template, pairs)
    _write_managed(directory, Path("diff.json"), content=diff_json.encode("utf-8"))
    _write_managed(directory, Path("diagnostics.json"),
                   content=dumps(DiagnosticsDocument(diagnostics)).encode("utf-8"))
    _write_managed(directory, Path("report.html"), content=html.encode("utf-8"))
    single = (_write_managed(directory, Path("report-single.html"),
                             content=_single_file_html(html, directory).encode("utf-8")) if single_file else None)
    return ReportResult(directory, document, single)
