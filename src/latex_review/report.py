"""把同一份审阅文档写为可移动的论文审阅报告目录。"""

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
from .preview import _INLINE_SAFE, _UNSAFE_MATH, _inline_html, render_preview
from .rules import check_rules
from .structure import ParsedNode, ParsedProject
from .sources import ExpandedProject
from .table_model import display_tables
from .text_diff import split_sentences
from .latex_commands import MATH_COMMANDS


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_LINKABLE_ASSET_SUFFIXES = _IMAGE_SUFFIXES | {".pdf"}
_KIND_LABELS = {"added": "新增", "removed": "删除", "modified": "修改", "moved": "移动"}
_CATEGORY_LABELS = {"text": "正文", "equation": "公式", "figure": "图", "table": "表格", "citation": "引用", "comment": "注释", "move": "移动"}
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


def _sentence_card_html(raw: str | None, project: ParsedProject, side: str,
                        node_id: str | None, numbers: tuple[int, ...]) -> str:
    if raw is None:
        return "该侧无内容"
    if (re.search(r"<[^>]+>|(?:javascript|data|vbscript)\s*:", raw, re.I) or
            _UNSAFE_MATH.search(raw) or
            any(match.group(1) not in _INLINE_SAFE and match.group(1) not in {"paragraph", "subparagraph"}
                and match.group(1) not in project.macros
                for match in re.finditer(r"\\([A-Za-z@]+)", raw))):
        return "此处暂无法预览"
    parent = project.by_id().get(node_id) if node_id else None
    if parent is None or not numbers:
        return "此处暂无法预览"
    children = [project.by_id()[child] for child in parent.review.child_ids]
    sentences = split_sentences(parent.review.raw_latex)
    rendered_parts = []
    for number in numbers:
        if number > len(sentences):
            return "此处暂无法预览"
        sentence = sentences[number - 1]
        contained = [child for child in children if parent.expanded_start + sentence.start <= child.expanded_start
                     and child.expanded_end <= parent.expanded_start + sentence.end]
        rendered_parts.append(_inline_html(sentence.text, project, side, contained, source=parent.review.source,
                                           base_start=parent.expanded_start + sentence.start))
    rendered = " ".join(rendered_parts)
    # 明细本身是按钮，行内引用在这里保留文字但不嵌套交互链接。
    return re.sub(r"<a\b[^>]*>", "<span>", rendered).replace("</a>", "</span>").strip()


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
                relative.parts[0] not in {"assets", "previews", "pages"}):
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
    return re.sub(r'(src|href)="((?:assets|previews|pages)/[^\"]+)"', replace_url, html)


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


def write_cache_metadata(directory: Path, metadata: dict) -> None:
    """缓存运行信息独立于语义差异写入，避免冷热运行改变 diff.json。"""
    _write_managed(directory, Path("cache-meta.json"),
                   content=(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


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


def _graphic_page(raw: str, asset: str, occurrence: int = 0) -> tuple[int | None, str | None]:
    seen = 0
    for match in re.finditer(r"\\includegraphics\*?\s*(?:\[([^\]]*)\])?\s*\{([^{}]+)\}", raw):
        if match.group(2).strip() != asset:
            continue
        if seen < occurrence:
            seen += 1
            continue
        options = dict(part.strip().split("=", 1) if "=" in part else (part.strip(), "")
                       for part in (match.group(1) or "").split(",") if part.strip())
        # 影响图像内容的参数不能悄悄忽略，以免审阅者看到错误的图。
        unsupported = set(options) & {"trim", "viewport", "clip", "angle", "origin", "pagebox"}
        if unsupported:
            return None, f"图像参数尚无法可靠转换：{', '.join(sorted(unsupported))}"
        page = options.get("page", "1").strip()
        if not page.isdigit() or not 1 <= int(page) <= 10000:
            return None, f"无效的 PDF 页码：{page}"
        return int(page), None
    return (None, "图像调用与资源数量不符") if occurrence else (1, None)


def _pdf_preview(source: Path, directory: Path, relative: Path,
                 converters: tuple[tuple[str, str], ...], timeout: float, page: int) -> tuple[Path | None, str]:
    if not converters:
        return None, "缺少可用的 PDF 转换工具"
    reasons = []
    with tempfile.TemporaryDirectory(prefix="latex-review-pdf-") as temporary:
        output = Path(temporary) / "preview.png"
        def limit_output() -> None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024 * 1024, 12 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (max(1, int(timeout) + 1), max(1, int(timeout) + 1)))
        for kind, converter in converters:
            output.unlink(missing_ok=True)
            if kind == "pdftoppm":
                command = (converter, "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1200",
                           "-png", str(source), str(output.with_suffix("")))
            elif kind == "gs":
                command = (converter, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                           f"-dFirstPage={page}", f"-dLastPage={page}", "-r120",
                           f"-sOutputFile={output}", str(source))
            elif page == 1:
                command = (converter, "-s", "format", "png", str(source), "--out", str(output))
            else:
                reasons.append("sips 不支持指定 PDF 页码")
                continue
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, timeout=timeout, check=False,
                                        cwd=temporary, preexec_fn=limit_output)
            except subprocess.TimeoutExpired:
                reasons.append(f"{kind} 转换超时")
                continue
            except OSError as exc:
                reasons.append(f"{kind} 无法启动：{exc}")
                continue
            if (result.returncode == 0 and not output.is_symlink() and output.is_file()
                    and 0 < output.stat().st_size <= 12 * 1024 * 1024):
                return _write_managed(directory, relative, source=output), ""
            stderr = result.stderr[:500].decode("utf-8", "replace").strip()
            reasons.append(f"{kind} 退出状态 {result.returncode}" + (f"：{stderr}" if stderr else ""))
    return None, "；".join(reasons)


def _figure_assets(old: ParsedProject, new: ParsedProject, directory: Path,
                   converters: tuple[tuple[str, str], ...], timeout: float) -> tuple[dict[tuple[str, str], str], list[Diagnostic]]:
    html: dict[tuple[str, str], str] = {}
    diagnostics: list[Diagnostic] = []
    for side, project in (("old", old), ("new", new)):
        for node in project.nodes:
            if node.review.type != "figure" or not node.assets:
                continue
            cards = []
            occurrences: dict[str, int] = {}
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
                    page, option_problem = _graphic_page(node.review.raw_latex, asset,
                                                          occurrences.get(asset, 0))
                    occurrences[asset] = occurrences.get(asset, 0) + 1
                    preview_relative = Path("previews") / side / f"{copied.relative_to(directory / 'assets' / side)}.page-{page or 1}.png"
                    preview, reason = (_pdf_preview(copied, directory, preview_relative, converters, timeout, page)
                                       if page is not None else (None, option_problem or "图像参数无法转换"))
                    if preview is not None:
                        preview_url = "/".join(quote(part) for part in preview.relative_to(directory).parts)
                        picture = f'<img src="{_e(preview_url)}" alt="PDF 图 {_e(asset)} 的第 {page} 页预览" loading="lazy">'
                    else:
                        diagnostics.append(_asset_diagnostic("report_pdf_preview_unavailable",
                                                            f"PDF 图预览不可用：{asset}；{reason}", node, side))
                        picture = '<p class="asset-fallback">此处暂无法预览</p>'
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


def _change_cards(document: ReviewDocument, anchors: set[str], old: ParsedProject,
                  new: ParsedProject, editor_template: str | None,
                  rendering: dict | None = None) -> str:
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
            old_sentence_ids = " ".join(f"{old_id}-sentence-{number}" for number in detail.old_sentences)
            new_sentence_ids = " ".join(f"{new_id}-sentence-{number}" for number in detail.new_sentences)
            if detail.old_sentences or detail.new_sentences:
                if not detail.old_sentences:
                    detail_old_id = ""
                    old_source = None
                if not detail.new_sentences:
                    detail_new_id = ""
                    new_source = None
            sentence_text = (f'<span class="sentence-pair">'
                             f'<span class="sentence-before">修改前：{_sentence_card_html(detail.old_text, old, "old", change.old_node_id, detail.old_sentences)}</span>'
                             f'<span class="sentence-after">修改后：{_sentence_card_html(detail.new_text, new, "new", change.new_node_id, detail.new_sentences)}</span>'
                             f'</span>') if detail.old_sentences or detail.new_sentences else ""
            old_location = "所属段落；" + _location(old_source) if detail.old_sentences else _location(old_source)
            new_location = "所属段落；" + _location(new_source) if detail.new_sentences else _location(new_source)
            details.append(f'<li><button type="button" class="detail-jump" data-old="{_e(detail_old_id)}" '
                           f'data-new="{_e(detail_new_id)}" data-old-location="{_e(old_location)}" '
                           f'data-new-location="{_e(new_location)}" '
                           f'data-old-sentences="{_e(old_sentence_ids)}" data-new-sentences="{_e(new_sentence_ids)}" '
                           f'data-old-row="{detail.row_old or ""}" data-old-column="{detail.column_old or ""}" '
                           f'data-new-row="{detail.row_new or ""}" data-new-column="{detail.column_new or ""}">'
                           f'{_e(_CATEGORY_LABELS.get(detail.category, detail.category))} · '
                           f'{_e(_KIND_LABELS.get(detail.kind, detail.kind))}：'
                           f'{_e(detail.summary if detail.category in {"citation", "figure", "text"} else "内容变化")}'
                           f'{sentence_text}</button></li>')
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
                     f'<p class="card-meta">{badges}</p>'
                     f'{page_meta}'
                     f'<ul class="detail-list">{"".join(details)}</ul></article>')
    return "".join(cards) or '<p class="empty-list">没有检测到主变更。</p>'


def _rendering_html(rendering: dict | None, statuses: tuple | None) -> str:
    if rendering is None or statuses is None:
        return ""
    items = []
    for status in statuses:
        label = "修改前" if status.side == "old" else "修改后"
        items.append(f'<li>{label}：{"页面预览可用" if status.pdf else "此处暂无法预览"}</li>')
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
            f'<p>{_e(summary)}</p>' + "".join(cards) + '''</section>
<script>(function(){
 const pages=document.querySelector('.rendered-pages');
 pages.addEventListener('click',function(event){
  const link=event.target.closest('a[href^="#"]');
  if(!link||!pages.contains(link))return;
  const card=document.getElementById(link.getAttribute('href').slice(1));
  if(!card||!card.classList.contains('change-card'))return;
  event.preventDefault();
  if(card.hidden){
   const kind=document.getElementById('kind-filter');
   const category=document.getElementById('category-filter');
   if(kind.value!=='all'&&kind.value!==card.dataset.kind)kind.value='all';
   if(category.value!=='all'&&!card.dataset.categories.split(' ').includes(category.value))category.value='all';
   kind.dispatchEvent(new Event('change',{bubbles:true}));
  }
  if(card.hidden)return;
  card.scrollIntoView({block:'center',behavior:'auto'});
  const button=card.querySelector('.change-jump');
  button.focus({preventScroll:true});
  button.click();
 });
})();</script>''')


def _report_html(document: ReviewDocument, preview_html: str, old: ParsedProject,
                 new: ParsedProject, editor_template: str | None,
                 pairs: tuple[tuple[str, str], ...], *, rendering: dict | None = None,
                 statuses: tuple | None = None, translation_units: list[dict] | None = None) -> str:
    # 预览层拥有节点 HTML 和公式降级逻辑；报告提供以新稿为主体的阅读容器。
    style = re.search(r"<style>(.*?)</style>", preview_html, re.S)
    main = re.search(r"<main>(.*?)</main>", preview_html, re.S)
    scripts = re.search(r"(<script>.*?</script>\s*<script async .*?</script>)", preview_html, re.S)
    if not (style and main and scripts):
        raise ValueError("预览页面缺少报告所需的节点或公式脚本")
    counts = document.summary
    category_counts = " · ".join(f"{_CATEGORY_LABELS.get(key, key)} {value}" for key, value in counts.category_hits.items())
    anchors = set(re.findall(r'\bid="([^"]+)"', main.group(1)))
    cards = _change_cards(document, anchors, old, new, editor_template, rendering)
    interaction = files("latex_review").joinpath("report_interaction.js").read_text(encoding="utf-8")
    pairs_json = json.dumps(pairs, ensure_ascii=False).replace("<", "\\u003c")
    report_style = files("latex_review").joinpath("report.css").read_text(encoding="utf-8")
    from .translation import build_units
    translation_units = json.dumps([{k: v for k, v in unit.items() if k != "sentences"}
                                   for unit in (translation_units if translation_units is not None else build_units(document, old, new))],
                                   ensure_ascii=False).replace("<", "\\u003c")
    translation_script = files("latex_review").joinpath("report_translation.js").read_text(encoding="utf-8")
    severity_labels = {"error": "错误", "warning": "提醒", "info": "说明"}
    diagnostics_html = "".join(
        f'<li><strong>{_e(severity_labels.get(item.severity, item.severity))}</strong> · {_e(item.message)}</li>'
        for item in document.diagnostics) or '<li>没有额外诊断。</li>'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>论文修改审阅 · {_e(document.entry)}</title><style>{style.group(1)}
{report_style}
</style></head><body class="reading-view">
<header class="report-header">
<div class="report-identity"><span class="report-eyebrow">论文修改审阅</span><h1>{_e(document.entry)}</h1></div>
<p class="summary" id="report-summary" data-changes="{counts.changes}"><strong>{counts.changes}</strong> 项主变更 <span>正文 +{counts.added_words} / −{counts.removed_words} 词</span></p>
<details class="report-help"><summary>报告说明</summary><div>
<p>内容预览供审阅，不代表最终编译版式；来源位置可能为近似值。</p>
<p>同键文献字段变化单独计入引用主变更，不累计为正文变化。分类命中可重叠。</p>
<p class="summary-extra">{_e(category_counts)}</p>
<p>右侧页边卡片显示变更摘要。点击正文划线或卡片，展开修改前后对比；窄屏卡片显示在对应段落下方。按 Alt+↑ / Alt+↓ 可跳到上一项 / 下一项；输入时快捷键不生效。公式排版可能需要联网。</p>
<p id="math-status" role="status">正在加载在线公式排版。</p>
</div></details></header>
<nav class="report-controls" aria-label="审阅工具">
<button type="button" id="toggle-changes" aria-expanded="false" aria-controls="changes-side">变更目录 <span>{counts.changes}</span></button>
<div class="reading-options"><label>阅读范围 <select id="reading-mode"><option value="full">完整文档</option><option value="context">变更上下文</option></select></label>
<label>对照方式 <select id="layout-mode"><option value="reading">新稿阅读</option><option value="compare">双稿对照</option></select></label>
<label class="sync-option"><input type="checkbox" id="sync-scroll"> 同步滚动</label></div>
<div class="translation-controls"><label>译文 <select id="translation-mode"><option value="original">英文原文</option><option value="bilingual">英中对照</option></select></label>
<button type="button" id="translate-all">翻译全部变更</button><button type="button" id="translation-cancel" disabled>停止翻译</button>
<button type="button" id="translation-export">导出含译文报告</button><span id="translation-status" role="status">点击后翻译变更段落、标题和图注；表格暂不翻译。</span></div>
<div class="step-controls"><span id="current-change" role="status">尚未选择变更</span><button type="button" id="previous-change" aria-label="上一变更">↑ 上一处</button><button type="button" id="next-change" aria-label="下一变更">下一处 ↓</button></div>
</nav>
<main>{main.group(1)}<section class="changes-side" aria-label="变更" id="changes-side"><div class="directory-heading"><h2>变更目录</h2><button type="button" id="close-changes" aria-label="收起变更目录">收起</button></div>
<div class="filters"><label>操作 <select id="kind-filter"><option value="all">全部</option><option value="added">新增</option><option value="removed">删除</option><option value="modified">修改</option><option value="moved">移动</option></select></label>
<label>类别 <select id="category-filter"><option value="all">全部</option><option value="text">正文</option><option value="equation">公式</option><option value="figure">图</option><option value="table">表格</option><option value="citation">引用</option><option value="comment">注释</option><option value="move">移动</option></select></label></div>
<p id="filter-count" role="status"></p><p id="jump-status" class="jump-status" role="status"></p>{cards}</section></main>
<details class="report-supplement"><summary>报告诊断 <span>{len(document.diagnostics)} 条诊断</span></summary>
<ul>{diagnostics_html}</ul>
</details>
{('<details class="report-supplement" id="compiled-pages"><summary>编译页面与视觉差异</summary>' + _rendering_html(rendering, statuses) + '</details>') if rendering is not None and statuses is not None else ''}
<script>window.reviewNodePairs={pairs_json};</script><script>{interaction}</script>
<script type="application/json" id="translation-units">{translation_units}</script>
<script type="application/json" id="translation-results">{{}}</script>
<script>{translation_script}</script>
{scripts.group(1)}
</body></html>'''


def write_report(old: ParsedProject, new: ParsedProject, comparison: ComparisonResult,
                 output_dir: str | Path, *, pdf_converter: str | None = None,
                 conversion_timeout: float = 8.0, editor_url_template: str | None = None,
                 single_file: bool = False, extra_diagnostics: tuple[Diagnostic, ...] = (),
                 rendering: dict | None = None, statuses: tuple | None = None,
                 fragment_timeout: float = 6.0) -> ReportResult:
    """在来源快照有效期内写入 report.html、diff.json、diagnostics.json 和双侧资源。"""
    if conversion_timeout <= 0 or fragment_timeout <= 0:
        raise ValueError("转换超时必须为正数")
    _validate_editor_template(editor_url_template)
    requested_directory = Path(output_dir)
    if requested_directory.is_symlink():
        raise ReportPathError("报告输出目录不可为符号链接")
    directory = requested_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if pdf_converter is None:
        from .compilation import _program, _safe_path
        safe_path, forbidden = _safe_path((old.expanded.source, new.expanded.source), directory)
        converters = tuple((name, found) for name in ("pdftoppm", "gs", "sips")
                           if (found := _program(name, safe_path, forbidden)))
    else:
        converters = (("pdftoppm", pdf_converter),) if pdf_converter else ()
    figure_html, asset_diagnostics = _figure_assets(old, new, directory, converters, conversion_timeout)
    from .fragment_render import render_fragment
    fragment_diagnostics = []
    known_math = MATH_COMMANDS | {"begin", "end", "label", "tag", "notag", "nonumber", "text", "operatorname"}
    for side, project in (("old", old), ("new", new)):
        attempted = 0
        for node in project.nodes:
            raw = node.review.raw_latex
            if node.review.type == "table":
                needed = bool(re.search(r"\\begin\{(?:tabular\*?|longtable)\}", raw)) and not display_tables(raw)
            elif node.review.type == "equation":
                needed = not _UNSAFE_MATH.search(raw) and any(
                    match.group(1) not in known_math for match in re.finditer(r"\\([A-Za-z@]+)", raw))
            else:
                continue
            if not needed:
                continue
            if attempted >= 8:
                reason, image = "片段编译数量达到上限", None
            else:
                attempted += 1
                image, reason = render_fragment(raw, node.review.type, project.expanded.source,
                                                directory, converters, timeout=fragment_timeout)
            if image is not None:
                figure_html[(side, node.review.id)] = "/".join(quote(part) for part in image.relative_to(directory).parts)
            else:
                fragment_diagnostics.append(_asset_diagnostic("preview_fragment_unavailable",
                                                              f"{node.review.type} 编译预览不可用：{reason}", node, side))
    parsed_ids = {"old": {node.review.id for node in old.nodes}, "new": {node.review.id for node in new.nodes}}
    extra_nodes = {
        "old": tuple(node for node in comparison.document.nodes_old if node.id not in parsed_ids["old"]),
        "new": tuple(node for node in comparison.document.nodes_new if node.id not in parsed_ids["new"]),
    }
    sentence_highlights: dict[tuple[str, str], set[int]] = {}
    for change in comparison.document.changes:
        for detail in change.details:
            for side, node_id, numbers in (("old", change.old_node_id, detail.old_sentences),
                                           ("new", change.new_node_id, detail.new_sentences)):
                if node_id and numbers:
                    sentence_highlights.setdefault((side, node_id), set()).update(numbers)
    preview = render_preview(old, new, figure_assets=figure_html, extra_nodes=extra_nodes,
                             sentence_highlights={key: frozenset(value) for key, value in sentence_highlights.items()})
    preview_diagnostics = (item for item in preview.diagnostics
                           if item.code not in {"bibliography_key_unresolved", "unresolved_reference"})
    diagnostics = tuple(dict.fromkeys((*comparison.document.diagnostics, *preview_diagnostics,
                                       *check_rules(old, new), *asset_diagnostics,
                                       *fragment_diagnostics, *extra_diagnostics)))
    document = replace(comparison.document, diagnostics=diagnostics)
    diff_json = dumps(document)
    pairs = tuple((pair.old_id, pair.new_id) for pair in comparison.mapping.pairs)
    from .translation import build_units
    units = build_units(document, old, new)
    html = _report_html(document, preview.html, old, new, editor_url_template, pairs,
                        rendering=rendering, statuses=statuses, translation_units=units)
    _write_managed(directory, Path("diff.json"), content=diff_json.encode("utf-8"))
    _write_managed(directory, Path("diagnostics.json"),
                   content=dumps(DiagnosticsDocument(diagnostics)).encode("utf-8"))
    _write_managed(directory, Path("report.html"), content=html.encode("utf-8"))
    _write_managed(directory, Path("translation-units.json"),
                   content=json.dumps(units, ensure_ascii=False).encode("utf-8"))
    single = (_write_managed(directory, Path("report-single.html"),
                             content=_single_file_html(html, directory).encode("utf-8")) if single_file else None)
    return ReportResult(directory, document, single)


def write_source_fallback(old: ExpandedProject, new: ExpandedProject, output_dir: str | Path, *,
                          parse_failures: list[tuple[str, str]] = (),
                          parsed: tuple[ParsedProject | None, ParsedProject | None] = (None, None),
                          extra_diagnostics: tuple[Diagnostic, ...] = (), rendering: dict | None = None,
                          statuses: tuple | None = None) -> ReportResult:
    """结构解析整体失败时，保留两侧逐文件原始源码及逐行差异。"""

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
                              unknown, unknown, 0, ("text",), (), "结构解析失败；阅读界面使用占位"),)
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
    diagnostics.append(Diagnostic("source_fallback", "warning", "结构解析整体失败；机器数据保留来源，阅读界面使用占位"))
    diagnostics.extend(extra_diagnostics)
    document = ReviewDocument(old.source.entry, old.source.identity, new.source.identity,
                              (old_node,), (new_node,), changes, tuple(dict.fromkeys(diagnostics)),
                              build_summary(changes))
    html = ("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>内容预览</title>
<style>body{font:16px/1.5 system-ui;margin:1rem;color:#17212d}main{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
section{min-width:0;border:1px solid #adb7c4;padding:1rem}.old{background:#fce2df}.new{background:#d8f0df}
@media(max-width:800px){main{grid-template-columns:1fr}}</style></head><body><h1>内容预览</h1>
<main><section class="old"><h2>修改前</h2><p>此处暂无法预览</p></section>
<section class="new"><h2>修改后</h2><p>此处暂无法预览</p></section></main>"""
            + _rendering_html(rendering, statuses) + '</body></html>')
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
