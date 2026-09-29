"""把同一份审阅文档写为可移动的三栏报告目录。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
import os
from pathlib import Path
import re
import resource
import secrets
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import quote

from .comparison import ComparisonResult
from .contract import (ChangeDetail, Diagnostic, DiagnosticsDocument, PrimaryChange, ReviewDocument,
                       ReviewNode, SourceLocation, build_summary, dumps)
from .preview import render_preview
from .structure import ParsedNode, ParsedProject
from .sources import ExpandedProject


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_LINKABLE_ASSET_SUFFIXES = _IMAGE_SUFFIXES | {".pdf"}
_KIND_LABELS = {"added": "新增", "removed": "删除", "modified": "修改", "moved": "移动"}
_CATEGORY_LABELS = {"text": "正文", "equation": "公式", "figure": "图", "table": "表格", "citation": "引用", "comment": "注释"}
_SEVERITY_LABELS = {"info": "提示", "warning": "警告", "error": "错误"}


class ReportPathError(OSError):
    """受管报告路径包含链接或越出输出目录。"""


@dataclass(frozen=True)
class ReportResult:
    directory: Path
    document: ReviewDocument

    @property
    def html(self) -> Path:
        return self.directory / "report.html"

    @property
    def diff_json(self) -> Path:
        return self.directory / "diff.json"


def _e(value: object) -> str:
    return escape(str(value), quote=True)


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


def _media_signature_matches(source: Path, suffix: str) -> bool:
    with source.open("rb") as stream:
        head = stream.read(1024)
    return {
        ".png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": head.startswith(b"\xff\xd8\xff"),
        ".jpeg": head.startswith(b"\xff\xd8\xff"),
        ".gif": head.startswith((b"GIF87a", b"GIF89a")),
        ".webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        ".pdf": head.lstrip().startswith(b"%PDF-"),
    }[suffix]


def _pdf_preview(source: Path, directory: Path, relative: Path, converter: str, timeout: float) -> Path | None:
    with tempfile.TemporaryDirectory(prefix="latex-review-pdf-") as temporary:
        output = Path(temporary) / "preview.png"
        def limit_output() -> None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024 * 1024, 12 * 1024 * 1024))
        try:
            result = subprocess.run((converter, "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1200",
                                     "-png", str(source), str(output.with_suffix(""))),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                    cwd=temporary, preexec_fn=limit_output)
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
                suffix = Path(relative or asset).suffix.lower()
                if suffix and suffix not in _LINKABLE_ASSET_SUFFIXES:
                    diagnostics.append(_asset_diagnostic("report_asset_unsupported",
                                                         f"图资源格式不允许复制或链接：{asset}", node, side))
                    cards.append(f'<div class="asset-card asset-missing" role="note">'
                                 f'<strong>图资源格式未支持：{_e(asset)}</strong><p>{_e(provenance)}</p></div>')
                    continue
                if relative is None:
                    diagnostics.append(_asset_diagnostic("report_asset_missing", f"图资源无法定位：{asset}", node, side))
                    cards.append(f'<div class="asset-card asset-missing" role="note"><strong>图缺失：{_e(asset)}</strong>'
                                 f'<p>{_e(provenance)}</p><p>尺寸：未知</p></div>')
                    continue
                try:
                    root = project.expanded.source.root.resolve()
                    source = (root / relative).resolve()
                    if not source.is_relative_to(root):
                        raise OSError("图资源逃出比较版本目录")
                    if not _media_signature_matches(source, suffix):
                        diagnostics.append(_asset_diagnostic("report_asset_unsupported",
                                                             f"图资源内容与格式不符，未复制或链接：{asset}", node, side))
                        cards.append(f'<div class="asset-card asset-missing" role="note">'
                                     f'<strong>图资源格式未支持：{_e(asset)}</strong><p>{_e(provenance)}</p></div>')
                        continue
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


def _change_cards(document: ReviewDocument, anchors: set[str], rendering: dict | None = None) -> str:
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
                           f'<small>{_e(detail.old_text or "∅")} → {_e(detail.new_text or "∅")}</small></li>')
        page_links = []
        if rendering:
            for side, label in (("old", "旧"), ("new", "新")):
                mapped = rendering.get("changes", {}).get(change.id, {}).get(side, {})
                page = mapped.get("page")
                if page:
                    page_links.append(f'<a href="#page-{side}-{page}">{label}侧第 {page} 页</a>')
                else:
                    page_links.append(f'{label}侧页码不确定：{_e(mapped.get("reason", "未知"))}')
        page_meta = f'<p class="card-pages">{" · ".join(page_links)}</p>' if page_links else ""
        cards.append(f'<article class="change-card" id="{_e(change.id)}" data-kind="{_e(change.kind)}" '
                     f'data-categories="{_e(" ".join(categories))}">'
                     f'<button type="button" class="change-jump" data-old="{_e(old_id)}" data-new="{_e(new_id)}" '
                     f'data-old-location="{_e(_location(change.source_old))}" '
                     f'data-new-location="{_e(_location(change.source_new))}">'
                     f'<span class="kind kind-{_e(change.kind)}">{_e(_KIND_LABELS.get(change.kind, change.kind))}</span> '
                     f'<strong>{_e(change.summary)}</strong></button>'
                     f'<p class="card-meta">{badges} · 匹配置信度 {change.matching_confidence:.2f}</p>'
                     f'<p class="card-source">旧：{_e(_location(change.source_old))}<br>新：{_e(_location(change.source_new))}</p>'
                     f'{page_meta}'
                     f'<ul class="detail-list">{"".join(details)}</ul></article>')
    return "".join(cards) or '<p class="empty-list">没有检测到主变更。</p>'


def _rendering_html(rendering: dict | None, statuses: tuple | None) -> str:
    if rendering is None or statuses is None:
        return ""
    items = []
    for status in statuses:
        label = "修改前" if status.side == "old" else "修改后"
        document_link = f'<a href="{_e(status.pdf)}">编译文档</a>' if status.pdf else "无编译文档"
        log_link = f'<a href="{_e(status.log)}">编译日志</a>' if status.log else "无编译日志"
        items.append(f'<li>{label}：{_e(status.status)}；{document_link}；'
                     f'{log_link}'
                     f'{"；" + _e(status.reason) if status.reason else ""}</li>')
    cards = []
    if rendering["comparable"]:
        pairs = rendering["pairs"]
        changed = sum(pair["kind"] == "changed" for pair in pairs)
        added = sum(pair["kind"] == "added" for pair in pairs)
        removed = sum(pair["kind"] == "removed" for pair in pairs)
        summary = f"页面视觉差异：变化 {changed} 对，新增 {added} 页，删除 {removed} 页。此计数与主变更数分开。"
    else:
        pairs = [{"old": index, "new": None, "kind": "unpaired", "image": None}
                 for index in range(1, len(rendering["old"]) + 1)]
        pairs += [{"old": None, "new": index, "kind": "unpaired", "image": None}
                  for index in range(1, len(rendering["new"]) + 1)]
        summary = "缺少双侧编译文档，页面视觉差异不可比较。"
    for pair in pairs:
        pictures = []
        for side, label in (("old", "修改前"), ("new", "修改后")):
            page = pair[side]
            if page is None:
                continue
            source = rendering[side][page - 1]
            change_links = []
            for change_id, mapped in rendering.get("changes", {}).items():
                if mapped.get(side, {}).get("page") == page:
                    change_links.append(f'<a href="#{_e(change_id)}">{_e(change_id)}</a>')
            pictures.append(f'<figure id="page-{side}-{page}"><figcaption>{label}第 {page} 页；'
                            f'{"、".join(change_links) if change_links else "无可确定页码的结构变更"}</figcaption>'
                            f'<img src="{_e(source)}" alt="{label}第 {page} 页预览" loading="lazy"></figure>')
        if pair.get("image"):
            pictures.append(f'<figure><figcaption>视觉差异图</figcaption><img src="{_e(pair["image"])}" '
                            'alt="页面像素差异" loading="lazy"></figure>')
        cards.append(f'<article class="page-pair"><h3>{_e(pair["kind"])}：'
                     f'{pair["old"] or "—"} → {pair["new"] or "—"}</h3>' + "".join(pictures) + "</article>")
    return ('<section class="rendered-pages" aria-label="编译页面与视觉差异"><h2>编译页面与视觉差异</h2>'
            '<p>页面差异表示排版像素变化，不证明内容语义变化。</p><ul>' + "".join(items) + '</ul>'
            f'<p>{_e(summary)}</p>' + "".join(cards) + '</section>')


def _report_html(document: ReviewDocument, preview_html: str, *, rendering: dict | None = None,
                 statuses: tuple | None = None) -> str:
    # 预览层拥有节点 HTML 和公式降级逻辑；报告仅将它们嵌入三栏容器。
    style = re.search(r"<style>(.*?)</style>", preview_html, re.S)
    main = re.search(r"<main>(.*?)</main>", preview_html, re.S)
    scripts = re.search(r"(<script>.*?</script>\s*<script async .*?</script>)", preview_html, re.S)
    if not (style and main and scripts):
        raise ValueError("预览页面缺少报告所需的节点或公式脚本")
    counts = document.summary
    category_counts = " · ".join(f"{_CATEGORY_LABELS.get(key, key)} {value}" for key, value in counts.category_hits.items())
    anchors = set(re.findall(r'\bid="([^"]+)"', main.group(1)))
    cards = _change_cards(document, anchors, rendering)
    diagnostics_html = _diagnostics_html(document)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaTeX 三栏审阅报告</title><style>{style.group(1)}
body{{background:#f3f4f6;color:#17212d}}header{{padding:.8rem 1.2rem}}header h1{{font-size:1.25rem}}
.summary{{margin:.3rem 0;font-weight:650}}.summary-extra{{margin:.2rem 0;color:#374151}}
main{{grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;padding:.7rem;align-items:start}}
.preview-side,.changes-side{{height:calc(100vh - 11rem);max-height:none;overflow:auto;background:white;border:1px solid #b8c0ca;border-radius:.35rem;padding:1rem}}
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
.rendered-pages{{padding:1rem;background:white;margin:1rem;border:1px solid #b8c0ca}}
.page-pair{{border-top:1px solid #b8c0ca;padding:.7rem 0;display:flex;gap:1rem;flex-wrap:wrap;align-items:start}}
.page-pair h3{{width:100%;margin:.2rem 0}}.page-pair figure{{margin:0;max-width:31%;min-width:230px}}
.page-pair img{{max-width:100%;height:auto;border:1px solid #b8c0ca}}
@media(max-width:1000px){{main{{grid-template-columns:1fr}}.preview-side,.changes-side{{height:auto;max-height:none}}}}
</style></head><body><header><h1>LaTeX 三栏审阅报告</h1>
<p class="notice">内容预览供审阅，不代表最终编译版式；来源位置可能为近似值。</p>
<p class="summary" id="report-summary" data-changes="{counts.changes}">主变更 {counts.changes} · 正文增加 {counts.added_words} 词 · 删除 {counts.removed_words} 词</p>
<p class="summary-extra">{_e(category_counts)}</p>
<p id="math-status" role="status">正在加载在线公式排版；原始 TeX 可直接阅读。</p></header>
<main>{main.group(1)}<section class="changes-side" aria-label="变更" id="changes-side"><h2>变更</h2>
<div class="filters"><label>操作 <select id="kind-filter"><option value="all">全部</option><option value="added">新增</option><option value="removed">删除</option><option value="modified">修改</option></select></label>
<label>类别 <select id="category-filter"><option value="all">全部</option><option value="text">正文</option><option value="equation">公式</option><option value="figure">图</option><option value="table">表格</option><option value="citation">引用</option><option value="comment">注释</option></select></label></div>
<p id="filter-count" role="status"></p><p id="jump-status" class="jump-status" role="status"></p>{diagnostics_html}{cards}</section></main>
{_rendering_html(rendering, statuses)}
<script>(function(){{
const cards=Array.from(document.querySelectorAll('.change-card'));
const kind=document.getElementById('kind-filter'),category=document.getElementById('category-filter');
const count=document.getElementById('filter-count'),status=document.getElementById('jump-status');
function filter(){{let shown=0;cards.forEach(function(card){{
 const visible=(kind.value==='all'||card.dataset.kind===kind.value)&&
 (category.value==='all'||card.dataset.categories.split(' ').includes(category.value));
 card.hidden=!visible;if(visible)shown++;
}});count.textContent='显示 '+shown+' / '+cards.length+' 项主变更';}}
kind.addEventListener('change',filter);category.addEventListener('change',filter);filter();
document.querySelectorAll('.preview-side').forEach(function(side){{
 const empty=document.createElement('p');empty.className='side-empty';empty.setAttribute('role','status');
 side.insertBefore(empty,side.querySelector('h2').nextSibling);
}});
document.getElementById('changes-side').addEventListener('click',function(event){{
 const button=event.target.closest('button[data-old][data-new]');if(!button)return;
 document.querySelectorAll('.preview-side .is-highlighted').forEach(function(node){{node.classList.remove('is-highlighted');}});
 const messages=[];
 [['old','修改前'],['new','修改后']].forEach(function(pair){{
   const side=pair[0],label=pair[1],id=button.dataset[side];
   const panel=document.querySelector('.preview-side[data-side="'+side+'"]');
   const empty=panel.querySelector('.side-empty');
   const node=id?document.getElementById(side+'-'+id):null;
   if(node){{empty.classList.remove('is-visible');node.classList.add('is-highlighted');node.scrollIntoView({{block:'center',behavior:'auto'}});}}
   else{{empty.textContent=label+'侧无对应节点';empty.classList.add('is-visible');}}
   messages.push(label+'：'+button.dataset[side+'Location']);
 }});
 status.textContent=messages.join('；');
}});
}})();</script>
{scripts.group(1)}
</body></html>'''


def write_report(old: ParsedProject, new: ParsedProject, comparison: ComparisonResult,
                 output_dir: str | Path, *, pdf_converter: str | None = None,
                 conversion_timeout: float = 8.0, extra_diagnostics: tuple[Diagnostic, ...] = (),
                 rendering: dict | None = None, statuses: tuple | None = None) -> ReportResult:
    """在来源快照有效期内写入 report.html、diff.json、diagnostics.json 和双侧资源。"""
    if conversion_timeout <= 0:
        raise ValueError("转换超时必须为正数")
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
    diagnostics = tuple(dict.fromkeys((*comparison.document.diagnostics, *preview.diagnostics,
                                       *asset_diagnostics, *extra_diagnostics)))
    document = replace(comparison.document, diagnostics=diagnostics)
    diff_json = dumps(document)
    html = _report_html(document, preview.html, rendering=rendering, statuses=statuses)
    _write_managed(directory, Path("diff.json"), content=diff_json.encode("utf-8"))
    _write_managed(directory, Path("diagnostics.json"),
                   content=dumps(DiagnosticsDocument(diagnostics)).encode("utf-8"))
    _write_managed(directory, Path("report.html"), content=html.encode("utf-8"))
    return ReportResult(directory, document)


def write_source_fallback(old: ExpandedProject, new: ExpandedProject, output_dir: str | Path, *,
                          parse_failures: list[tuple[str, str]] = (),
                          parsed: tuple[ParsedProject | None, ParsedProject | None] = (None, None),
                          extra_diagnostics: tuple[Diagnostic, ...] = (), rendering: dict | None = None,
                          statuses: tuple | None = None) -> ReportResult:
    """结构解析整体失败时，保留两侧逐文件原始源码及逐行差异。"""
    from difflib import SequenceMatcher

    directory = Path(output_dir)
    if directory.is_symlink():
        raise ReportPathError("报告输出目录不可为符号链接")
    directory.mkdir(parents=True, exist_ok=True)
    unknown = SourceLocation(None, None, None, confidence=0, uncertainty_reason="整体结构解析失败，无法定位到单个原文件")
    def raw_files(project: ExpandedProject) -> str:
        return "".join(f"===== {name} =====\n{content}\n"
                       for name, content in sorted(project.source_map.files.items()))
    old_raw, new_raw = raw_files(old), raw_files(new)
    old_node = ReviewNode("source-old", "source_fallback", old_raw, None, (), (), unknown)
    new_node = ReviewNode("source-new", "source_fallback", new_raw, None, (), (), unknown)
    changes = ((PrimaryChange("source-change-1", "modified", "source_fallback", old_node.id, new_node.id,
                              unknown, unknown, 0, ("text",), (), "结构解析失败；按展开源码对比"),)
               if old_raw != new_raw else ())
    diagnostics = [*(parsed[0].diagnostics if parsed[0] else ()),
                   *(parsed[1].diagnostics if parsed[1] else ())]
    for side, message in parse_failures:
        diagnostics.append(Diagnostic("structure_parse_failed", "warning", f"{side} 结构解析失败：{message}",
                                      source_old=unknown if side == "old" else None,
                                      source_new=unknown if side == "new" else None))
    for project in (old, new):
        if parsed[0 if project.source.side == "old" else 1] is not None:
            continue
        for issue in project.diagnostics:
            content = project.source_map.files.get(issue.file, "")
            line = content[:issue.start].count("\n") + 1
            location = SourceLocation(issue.file, line, line, confidence=0.3, uncertainty_reason="来源展开不确定")
            diagnostics.append(Diagnostic(issue.code, "warning", issue.message,
                                          source_old=location if issue.side == "old" else None,
                                          source_new=location if issue.side == "new" else None))
    diagnostics.append(Diagnostic("source_fallback", "warning", "结构解析整体失败；已生成源码对比报告，原始内容完整保留"))
    diagnostics.extend(extra_diagnostics)
    document = ReviewDocument(old.source.entry, old.source.identity, new.source.identity,
                              (old_node,), (new_node,), changes, tuple(dict.fromkeys(diagnostics)),
                              build_summary(changes))
    old_lines, new_lines = old_raw.splitlines(keepends=True), new_raw.splitlines(keepends=True)
    before, after = [], []
    for op, i, j, k, l in SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        before.extend(f'<span class="{("removed" if op != "equal" else "same")}">{_e(line)}</span>'
                      for line in old_lines[i:j])
        after.extend(f'<span class="{("added" if op != "equal" else "same")}">{_e(line)}</span>'
                     for line in new_lines[k:l])
    diagnostics_html = _diagnostics_html(document)
    html = ("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>源码对比报告</title>
<style>body{font:16px/1.5 system-ui;margin:1rem;color:#17212d}main{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
section{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #adb7c4;padding:1rem}
.removed{background:#fce2df}.added{background:#d8f0df}.report-diagnostics{border:1px solid #a9b4c1;padding:.5rem}
@media(max-width:800px){main{grid-template-columns:1fr}}</style></head><body><h1>源码对比报告</h1>
<p>结构解析整体失败。下方按文件保留两侧原始源码，着色行表示差异；不能作为结构化差异使用。</p>"""
            + diagnostics_html + '<main><section><h2>修改前</h2><pre>' + "".join(before)
            + '</pre></section><section><h2>修改后</h2><pre>' + "".join(after)
            + '</pre></section></main>' + _rendering_html(rendering, statuses) + '</body></html>')
    _write_managed(directory, Path("diff.json"), content=dumps(document).encode("utf-8"))
    _write_managed(directory, Path("diagnostics.json"),
                   content=dumps(DiagnosticsDocument(document.diagnostics)).encode("utf-8"))
    _write_managed(directory, Path("report.html"), content=html.encode("utf-8"))
    return ReportResult(directory, document)


def write_failure_diagnostics(output_dir: Path, diagnostic: Diagnostic) -> None:
    """无可信报告时仅留下本轮诊断，不保留差异或页面入口。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_managed(output_dir, Path("diagnostics.json"),
                   content=dumps(DiagnosticsDocument((diagnostic,))).encode("utf-8"))
