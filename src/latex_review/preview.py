"""基于 plasTeX DOM 的双侧内容预览；不模拟最终 TeX 版式。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
import re

from plasTeX.TeX import TeX

from .contract import Diagnostic, SourceLocation
from .structure import ParsedNode, ParsedProject, _argument, _masked, _quiet_plastex


_MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"


@dataclass(frozen=True)
class PreviewResult:
    html: str
    diagnostics: tuple[Diagnostic, ...]


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _anchor(side: str, node_id: str) -> str:
    return f"{side}-{node_id}"


def _math(raw: str, display: bool) -> str:
    if raw.startswith("\\begin"):
        wrapped = raw  # MathJax 处理完整环境；不能再套一层展示公式定界符。
    elif raw.startswith("\\[") and raw.endswith("\\]"):
        wrapped = raw
    elif raw.startswith("\\(") and raw.endswith("\\)"):
        wrapped = raw
    elif raw.startswith("$$") and raw.endswith("$$"):
        wrapped = f"\\[{raw[2:-2]}\\]"
    elif raw.startswith("$") and raw.endswith("$"):
        wrapped = f"\\({raw[1:-1]}\\)"
    else:
        wrapped = f"\\[{raw}\\]" if display else f"\\({raw}\\)"
    tag = "div" if display else "span"
    return f'<{tag} class="math-tex" data-raw-tex="{_e(raw)}">{_e(wrapped)}</{tag}>'


@lru_cache(maxsize=2048)
def _parse_dom(raw: str):
    tex = TeX()
    tex.input("\\begin{document}\n" + raw + "\n\\end{document}")
    with _quiet_plastex():
        document = tex.parse()
    return next((child for child in document.childNodes if getattr(child, "nodeName", "") == "document"), document)


def _dom_html(node: object, project: ParsedProject, side: str, inline: list[ParsedNode], cursors: dict[str, int]) -> str:
    name = getattr(node, "nodeName", "")
    if name == "#text":
        return _e(str(node))
    if name in {"label", "centering", "hfill", "noindent"}:
        return ""
    if name in {"cite", "citep", "citet", "ref", "eqref", "autoref", "pageref", "math", "displaymath"}:
        kind = "citation" if name.startswith("cite") else "reference" if name.endswith("ref") else "inline_math"
        candidates = [item for item in inline if item.review.type == kind]
        index = cursors.get(kind, 0)
        item = candidates[index] if index < len(candidates) else None
        cursors[kind] = index + 1
        raw = item.review.raw_latex if item else str(getattr(node, "source", ""))
        anchor = f' id="{_e(_anchor(side, item.review.id))}" data-node-id="{_e(item.review.id)}"' if item else ""
        if kind == "inline_math":
            return f"<span{anchor}>{_math(raw, False)}</span>"
        key_match = re.search(r"\{([^{}]+)\}", raw)
        keys = key_match.group(1).split(",") if key_match else []
        if kind == "citation":
            parts = []
            for key in keys:
                key = key.strip()
                target = project.labels.get(f"bib:{key}")
                parts.append(f'<a href="#{_e(_anchor(side, target))}">{_e(key)}</a>' if target else _e(key))
            return f'<span class="citation"{anchor}>[{", ".join(parts)}]</span>'
        key = keys[0].strip() if keys else ""
        target = project.labels.get(key)
        label = key
        if target:
            node_info = project.by_id()[target].review
            if node_info.type in {"part", "chapter", "section", "subsection", "subsubsection"} and node_info.section_path:
                label = node_info.section_path[-1]
            return f'<a class="cross-ref" href="#{_e(_anchor(side, target))}"{anchor}>{_e(label)}</a>'
        return f'<span class="unresolved-ref"{anchor}>?? ({_e(key)})</span>'
    if type(node).__module__ == "plasTeX.Context":
        return f'<span class="fallback-inline">未识别：<code>{_e(getattr(node, "source", ""))}</code></span>'
    children = "".join(_dom_html(child, project, side, inline, cursors) for child in getattr(node, "childNodes", ()))
    if name in {"#document", "document", "par", "bgroup", "group"}:
        return children
    if name in {"textbf", "bfseries", "bf"}:
        return f"<strong>{children}</strong>"
    if name in {"textit", "emph", "itshape", "it"}:
        return f"<em>{children}</em>"
    if name in {"texttt", "tt"}:
        return f"<code>{children}</code>"
    if name == "underline":
        return f"<u>{children}</u>"
    if name in {"footnote"}:
        return f'<small class="footnote">{children}</small>'
    if name in {"newline", "linebreak"}:
        return "<br>"
    if name == "url":
        return f"<code>{children}</code>"
    return children


def _inline_html(raw: str, project: ParsedProject, side: str, inline: list[ParsedNode] | None = None,
                 diagnostics: list[Diagnostic] | None = None, source: SourceLocation | None = None,
                 base_start: int | None = None) -> str:
    inline = inline or []
    if any(item.review.type == "fallback" for item in inline):
        # 对未知宏分段，使 plasTeX 的宽松解析不会吞掉其参数。
        pieces = []
        cursor = 0
        for item in sorted((node for node in inline if node.review.type == "fallback"), key=lambda node: node.expanded_start):
            relative = item.expanded_start - base_start if base_start is not None else raw.find(item.review.raw_latex, cursor)
            if relative < 0:
                continue
            prefix = [child for child in inline if child.review.type != "fallback" and
                      (base_start is None or base_start + cursor <= child.expanded_start < base_start + relative)]
            pieces.append(_inline_html(raw[cursor:relative], project, side, prefix, diagnostics, source,
                                       None if base_start is None else base_start + cursor))
            pieces.append(f'<span class="fallback-inline" id="{_e(_anchor(side, item.review.id))}" '
                          f'data-node-id="{_e(item.review.id)}">未识别：<code>{_e(item.review.raw_latex)}</code></span>')
            cursor = relative + len(item.review.raw_latex)
        suffix = [child for child in inline if child.review.type != "fallback" and
                  (base_start is None or child.expanded_start >= base_start + cursor)]
        pieces.append(_inline_html(raw[cursor:], project, side, suffix, diagnostics, source,
                                   None if base_start is None else base_start + cursor))
        return "".join(pieces)
    try:
        dom = _parse_dom(raw)
        if diagnostics is not None:
            unknown = set()
            def collect(node: object) -> None:
                if type(node).__module__ == "plasTeX.Context":
                    name = getattr(node, "nodeName", "")
                    if re.search(r"\\" + re.escape(name) + r"(?![A-Za-z@])", raw):
                        unknown.add(name)
                for child in getattr(node, "childNodes", ()):
                    collect(child)
            collect(dom)
            for name in sorted(unknown):
                location = source or SourceLocation(None, None, None, confidence=0, uncertainty_reason="预览片段无法定位")
                diagnostics.append(Diagnostic("preview_unknown_macro", "warning", f"预览无法解释宏 \\{name}；已显示原文",
                                              source_old=location if side == "old" else None,
                                              source_new=location if side == "new" else None))
        return _dom_html(dom, project, side, inline, {})
    except Exception as exc:
        if diagnostics is not None and raw.strip():
            location = source or SourceLocation(None, None, None, confidence=0, uncertainty_reason="预览片段无法定位")
            diagnostics.append(Diagnostic("preview_fallback", "warning", f"plasTeX 预览片段解析失败，已显示原文：{exc}",
                                          source_old=location if side == "old" else None,
                                          source_new=location if side == "new" else None))
        return f'<code class="fallback-inline">{_e(raw)}</code>'


def _source_details(node: ParsedNode) -> str:
    warnings = "".join(f'<p class="node-warning">{_e(diag.code)}：{_e(diag.message)}</p>' for diag in node.diagnostics)
    origin = node.review.source
    location = f"{origin.file}:{origin.start_line}" if origin.file and origin.start_line else "来源位置不确定"
    return (f'{warnings}<details class="latex-source"><summary>查看 LaTeX 原文 · {_e(location)}</summary>'
            f'<pre>{_e(node.review.raw_latex)}</pre></details>')


def _embedded_fallbacks(children: list[ParsedNode], side: str) -> str:
    return "".join(
        f'<p class="node-warning" id="{_e(_anchor(side, child.review.id))}" '
        f'data-node-id="{_e(child.review.id)}">未识别的局部内容：<code>{_e(child.review.raw_latex)}</code></p>'
        for child in children if child.review.type == "fallback"
    )


def _body(node: ParsedNode, project: ParsedProject, side: str, by_id: dict[str, ParsedNode], diagnostics: list[Diagnostic]) -> str:
    kind = node.review.type
    children = [by_id[child] for child in node.review.child_ids]
    if kind in {"part", "chapter", "section", "subsection", "subsubsection"}:
        arg = _argument(_masked(node.review.raw_latex), len(re.match(r"\\[A-Za-z@]+", node.review.raw_latex).group()))
        title = node.review.raw_latex[arg[2]:arg[0] - 1] if arg else node.review.plain_text or ""
        title_children = [child for child in children if child.expanded_end <= node.expanded_end]
        depth = {"part": 1, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}[kind]
        heading = f"<h{depth}>{_inline_html(title, project, side, title_children, diagnostics, node.review.source,
                                             node.expanded_start + arg[2] if arg else None)}</h{depth}>"
        return heading + "".join(_render_node(child, project, side, by_id, diagnostics) for child in children)
    if kind == "paragraph":
        return f'<p>{_inline_html(node.review.raw_latex, project, side, children, diagnostics, node.review.source, node.expanded_start)}</p>'
    if kind == "equation":
        return _math(node.review.raw_latex, True) + _embedded_fallbacks(children, side)
    if kind == "list":
        tag = "ol" if node.review.raw_latex.startswith("\\begin{enumerate}") else "ul"
        return f"<{tag}>" + "".join(_render_node(child, project, side, by_id, diagnostics) for child in children) + f"</{tag}>"
    if kind == "list_item":
        body = re.sub(r"^\\item(?:\[[^\]]*\])?", "", node.review.raw_latex, count=1)
        if children:
            return "".join(_render_node(child, project, side, by_id, diagnostics) for child in children)
        return _inline_html(body, project, side, diagnostics=diagnostics, source=node.review.source)
    if kind == "theorem":
        name = re.match(r"\\begin\{([^{}]+)\}", node.review.raw_latex)
        title = name.group(1) if name else "定理"
        return f'<p class="theorem-title">{_e(title)}</p>' + "".join(_render_node(child, project, side, by_id, diagnostics) for child in children)
    if kind == "figure":
        caption = re.search(r"\\caption(?:\[[^\]]*\])?\s*\{([^{}]*)\}", node.review.raw_latex)
        assets = "".join(f'<p class="asset">图资源：{_e(asset)}</p>' for asset in node.assets)
        caption_children = [child for child in children if child.review.type != "fallback"]
        return assets + (f'<figcaption>{_inline_html(caption.group(1), project, side, caption_children, diagnostics, node.review.source)}</figcaption>' if caption else "") + _embedded_fallbacks(children, side)
    if kind == "table":
        caption = re.search(r"\\caption(?:\[[^\]]*\])?\s*\{([^{}]*)\}", node.review.raw_latex)
        caption_children = [child for child in children if child.review.type != "fallback"]
        return ((f'<p class="table-caption">{_inline_html(caption.group(1), project, side, caption_children, diagnostics, node.review.source)}</p>' if caption else "")
                + f'<pre class="table-source">{_e(node.review.raw_latex)}</pre>' + _embedded_fallbacks(children, side))
    if kind == "bibliography":
        return '<h3>参考文献</h3>' + ("<ol>" + "".join(_render_node(child, project, side, by_id, diagnostics) for child in children) + "</ol>" if children else '<p>参考文献资源见源码。</p>')
    if kind == "bibliography_entry":
        body = re.sub(r"^\\bibitem(?:\[[^\]]*\])?\s*\{[^{}]+\}", "", node.review.raw_latex, count=1)
        base = node.expanded_start + len(node.review.raw_latex) - len(body)
        return f'<span>{_inline_html(body, project, side, children, diagnostics, node.review.source, base)}</span>'
    if kind == "fallback":
        return f'<p class="fallback-label">未识别的 LaTeX 内容，原文如下：</p><pre class="fallback-raw">{_e(node.review.raw_latex)}</pre>'
    if kind == "environment":
        return "".join(_render_node(child, project, side, by_id, diagnostics) for child in children)
    return _inline_html(node.review.raw_latex, project, side, children, diagnostics, node.review.source)


def _render_node(node: ParsedNode, project: ParsedProject, side: str, by_id: dict[str, ParsedNode], diagnostics: list[Diagnostic]) -> str:
    kind = node.review.type
    if kind in {"inline_math", "citation", "reference"}:
        return ""  # 行内节点由父段落的 plasTeX DOM 呈现。
    tag = {"figure": "figure", "list_item": "li", "theorem": "aside", "bibliography_entry": "li"}.get(kind, "div")
    return (f'<{tag} class="review-node node-{_e(kind)}" id="{_e(_anchor(side, node.review.id))}" '
            f'data-node-id="{_e(node.review.id)}" data-side="{side}">'
            + _body(node, project, side, by_id, diagnostics) + _source_details(node) + f"</{tag}>")


def _side(project: ParsedProject, side: str, title: str, diagnostics: list[Diagnostic]) -> str:
    by_id = project.by_id()
    roots = [node for node in project.nodes if node.review.parent_id is None]
    content = "".join(_render_node(node, project, side, by_id, diagnostics) for node in roots)
    return f'<section class="preview-side" aria-label="{_e(title)}" data-side="{side}"><h2>{_e(title)}</h2>{content}</section>'


def render_preview(old: ParsedProject, new: ParsedProject) -> PreviewResult:
    """返回双侧连续阅读视图与公共诊断，不写文件。"""
    diagnostics = [*old.diagnostics, *new.diagnostics]
    for project, side in ((old, "old"), (new, "new")):
        by_id = project.by_id()
        for node in project.nodes:
            if any(by_id[child].references and by_id[child].expanded_start >= node.expanded_start
                   and by_id[child].expanded_end <= node.expanded_end for child in node.review.child_ids):
                continue
            for key in node.references:
                if key not in project.labels:
                    diagnostics.append(Diagnostic("unresolved_reference", "warning", f"交叉引用目标不存在：{key}",
                                                  source_old=node.review.source if side == "old" else None,
                                                  source_new=node.review.source if side == "new" else None))
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>LaTeX 内容预览</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;color:#1f2937;background:#f5f4f0;font:16px/1.7 system-ui,sans-serif}}
header{{padding:1rem 1.5rem;background:#fff;border-bottom:1px solid #d8d5ca}}header h1{{margin:0;font-size:1.25rem}}
.notice{{margin:.4rem 0 0;color:#75531e}}#math-status{{margin:.45rem 0 0;color:#8b3d24}}
main{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;padding:1rem}}
.preview-side{{background:#fff;border:1px solid #ddd8cb;padding:1.5rem;min-width:0;max-height:calc(100vh - 9rem);overflow:auto}}
.preview-side h2{{margin:0 0 1rem;border-bottom:1px solid #ddd8cb}}
.review-node{{scroll-margin-top:1rem}}.review-node:target{{outline:2px solid #996e26;outline-offset:3px}}
.review-node p{{margin:.6rem 0}}.node-section,.node-subsection,.node-subsubsection{{margin-top:1.25rem}}
.node-theorem{{border-left:3px solid #7b7161;padding:.25rem 1rem;background:#faf9f6}}
.theorem-title{{font-weight:700;text-transform:capitalize}}figure{{margin:1rem 0;padding:.8rem;border:1px solid #ddd8cb}}
figcaption,.table-caption{{font-style:italic}}.asset,.fallback-label{{color:#75531e}}
.math-tex{{font-family:serif;white-space:pre-wrap;overflow-wrap:anywhere}}.node-equation{{overflow-x:auto;text-align:center;margin:1rem 0}}
.latex-source{{font-size:.75rem;color:#6b6356;margin:.4rem 0}}.latex-source pre,.fallback-raw,.table-source{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f4ef;padding:.7rem;text-align:left}}
.node-warning{{color:#9b3c26;background:#fff1e8;padding:.35rem .55rem}}.fallback-inline{{background:#fff1e8;color:#8d3320}}
.citation,.cross-ref{{color:#315c86}}.unresolved-ref{{color:#9b3c26}}
@media(max-width:800px){{main{{grid-template-columns:1fr}}.preview-side{{max-height:none}}}}
</style></head><body>
<header><h1>LaTeX 内容预览</h1><p class="notice">用于审阅正文内容，不代表最终编译版式。</p>
<p id="math-status" role="status">正在加载在线公式排版；原始 TeX 可直接阅读。</p></header>
<main>{_side(old, "old", "修改前", diagnostics)}{_side(new, "new", "修改后", diagnostics)}</main>
<script>
(function(){{
 window.MathJax={{tex:{{processEnvironments:true}}}};
 const status=document.getElementById('math-status');
 let settled=false;
 function failed(){{if(settled)return;settled=true;status.textContent='在线公式排版不可用；页面保留原始 TeX 公式供阅读。';status.dataset.state='failed';}}
 const timer=setTimeout(failed,10000);
 window.mathDependencyFailed=failed;
 window.mathDependencyReady=function(){{
   if(!window.MathJax || !MathJax.startup || !MathJax.startup.promise){{failed();return;}}
   MathJax.startup.promise.then(function(){{if(settled)return;settled=true;clearTimeout(timer);status.textContent='公式排版已加载。';status.dataset.state='ready';}},failed);
 }};
}})();
</script>
<script async src="{_MATHJAX_URL}" onload="mathDependencyReady()" onerror="mathDependencyFailed()"></script>
</body></html>'''
    return PreviewResult(html, tuple(diagnostics))
