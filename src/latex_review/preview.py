"""基于 plasTeX DOM 的双侧内容预览；不模拟最终 TeX 版式。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
import re
from typing import Mapping

from plasTeX import Command
from plasTeX.TeX import TeX

from .contract import Diagnostic, ReviewNode, SourceLocation
from .structure import ParsedNode, ParsedProject, _argument, _masked, _quiet_plastex
from .table_model import display_tables
from .macros import expand_call
from .text_diff import CITATION_COMMANDS, _without_comments, split_sentences
from .latex_commands import MATH_COMMANDS, REFERENCE_COMMANDS, TEXT_COMMANDS


_MATHJAX_URL = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
_INLINE_SAFE = {
    "textbf", "bfseries", "bf", "textit", "emph", "itshape", "it", "texttt", "tt", "underline",
    "footnote", "newline", "linebreak", *CITATION_COMMANDS, "ref", "eqref", "autoref",
    "pageref", "label", "centering", "hfill", "noindent", "url", "math", "displaymath",
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "sigma",
    "omega", "sum", "prod", "int", "frac", "sqrt", "left", "right", "mathrm", "mathbf",
    "mathbb", "mathcal", "text", "cdot", "times", "leq", "geq", "infty", "partial",
    "nabla", "ell", "operatorname", "overline", "hat", "bar", "tilde", *MATH_COMMANDS,
    *REFERENCE_COMMANDS, *TEXT_COMMANDS,
}
_UNSAFE_MATH = re.compile(
    r"\\(?:href|url|html\w*|class|style|cssId|cssClass|require|input|include|write|openout|read)"
    r"(?![A-Za-z@])|(?:javascript|data|vbscript)\s*:", re.I)


@dataclass(frozen=True)
class PreviewResult:
    html: str
    diagnostics: tuple[Diagnostic, ...]


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _anchor(side: str, node_id: str) -> str:
    return f"{side}-{node_id}"


def _citation_html(keys: tuple[str, ...], project: ParsedProject, side: str, anchor: str = "") -> str:
    parts = []
    by_id = project.by_id()
    for key in keys:
        key = key.strip()
        target = project.labels.get(f"bib:{key}")
        label = key
        if target:
            item = by_id[target]
            parent = by_id.get(item.review.parent_id)
            siblings = [by_id[child] for child in parent.review.child_ids] if parent else []
            bibliography = [node for node in siblings if node.review.type == "bibliography_entry"]
            # 仅在手写文献列表的编号规则可确认时推导序号；BibTeX 顺序不是排版编号。
            if (parent and parent.review.raw_latex.startswith(r"\begin{thebibliography}") and
                    len(bibliography) == len(siblings) and
                    not re.search(r"\\(?:setcounter|renewcommand|item)(?![A-Za-z@])", parent.review.raw_latex) and
                    all(re.match(r"\\bibitem\s*\{", node.review.raw_latex) for node in bibliography)):
                label = str(bibliography.index(item) + 1)
            else:
                explicit = re.match(r"\\bibitem\[([0-9]+)\]\s*\{", item.review.raw_latex)
                if explicit:
                    label = explicit.group(1)
        parts.append(f'<a href="#{_e(_anchor(side, target))}">{_e(label)}</a>' if target else _e(label))
    return f'<span class="citation"{anchor}>[{", ".join(parts)}]</span>'


def _missing_bibliography(key: str, project: ParsedProject, side: str, source: SourceLocation) -> Diagnostic:
    handmade = f"bib:{key}" in project.labels
    return Diagnostic("bibliography_unsupported" if handmade else "bibliography_key_unresolved", "warning",
                      f"引用键 {key} 的手写文献格式未提供结构化元数据；已保留引用键" if handmade else
                      f"引用键 {key} 无法解析文献元数据；已保留引用键",
                      source_old=source if side == "old" else None,
                      source_new=source if side == "new" else None)


def _math(raw: str, display: bool, fallback_url: str | None = None) -> str:
    if _UNSAFE_MATH.search(raw):
        return '<span class="preview-unavailable">此处暂无法预览</span>'
    if raw.startswith("\\begin"):
        expression = raw
    elif raw.startswith("\\[") and raw.endswith("\\]"):
        expression = raw[2:-2]
    elif raw.startswith("\\(") and raw.endswith("\\)"):
        expression = raw[2:-2]
    elif raw.startswith("$$") and raw.endswith("$$"):
        expression = raw[2:-2]
    elif raw.startswith("$") and raw.endswith("$"):
        expression = raw[1:-1]
    else:
        expression = raw
    # 两侧可能复用相同标签；页面中的交叉引用由结构层处理，避免 MathJax 重复登记。
    expression = re.sub(r"\\label\s*\{[^{}]*\}", "", expression)
    tag = "div" if display else "span"
    fallback = f' data-fallback-src="{_e(fallback_url)}"' if fallback_url else ""
    return f'<{tag} class="math-tex" data-display="{str(display).lower()}"{fallback}>{_e(expression)}</{tag}>'


def _math_preview(raw: str, project: ParsedProject, side: str, source: SourceLocation,
                  diagnostics: list[Diagnostic], display: bool, fallback_url: str | None = None) -> str:
    expanded = raw
    unavailable = False
    if project.macros:
        pieces = []
        cursor = 0
        for match in re.finditer(r"\\([A-Za-z@]+)", raw):
            if match.start() < cursor or match.group(1) not in project.macros:
                continue
            pieces.append(raw[cursor:match.start()])
            replacement, end, problem = expand_call(raw, match.start(), match.group(1), project.macros)
            if project.macros[match.group(1)].strategy == "raw":
                unavailable = True
            if problem:
                unavailable = True
                diagnostics.append(Diagnostic("preview_macro_fallback", "warning",
                                              f"\\{match.group(1)}：{problem}；已显示占位",
                                              source_old=source if side == "old" else None,
                                              source_new=source if side == "new" else None))
            pieces.append(replacement)
            cursor = end
        if pieces:
            pieces.append(raw[cursor:])
            expanded = "".join(pieces)
    if unavailable:
        return '<span class="preview-unavailable">此处暂无法预览</span>'
    if _UNSAFE_MATH.search(expanded):
        diagnostics.append(Diagnostic("preview_unsafe_math", "warning", "公式包含被禁止的命令，已显示占位",
                                      source_old=source if side == "old" else None,
                                      source_new=source if side == "new" else None))
    rendered = _math(expanded, display, fallback_url)
    if expanded != raw:
        return f'<span class="macro-placeholder">{rendered}</span>'
    return rendered


@lru_cache(maxsize=2048)
def _parse_dom(raw: str):
    if any(match.group(1) not in _INLINE_SAFE for match in re.finditer(r"\\([A-Za-z@]+)", raw)):
        raise ValueError("片段含未允许的宏，已显示占位")
    tex = TeX()
    # 片段没有论文的导言区；仅注册预览所需的字面参数，不加载论文包或执行宏。
    for name in CITATION_COMMANDS:
        tex.ownerDocument.context.addGlobal(name, type(name, (Command,), {
            "args": "* [ prenote:str ] [ postnote:str ] keys:str",
        }))
    for name in REFERENCE_COMMANDS:
        tex.ownerDocument.context.addGlobal(name, type(name, (Command,), {"args": "* key:str"}))
    tex.ownerDocument.context.addGlobal("sep", type("sep", (Command,), {"args": ""}))
    tex.input("\\begin{document}\n" + raw + "\n\\end{document}")
    with _quiet_plastex() as quiet:
        document = tex.parse()
    if quiet.errors:
        raise ValueError(quiet.errors[0])
    return next((child for child in document.childNodes if getattr(child, "nodeName", "") == "document"), document)


def _dom_html(node: object, project: ParsedProject, side: str, inline: list[ParsedNode], cursors: dict[str, int],
              diagnostics: list[Diagnostic], source: SourceLocation | None) -> str:
    name = getattr(node, "nodeName", "")
    if name == "#text":
        return _e(str(node))
    if name == "sep":
        return " · "
    if name in {"label", "centering", "hfill", "noindent"}:
        return ""
    if name in {*CITATION_COMMANDS, *REFERENCE_COMMANDS, "math", "displaymath"}:
        kind = "citation" if name in CITATION_COMMANDS else "reference" if name in REFERENCE_COMMANDS else "inline_math"
        candidates = [item for item in inline if item.review.type == kind]
        index = cursors.get(kind, 0)
        item = candidates[index] if index < len(candidates) else None
        cursors[kind] = index + 1
        raw = item.review.raw_latex if item else str(getattr(node, "source", ""))
        anchor = f' id="{_e(_anchor(side, item.review.id))}" data-node-id="{_e(item.review.id)}"' if item else ""
        if kind == "inline_math":
            return f"<span{anchor}>{_math_preview(raw, project, side, item.review.source if item else SourceLocation(None, None, None, confidence=0, uncertainty_reason='公式来源不确定'), diagnostics, False)}</span>"
        if item:
            keys = item.citations if kind == "citation" else item.references
        else:
            command = re.match(r"\\[A-Za-z@]+", raw)
            argument = _argument(_masked(raw), command.end()) if command else None
            keys = tuple(part.strip() for part in argument[1].split(",")) if argument else ()
        if kind == "citation":
            location = item.review.source if item else source or SourceLocation(
                None, None, None, confidence=0, uncertainty_reason="引用来源不确定")
            for key in keys:
                if key not in project.bibliography:
                    diagnostic = _missing_bibliography(key, project, side, location)
                    if diagnostic not in diagnostics:
                        diagnostics.append(diagnostic)
            return _citation_html(tuple(keys), project, side, anchor)
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
        return '<span class="preview-unavailable">此处暂无法预览</span>'
    children = "".join(_dom_html(child, project, side, inline, cursors, diagnostics, source)
                       for child in getattr(node, "childNodes", ()))
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
    heading = re.match(r"^(\s*)\\(?:paragraph|subparagraph)\*?\s*\{([^{}]*)\}", raw)
    if heading:
        remainder = heading.end()
        suffix = [child for child in inline if base_start is not None and child.expanded_start >= base_start + remainder]
        return (_e(heading.group(1)) + f'<strong>{_e(heading.group(2))}</strong> '
                + _inline_html(raw[remainder:], project, side, suffix, diagnostics, source,
                               None if base_start is None else base_start + remainder))
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
            pieces.append(f'<span class="preview-unavailable" id="{_e(_anchor(side, item.review.id))}" '
                          f'data-node-id="{_e(item.review.id)}">此处暂无法预览</span>')
            cursor = relative + len(item.review.raw_latex)
        suffix = [child for child in inline if child.review.type != "fallback" and
                  (base_start is None or child.expanded_start >= base_start + cursor)]
        pieces.append(_inline_html(raw[cursor:], project, side, suffix, diagnostics, source,
                                   None if base_start is None else base_start + cursor))
        return "".join(pieces)
    sites = sorted((item for item in inline if item.review.type in {"citation", "reference"}),
                   key=lambda item: item.expanded_start)
    if sites:
        pieces = []
        cursor = 0
        for item in sites:
            relative = item.expanded_start - base_start if base_start is not None else raw.find(item.review.raw_latex, cursor)
            if relative < cursor or relative + len(item.review.raw_latex) > len(raw):
                continue
            prefix = [child for child in inline if child.review.type not in {"citation", "reference"} and
                      (base_start is None or base_start + cursor <= child.expanded_start < base_start + relative)]
            pieces.append(_inline_html(raw[cursor:relative], project, side, prefix, diagnostics, source,
                                       None if base_start is None else base_start + cursor))
            anchor = f' id="{_e(_anchor(side, item.review.id))}" data-node-id="{_e(item.review.id)}"'
            if item.review.type == "citation":
                for key in item.citations:
                    if key not in project.bibliography and diagnostics is not None:
                        diagnostic = _missing_bibliography(key, project, side, item.review.source)
                        if diagnostic not in diagnostics:
                            diagnostics.append(diagnostic)
                pieces.append(_citation_html(item.citations, project, side, anchor))
            else:
                key = item.references[0] if item.references else ""
                target = project.labels.get(key)
                if target:
                    node_info = project.by_id()[target].review
                    label = node_info.section_path[-1] if node_info.type in {"part", "chapter", "section", "subsection", "subsubsection"} and node_info.section_path else key
                    pieces.append(f'<a class="cross-ref" href="#{_e(_anchor(side, target))}"{anchor}>{_e(label)}</a>')
                else:
                    pieces.append(f'<span class="unresolved-ref"{anchor}>?? ({_e(key)})</span>')
            cursor = relative + len(item.review.raw_latex)
        suffix = [child for child in inline if child.review.type not in {"citation", "reference"} and
                  (base_start is None or child.expanded_start >= base_start + cursor)]
        pieces.append(_inline_html(raw[cursor:], project, side, suffix, diagnostics, source,
                                   None if base_start is None else base_start + cursor))
        return "".join(pieces)
    macro_math = [item for item in inline if item.review.type == "inline_math" and
                  any(match.group(1) in project.macros for match in re.finditer(r"\\([A-Za-z@]+)", item.review.raw_latex))]
    if macro_math and base_start is not None:
        pieces = []
        cursor = 0
        for item in macro_math:
            start = item.expanded_start - base_start
            prefix = [child for child in inline if child.expanded_start >= base_start + cursor and
                      child.expanded_end <= base_start + start]
            pieces.append(_inline_html(raw[cursor:start], project, side, prefix, diagnostics, source, base_start + cursor))
            anchor = _anchor(side, item.review.id)
            pieces.append(f'<span id="{_e(anchor)}" data-node-id="{_e(item.review.id)}">'
                          + _math_preview(item.review.raw_latex, project, side, item.review.source,
                                          diagnostics if diagnostics is not None else [], False) + "</span>")
            cursor = item.expanded_end - base_start
        suffix = [child for child in inline if child.expanded_start >= base_start + cursor]
        pieces.append(_inline_html(raw[cursor:], project, side, suffix, diagnostics, source, base_start + cursor))
        return "".join(pieces)
    if project.macros:
        mask = _masked(raw)
        if any(match.group(1) in project.macros for match in re.finditer(r"\\([A-Za-z@]+)", mask)):
            pieces = []
            consumed = 0
            search_at = 0
            while (found := re.search(r"\\([A-Za-z@]+)", mask[search_at:])) is not None:
                start = search_at + found.start()
                name = found.group(1)
                if name not in project.macros:
                    search_at = start + len(name) + 1
                    continue
                prefix_nodes = [child for child in inline if base_start is not None and
                                base_start + consumed <= child.expanded_start < base_start + start]
                pieces.append(_inline_html(raw[consumed:start], project, side, prefix_nodes, diagnostics, source,
                                           None if base_start is None else base_start + consumed))
                rendered, end, problem = expand_call(raw, start, name, project.macros)
                if problem and diagnostics is not None:
                    location = source or SourceLocation(None, None, None, confidence=0, uncertainty_reason="宏调用位置不确定")
                    diagnostics.append(Diagnostic("preview_macro_fallback", "warning", f"\\{name}：{problem}；已显示占位",
                                                  source_old=location if side == "old" else None,
                                                  source_new=location if side == "new" else None))
                pieces.append(f'<span class="macro-placeholder">{_e(rendered) if not problem and project.macros[name].strategy != "raw" else "此处暂无法预览"}</span>')
                consumed = end
                search_at = end
            suffix_nodes = [child for child in inline if base_start is not None and child.expanded_start >= base_start + consumed]
            pieces.append(_inline_html(raw[consumed:], project, side, suffix_nodes, diagnostics, source,
                                       None if base_start is None else base_start + consumed))
            return "".join(pieces)
    try:
        dom = _parse_dom(raw)
        if diagnostics is not None:
            unknown = set()
            def collect(node: object) -> None:
                if type(node).__module__ == "plasTeX.Context":
                    name = getattr(node, "nodeName", "")
                    if name not in _INLINE_SAFE and re.search(r"\\" + re.escape(name) + r"(?![A-Za-z@])", raw):
                        unknown.add(name)
                for child in getattr(node, "childNodes", ()):
                    collect(child)
            collect(dom)
            for name in sorted(unknown):
                location = source or SourceLocation(None, None, None, confidence=0, uncertainty_reason="预览片段无法定位")
                diagnostics.append(Diagnostic("preview_unknown_macro", "warning", f"预览无法解释宏 \\{name}；已显示占位",
                                              source_old=location if side == "old" else None,
                                              source_new=location if side == "new" else None))
        return _dom_html(dom, project, side, inline, {}, diagnostics if diagnostics is not None else [], source)
    except Exception as exc:
        if diagnostics is not None and raw.strip():
            location = source or SourceLocation(None, None, None, confidence=0, uncertainty_reason="预览片段无法定位")
            diagnostics.append(Diagnostic("preview_fallback", "warning", f"plasTeX 预览片段解析失败，已显示占位：{exc}",
                                          source_old=location if side == "old" else None,
                                          source_new=location if side == "new" else None))
        return '<span class="preview-unavailable">此处暂无法预览</span>'


def _caption(raw: str) -> tuple[str, int] | None:
    mask = _masked(raw)
    command = re.search(r"\\caption(?![A-Za-z@])", mask)
    argument = _argument(mask, command.end()) if command else None
    return None if argument is None else (raw[argument[2]:argument[0] - 1], argument[2])


def _embedded_fallbacks(children: list[ParsedNode], side: str) -> str:
    return "".join(
        f'<p class="node-warning" id="{_e(_anchor(side, child.review.id))}" '
        f'data-node-id="{_e(child.review.id)}">此处暂无法预览</p>'
        for child in children if child.review.type == "fallback"
    )


def _table_cell_html(cell: str, project: ParsedProject, side: str, diagnostics: list[Diagnostic],
                     source: SourceLocation) -> str:
    stacked = re.fullmatch(r"\s*\\(?:makecell|shortstack)(?:\[[^\]]*\])?\s*\{(.*)\}\s*", cell, re.S)
    if stacked:
        return "<br>".join(_table_cell_html(part, project, side, diagnostics, source)
                             for part in stacked.group(1).split(r"\\"))
    mask = _masked(cell)
    command = re.compile(r"\\(?:" + "|".join(CITATION_COMMANDS) + r")(?![A-Za-z@])")
    depth = 0
    index = 0
    while index < len(mask):
        if mask[index] == "\\":
            match = command.match(mask, index) if depth == 0 else None
            if match:
                argument = _argument(mask, match.end())
                if argument:
                    keys = tuple(part.strip() for part in argument[1].split(",") if part.strip())
                    diagnostics.extend(_missing_bibliography(key, project, side, source)
                                       for key in keys if key not in project.bibliography)
                    return (_inline_html(cell[:index], project, side, diagnostics=diagnostics, source=source)
                            + _citation_html(keys, project, side)
                            + _table_cell_html(cell[argument[0]:], project, side, diagnostics, source))
            index += 2 if index + 1 < len(mask) else 1
            continue
        if mask[index] == "{":
            depth += 1
        elif mask[index] == "}" and depth:
            depth -= 1
        index += 1
    return _inline_html(cell, project, side, diagnostics=diagnostics, source=source)


def _table_preview(node: ParsedNode, project: ParsedProject, side: str,
                   diagnostics: list[Diagnostic]) -> str | None:
    """宽松显示表体；精确单元格比较另由 parse_table 处理。"""
    tables = display_tables(node.review.raw_latex)
    if not tables:
        diagnostics.append(Diagnostic("preview_table_fallback", "warning",
                                      "表格预览无法提取表体；已显示占位",
                                      source_old=node.review.source if side == "old" else None,
                                      source_new=node.review.source if side == "new" else None))
        return None
    rendered = []
    for table in tables:
        rows = []
        for row_index, row in enumerate(table, 1):
            column = 1
            cells = []
            for cell in row:
                attrs = (f' data-row="{row_index}" data-column="{column}"'
                         + (f' colspan="{cell.colspan}"' if cell.colspan > 1 else "")
                         + (f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else ""))
                cells.append(f'<td{attrs}>{_table_cell_html(cell.text, project, side, diagnostics, node.review.source)}</td>')
                column += cell.colspan
            rows.append(f"<tr>{''.join(cells)}</tr>")
        rendered.append('<table class="table-preview"><tbody>' + "".join(rows) + "</tbody></table>")
    return "".join(rendered)


def _body(node: ParsedNode, project: ParsedProject, side: str, by_id: dict[str, ParsedNode], diagnostics: list[Diagnostic],
          figure_assets: Mapping[tuple[str, str], str] | None = None,
          sentence_highlights: Mapping[tuple[str, str], frozenset[int]] | None = None) -> str:
    kind = node.review.type
    children = [by_id[child] for child in node.review.child_ids]
    if kind in {"part", "chapter", "section", "subsection", "subsubsection"}:
        arg = _argument(_masked(node.review.raw_latex), len(re.match(r"\\[A-Za-z@]+", node.review.raw_latex).group()))
        title = node.review.raw_latex[arg[2]:arg[0] - 1] if arg else node.review.plain_text or ""
        title_children = [child for child in children if child.expanded_end <= node.expanded_end]
        depth = {"part": 1, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}[kind]
        title_html = _inline_html(title, project, side, title_children, diagnostics, node.review.source,
                                  node.expanded_start + arg[2] if arg else None)
        heading = f"<h{depth}>{title_html}</h{depth}>"
        return heading + "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
    if kind == "paragraph":
        changed = (sentence_highlights or {}).get((side, node.review.id))
        if changed is not None:
            pieces = []
            cursor = 0
            raw = node.review.raw_latex
            for number, sentence in enumerate(split_sentences(raw), 1):
                pieces.append(_e(_without_comments(raw[cursor:sentence.start])[0]))
                contained = [child for child in children if node.expanded_start + sentence.start <= child.expanded_start
                             and child.expanded_end <= node.expanded_start + sentence.end]
                body = _inline_html(sentence.text, project, side, contained, diagnostics, node.review.source,
                                    node.expanded_start + sentence.start)
                sentence_id = f"{side}-{node.review.id}-sentence-{number}"
                sentence_class = "review-sentence sentence-changed" if number in changed else "review-sentence"
                pieces.append(f'<span class="{sentence_class}" id="{_e(sentence_id)}">{body}</span>')
                cursor = sentence.end
            pieces.append(_e(_without_comments(raw[cursor:])[0]))
            return f'<p>{"".join(pieces)}</p>'
        return f'<p>{_inline_html(node.review.raw_latex, project, side, children, diagnostics, node.review.source, node.expanded_start)}</p>'
    if kind == "equation":
        arrays = "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights)
                         for child in children if child.review.type == "math_array")
        fallback_url = (figure_assets or {}).get((side, node.review.id))
        return _math_preview(node.review.raw_latex, project, side, node.review.source, diagnostics, True, fallback_url) + arrays
    if kind == "math_array":
        return ""  # 已由父公式预览，保留独立来源锚点。
    if kind == "frontmatter":
        return "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
    if kind in {"abstract", "keywords"}:
        title = "摘要" if kind == "abstract" else "关键词"
        return f"<h3>{title}</h3>" + "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
    if kind == "metadata":
        return ""  # 元数据保留结构与来源，正文阅读内容由后续界面批次处理。
    if kind == "list":
        tag = "ol" if node.review.raw_latex.startswith("\\begin{enumerate}") else "ul"
        return f"<{tag}>" + "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children) + f"</{tag}>"
    if kind == "list_item":
        body = re.sub(r"^\\item(?:\[[^\]]*\])?", "", node.review.raw_latex, count=1)
        if children:
            return "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
        return _inline_html(body, project, side, diagnostics=diagnostics, source=node.review.source)
    if kind == "theorem":
        name = re.match(r"\\begin\{([^{}]+)\}", node.review.raw_latex)
        title = name.group(1) if name else "定理"
        return f'<p class="theorem-title">{_e(title)}</p>' + "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
    if kind == "figure":
        caption = _caption(node.review.raw_latex)
        assets = (figure_assets or {}).get((side, node.review.id))
        if assets is None:
            assets = "".join(f'<p class="asset">图资源：{_e(asset)}</p>' for asset in node.assets)
        missing = ('<p class="preview-unavailable">此处暂无法预览</p>' if any(
            diag.code in {"missing_dependency", "dependency_outside_root"} for diag in node.diagnostics) else "")
        caption_children = [child for child in children if child.review.type != "fallback"]
        return assets + missing + (f'<figcaption>{_inline_html(caption[0], project, side, caption_children, diagnostics, node.review.source, node.expanded_start + caption[1])}</figcaption>' if caption else "") + _embedded_fallbacks(children, side)
    if kind == "table":
        caption = _caption(node.review.raw_latex)
        caption_children = [child for child in children if child.review.type != "fallback"]
        compiled = (figure_assets or {}).get((side, node.review.id))
        table_html = None if compiled and not display_tables(node.review.raw_latex) else _table_preview(node, project, side, diagnostics)
        if table_html is None and compiled:
            table_html = f'<img class="compiled-fragment" src="{_e(compiled)}" alt="表格预览" loading="lazy">'
        return ((f'<p class="table-caption">{_inline_html(caption[0], project, side, caption_children, diagnostics, node.review.source, node.expanded_start + caption[1])}</p>' if caption else "")
                + (table_html or '<p class="preview-unavailable">此处暂无法预览</p>')
                + _embedded_fallbacks(children, side))
    if kind == "bibliography":
        return '<h3>参考文献</h3>' + ("<ol>" + "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children) + "</ol>" if children else '<p class="preview-unavailable">此处暂无法预览</p>')
    if kind == "bibliography_entry":
        body = re.sub(r"^\\bibitem(?:\[[^\]]*\])?\s*\{[^{}]+\}", "", node.review.raw_latex, count=1)
        base = node.expanded_start + len(node.review.raw_latex) - len(body)
        return f'<span>{_inline_html(body, project, side, children, diagnostics, node.review.source, base)}</span>'
    if kind == "fallback":
        return '<p class="preview-unavailable">此处暂无法预览</p>'
    if kind == "environment":
        return "".join(_render_node(child, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for child in children)
    return _inline_html(node.review.raw_latex, project, side, children, diagnostics, node.review.source)


def _render_node(node: ParsedNode, project: ParsedProject, side: str, by_id: dict[str, ParsedNode], diagnostics: list[Diagnostic],
                 figure_assets: Mapping[tuple[str, str], str] | None = None,
                 sentence_highlights: Mapping[tuple[str, str], frozenset[int]] | None = None) -> str:
    kind = node.review.type
    if kind in {"inline_math", "citation", "reference"}:
        return ""  # 行内节点由父段落的 plasTeX DOM 呈现。
    tag = {"figure": "figure", "list_item": "li", "theorem": "aside", "bibliography_entry": "li"}.get(kind, "div")
    return (f'<{tag} class="review-node node-{_e(kind)}" id="{_e(_anchor(side, node.review.id))}" '
            f'data-node-id="{_e(node.review.id)}" data-side="{side}">'
            + _body(node, project, side, by_id, diagnostics, figure_assets, sentence_highlights) + f"</{tag}>")


def _side(project: ParsedProject, side: str, title: str, diagnostics: list[Diagnostic],
          figure_assets: Mapping[tuple[str, str], str] | None = None,
          extra_nodes: tuple[ReviewNode, ...] = (),
          sentence_highlights: Mapping[tuple[str, str], frozenset[int]] | None = None) -> str:
    by_id = project.by_id()
    roots = [node for node in project.nodes if node.review.parent_id is None]
    content = "".join(_render_node(node, project, side, by_id, diagnostics, figure_assets, sentence_highlights) for node in roots)
    if extra_nodes:
        comments = [node for node in extra_nodes if node.type == "comment"]
        bibliography = [node for node in extra_nodes if node.type == "bibliography_change"]
        if comments:
            content += '<h3>独立注释审阅</h3>' + "".join(
                f'<aside class="review-node node-comment" id="{_e(_anchor(side, node.id))}" '
                f'data-node-id="{_e(node.id)}" data-side="{side}">注释变更</aside>'
                for node in comments)
        if bibliography:
            content += '<h3>文献变更</h3>' + "".join(
                f'<aside class="review-node node-bibliography_change" id="{_e(_anchor(side, node.id))}" '
                f'data-node-id="{_e(node.id)}" data-side="{side}">引用键 {_e(node.plain_text or "")}</aside>'
                for node in bibliography)
    return f'<section class="preview-side" aria-label="{_e(title)}" data-side="{side}"><h2>{_e(title)}</h2>{content}</section>'


def render_preview(old: ParsedProject, new: ParsedProject, *,
                   figure_assets: Mapping[tuple[str, str], str] | None = None,
                   extra_nodes: Mapping[str, tuple[ReviewNode, ...]] | None = None,
                   sentence_highlights: Mapping[tuple[str, str], frozenset[int]] | None = None) -> PreviewResult:
    """返回双侧连续阅读视图与公共诊断，不写文件。"""
    diagnostics = [*old.diagnostics, *new.diagnostics]
    for project, side in ((old, "old"), (new, "new")):
        by_id = project.by_id()
        seen_citations: set[str] = set()
        for node in project.nodes:
            if node.review.type == "citation":
                for key in node.citations:
                    if key not in project.bibliography and key not in seen_citations:
                        seen_citations.add(key)
                        diagnostics.append(_missing_bibliography(key, project, side, node.review.source))
            if any(by_id[child].references and by_id[child].expanded_start >= node.expanded_start
                   and by_id[child].expanded_end <= node.expanded_end for child in node.review.child_ids):
                continue
            for key in node.references:
                if key not in project.labels:
                    diagnostics.append(Diagnostic("unresolved_reference", "info", f"交叉引用目标不存在：{key}",
                                                  source_old=node.review.source if side == "old" else None,
                                                  source_new=node.review.source if side == "new" else None))
    extra = extra_nodes or {}
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
.node-table{{overflow-x:auto}}.table-preview{{border-collapse:collapse;margin:.6rem 0;width:max-content;max-width:none}}
.table-preview td{{border:1px solid #ddd8cb;padding:.25rem .6rem;white-space:nowrap}}
.compiled-fragment{{max-width:100%;height:auto}}
.math-tex{{font-family:serif;white-space:pre-wrap;overflow-wrap:anywhere;visibility:hidden}}.math-tex.preview-unavailable{{visibility:visible}}.node-equation{{overflow-x:auto;text-align:center;margin:1rem 0}}
.preview-unavailable{{color:#6b3e27;background:#fff1e8;padding:.35rem .55rem}}
.citation,.cross-ref{{color:#315c86}}.unresolved-ref{{color:#9b3c26}}
.macro-placeholder{{background:#edf4ed;border-bottom:1px dotted #48734b}}
@media(max-width:800px){{main{{grid-template-columns:1fr}}.preview-side{{max-height:none}}}}
</style></head><body>
<header><h1>LaTeX 内容预览</h1><p class="notice">用于审阅正文内容，不代表最终编译版式。</p>
<p id="math-status" role="status">正在加载在线公式排版。</p></header>
<main>{_side(old, "old", "修改前", diagnostics, figure_assets, extra.get("old", ()), sentence_highlights)}{_side(new, "new", "修改后", diagnostics, figure_assets, extra.get("new", ()), sentence_highlights)}</main>
<script>
(function(){{
 window.MathJax={{startup:{{typeset:false}},tex:{{packages:{{'[+]':['ams','newcommand','boldsymbol']}}}}}};
 const status=document.getElementById('math-status');
 let settled=false;
 const formulas=Array.from(document.querySelectorAll('.math-tex'));
 window.reviewRenderDiagnostics=[];
 function unavailable(node, reason){{
   if(node.dataset.fallbackSrc){{const image=document.createElement('img');image.src=node.dataset.fallbackSrc;image.alt='公式预览';image.className='compiled-fragment';node.replaceChildren(image);}}
   else{{node.textContent='此处暂无法预览';node.classList.add('preview-unavailable');}}
   node.style.visibility='visible';node.dataset.renderError=String(reason);window.reviewRenderDiagnostics.push({{kind:'math',reason:String(reason)}});
 }}
 function failed(){{if(settled)return;settled=true;clearTimeout(timer);status.textContent='在线公式排版不可用。';status.dataset.state='failed';formulas.forEach(function(node){{unavailable(node,'公式引擎不可用');}});}}
 const timer=setTimeout(failed,10000);
 window.mathDependencyFailed=failed;
 window.mathDependencyReady=function(){{
   if(!window.MathJax || !MathJax.startup || !MathJax.startup.promise || !MathJax.tex2chtmlPromise){{failed();return;}}
   MathJax.startup.promise.then(async function(){{
     if(settled)return;
     clearTimeout(timer);
     let errors=0;
     for(const node of formulas){{
       try{{
         const rendered=await MathJax.tex2chtmlPromise(node.textContent,{{display:node.dataset.display==='true'}});
         if(rendered.querySelector('mjx-merror,[data-mjx-error],mtext[mathcolor="red"],mjx-mtext[style*="color: red"]'))throw new Error('公式语法或命令不受支持');
         node.replaceChildren(rendered);node.style.visibility='visible';
       }}catch(error){{errors++;unavailable(node,error && error.message || error);}}
     }}
     settled=true;
     status.textContent=errors ? '部分公式暂无法预览。' : '公式排版完成。';
     status.dataset.state=errors ? 'partial' : 'ready';
     if(window.dispatchEvent)window.dispatchEvent(new Event('review-math-ready'));
   }},failed);
 }};
}})();
</script>
<script async src="{_MATHJAX_URL}" onload="mathDependencyReady()" onerror="mathDependencyFailed()"></script>
</body></html>'''
    return PreviewResult(html, tuple(diagnostics))
