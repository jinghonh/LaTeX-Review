"""把同一份审阅文档写为可移动的三栏报告目录。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote

from .comparison import ComparisonResult
from .contract import Diagnostic, DiagnosticsDocument, ReviewDocument, SourceLocation, dumps
from .preview import render_preview
from .structure import ParsedNode, ParsedProject


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_KIND_LABELS = {"added": "新增", "removed": "删除", "modified": "修改", "moved": "移动"}
_CATEGORY_LABELS = {"text": "正文", "equation": "公式", "figure": "图", "table": "表格", "citation": "引用", "comment": "注释"}


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
    origins = {origin.origin.file for origin in node.origins}
    if node.review.source.file:
        origins.add(node.review.source.file)
    candidates = [dep for dep in project.expanded.dependencies if dep.kind == "graphic"
                  and dep.referenced_from in origins
                  and (dep.file == asset or dep.file.endswith("/" + asset)
                       or (Path(asset).suffix == "" and Path(dep.file).stem == Path(asset).name))]
    if len({dep.file for dep in candidates}) == 1:
        return candidates[0].file
    return None


def _copy_asset(project: ParsedProject, relative: str, directory: Path, side: str) -> tuple[Path, str]:
    root = project.expanded.source.root.resolve()
    source = (root / relative).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise OSError("资源不在比较版本目录内或已缺失")
    destination = directory / "assets" / side / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    url = "/".join(quote(part) for part in destination.relative_to(directory).parts)
    return destination, url


def _pdf_preview(source: Path, output: Path, converter: str, timeout: float) -> Path | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    try:
        result = subprocess.run((converter, "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1200",
                                 "-png", str(source), str(output.with_suffix(""))),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        if result.returncode == 0 and output.is_file() and 0 < output.stat().st_size <= 12 * 1024 * 1024:
            return output
    except (OSError, subprocess.TimeoutExpired):
        pass
    output.unlink(missing_ok=True)
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
                    preview_path = directory / "previews" / side / f"{relative}.png"
                    preview = _pdf_preview(copied, preview_path, converter, timeout) if converter else None
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


def _change_cards(document: ReviewDocument) -> str:
    cards = []
    for change in document.changes:
        categories = sorted(set(change.categories) | {detail.category for detail in change.details})
        old_id, new_id = change.old_node_id or "", change.new_node_id or ""
        badges = "".join(f'<span class="badge">{_e(_CATEGORY_LABELS.get(category, category))}</span>' for category in categories)
        details = []
        for detail in change.details:
            old_source = detail.source_old or change.source_old
            new_source = detail.source_new or change.source_new
            details.append(f'<li><button type="button" class="detail-jump" data-old="{_e(old_id)}" '
                           f'data-new="{_e(new_id)}" data-old-location="{_e(_location(old_source))}" '
                           f'data-new-location="{_e(_location(new_source))}">'
                           f'{_e(_CATEGORY_LABELS.get(detail.category, detail.category))} · '
                           f'{_e(_KIND_LABELS.get(detail.kind, detail.kind))}：{_e(detail.summary)}</button>'
                           f'<small>{_e(detail.old_text or "∅")} → {_e(detail.new_text or "∅")}</small></li>')
        cards.append(f'<article class="change-card" id="{_e(change.id)}" data-kind="{_e(change.kind)}" '
                     f'data-categories="{_e(" ".join(categories))}">'
                     f'<button type="button" class="change-jump" data-old="{_e(old_id)}" data-new="{_e(new_id)}" '
                     f'data-old-location="{_e(_location(change.source_old))}" '
                     f'data-new-location="{_e(_location(change.source_new))}">'
                     f'<span class="kind kind-{_e(change.kind)}">{_e(_KIND_LABELS.get(change.kind, change.kind))}</span> '
                     f'<strong>{_e(change.summary)}</strong></button>'
                     f'<p class="card-meta">{badges} · 匹配置信度 {change.matching_confidence:.2f}</p>'
                     f'<p class="card-source">旧：{_e(_location(change.source_old))}<br>新：{_e(_location(change.source_new))}</p>'
                     f'<ul class="detail-list">{"".join(details)}</ul></article>')
    return "".join(cards) or '<p class="empty-list">没有检测到主变更。</p>'


def _report_html(document: ReviewDocument, preview_html: str) -> str:
    # 预览层拥有节点 HTML 和公式降级逻辑；报告仅将它们嵌入三栏容器。
    style = re.search(r"<style>(.*?)</style>", preview_html, re.S)
    main = re.search(r"<main>(.*?)</main>", preview_html, re.S)
    scripts = re.search(r"(<script>.*?</script>\s*<script async .*?</script>)", preview_html, re.S)
    if not (style and main and scripts):
        raise ValueError("预览页面缺少报告所需的节点或公式脚本")
    counts = document.summary
    category_counts = " · ".join(f"{_CATEGORY_LABELS.get(key, key)} {value}" for key, value in counts.category_hits.items())
    cards = _change_cards(document)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaTeX 三栏审阅报告</title><style>{style.group(1)}
body{{background:#f3f4f6;color:#17212d}}header{{padding:.8rem 1.2rem}}header h1{{font-size:1.25rem}}
.summary{{margin:.3rem 0;font-weight:650}}.summary-extra{{margin:.2rem 0;color:#374151}}
main{{grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem;padding:.7rem;align-items:start}}
.preview-side,.changes-side{{height:calc(100vh - 11rem);max-height:none;overflow:auto;background:white;border:1px solid #b8c0ca;border-radius:.35rem;padding:1rem}}
.changes-side h2{{margin:0 0 .8rem}}.review-node.is-highlighted{{outline:3px solid #9a5700;outline-offset:3px;background:#fff5d9}}
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
@media(max-width:1000px){{main{{grid-template-columns:1fr}}.preview-side,.changes-side{{height:auto;max-height:none}}}}
</style></head><body><header><h1>LaTeX 三栏审阅报告</h1>
<p class="notice">内容预览供审阅，不代表最终编译版式；来源位置可能为近似值。</p>
<p class="summary" id="report-summary" data-changes="{counts.changes}">主变更 {counts.changes} · 正文增加 {counts.added_words} 词 · 删除 {counts.removed_words} 词</p>
<p class="summary-extra">{_e(category_counts)}</p>
<p id="math-status" role="status">正在加载在线公式排版；原始 TeX 可直接阅读。</p></header>
<main>{main.group(1)}<section class="changes-side" aria-label="变更" id="changes-side"><h2>变更</h2>
<div class="filters"><label>操作 <select id="kind-filter"><option value="all">全部</option><option value="added">新增</option><option value="removed">删除</option><option value="modified">修改</option></select></label>
<label>类别 <select id="category-filter"><option value="all">全部</option><option value="text">正文</option><option value="equation">公式</option><option value="figure">图</option><option value="table">表格</option><option value="citation">引用</option><option value="comment">注释</option></select></label></div>
<p id="filter-count" role="status"></p><p id="jump-status" class="jump-status" role="status"></p>{cards}</section></main>
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
 document.querySelectorAll('.review-node.is-highlighted').forEach(function(node){{node.classList.remove('is-highlighted');}});
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
                 conversion_timeout: float = 8.0) -> ReportResult:
    """在来源快照有效期内写入 report.html、diff.json、diagnostics.json 和双侧资源。"""
    if conversion_timeout <= 0:
        raise ValueError("转换超时必须为正数")
    directory = Path(output_dir).resolve()
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
    html = _report_html(document, preview.html)
    (directory / "diff.json").write_text(diff_json, encoding="utf-8")
    (directory / "diagnostics.json").write_text(dumps(DiagnosticsDocument(diagnostics)), encoding="utf-8")
    (directory / "report.html").write_text(html, encoding="utf-8")
    return ReportResult(directory, document)
