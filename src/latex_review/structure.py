"""plasTeX 识别宏；字符扫描构建有精确来源跨度的审阅结构树。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import logging
import re

from plasTeX.TeX import TeX

from .contract import Diagnostic, ReviewNode, SourceLocation
from .source_map import MappedRange
from .sources import ExpandedProject, SourceIssue


_COMMAND = re.compile(r"\\([A-Za-z@]+|.)")
_ENV = re.compile(r"\\(begin|end)\s*\{([A-Za-z@*]+)\}")
_HEADING = {"part": 0, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}
_MATH_ENV = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*", "displaymath", "math"}
_LIST_ENV = {"itemize", "enumerate", "description"}
_TABLE_ENV = {"table", "table*", "tabular", "tabular*", "longtable"}
_FIGURE_ENV = {"figure", "figure*"}
_THEOREM_ENV = {"theorem", "lemma", "proposition", "corollary", "definition", "remark", "proof", "example", "claim"}
_SAFE_COMMANDS = {
    "documentclass", "usepackage", "begin", "end", "label", "ref", "eqref", "autoref", "pageref", "cite", "citep", "citet", "nocite",
    "includegraphics", "caption", "centering", "item", "bibitem", "bibliography", "bibliographystyle", "addbibresource", "printbibliography",
    "input", "include", "graphicspath", "textbf", "textit", "emph", "texttt", "underline", "footnote", "url", "href", "hfill",
    "small", "large", "Large", "normalsize", "itshape", "bfseries", "textsc", "noindent", "newline", "newpage", "clearpage",
    "maketitle", "title", "author", "date", "abstract", "thanks", "today", "and", "quad", "qquad", "ldots", "cdots",
    "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "newenvironment", "renewenvironment", "provideenvironment",
    "newtheorem", "def", "gdef", "edef", "xdef", "setlength", "setcounter", "numberwithin", "tableofcontents",
}


@dataclass(frozen=True)
class ParsedNode:
    review: ReviewNode
    expanded_start: int
    expanded_end: int
    origins: tuple[MappedRange, ...]
    labels: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ParsedProject:
    expanded: ExpandedProject
    nodes: tuple[ParsedNode, ...]
    diagnostics: tuple[Diagnostic, ...]
    labels: dict[str, str]

    @property
    def review_nodes(self) -> tuple[ReviewNode, ...]:
        return tuple(node.review for node in self.nodes)

    def by_id(self) -> dict[str, ParsedNode]:
        return {node.review.id: node for node in self.nodes}


def _masked(text: str) -> str:
    """屏蔽注释，保留全部字符下标；转义百分号不开始注释。"""
    chars = list(text)
    i = 0
    while i < len(text):
        if text[i] == "%":
            backslashes = 0
            k = i - 1
            while k >= 0 and text[k] == "\\":
                backslashes += 1
                k -= 1
            if backslashes % 2:
                i += 1
                continue
            j = i
            while j < len(text) and text[j] not in "\r\n":
                chars[j] = " "
                j += 1
            i = j
        else:
            i += 1
    return "".join(chars)


def _group_end(mask: str, start: int, opening: str = "{", closing: str = "}") -> int | None:
    if start >= len(mask) or mask[start] != opening:
        return None
    depth = 1
    i = start + 1
    while i < len(mask):
        if mask[i] == "\\" and i + 1 < len(mask) and mask[i + 1] in (opening, closing):
            i += 2
            continue
        if mask[i] == opening:
            depth += 1
        elif mask[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _argument(mask: str, start: int) -> tuple[int, str, int] | None:
    i = start
    if i < len(mask) and mask[i] == "*":
        i += 1
    while i < len(mask) and mask[i].isspace():
        i += 1
    if i < len(mask) and mask[i] == "[":
        i = _group_end(mask, i, "[", "]") or i
        while i < len(mask) and mask[i].isspace():
            i += 1
    end = _group_end(mask, i)
    return None if end is None else (end, mask[i + 1:end - 1], i + 1)


def _environment_end(mask: str, start: int, limit: int) -> tuple[int, int] | None:
    opening = _ENV.match(mask, start)
    if opening is None or opening.group(1) != "begin":
        return None
    stack = [opening.group(2)]
    for match in _ENV.finditer(mask, opening.end(), limit):
        if match.group(1) == "begin":
            stack.append(match.group(2))
        elif stack and match.group(2) == stack[-1]:
            stack.pop()
            if not stack:
                return match.start(), match.end()
    return None


def _math_end(mask: str, start: int, limit: int) -> int | None:
    delimiter = "$$" if mask.startswith("$$", start) else "$"
    i = start + len(delimiter)
    while i < limit:
        if mask[i] == "\\":
            i += 2
        elif mask.startswith(delimiter, i):
            return i + len(delimiter)
        else:
            i += 1
    return None


def _location(project: ExpandedProject, origins: tuple[MappedRange, ...]) -> SourceLocation:
    if not origins:
        return SourceLocation(None, None, None, confidence=0, uncertainty_reason="无法对应原始源码")
    first, last = origins[0].origin, origins[-1].origin
    exact = all(item.origin.confidence == "exact" for item in origins)
    contiguous = all(a.origin.file == b.origin.file and a.origin.include_instance == b.origin.include_instance
                     and a.origin.end == b.origin.start for a, b in zip(origins, origins[1:]))
    if first.file != last.file or not contiguous:
        return SourceLocation(None, None, None, confidence=0.3, uncertainty_reason="结构跨越多个原文片段；完整片段见 origins")
    return SourceLocation(first.file, first.start_line, last.end_line, first.start_column, last.end_column,
                          1.0 if exact else 0.3, None if exact else "来源展开不确定")


def _issue_location(project: ExpandedProject, issue: SourceIssue) -> SourceLocation:
    content = project.source_map.files[issue.file]
    before = content[:issue.start]
    through = content[:issue.end]
    line = len(re.findall(r"\r\n|\r|\n", before)) + 1
    end_line = len(re.findall(r"\r\n|\r|\n", through)) + 1
    start_col = len(re.split(r"\r\n|\r|\n", before)[-1]) + 1
    end_col = len(re.split(r"\r\n|\r|\n", through)[-1]) + 1
    return SourceLocation(issue.file, line, end_line, start_col, end_col, 0.3,
                          "来源展开不确定" if issue.certainty != "exact" else None)


def _diagnostic(code: str, message: str, location: SourceLocation, side: str) -> Diagnostic:
    return Diagnostic(code, "warning", message,
                      source_old=location if side == "old" else None,
                      source_new=location if side == "new" else None)


def _metadata(raw: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    def groups(pattern: str) -> tuple[str, ...]:
        values = []
        for match in re.finditer(pattern, _masked(raw)):
            values.extend(part.strip() for part in match.group(1).split(",") if part.strip())
        return tuple(dict.fromkeys(values))
    return (groups(r"\\label\s*\{([^{}]+)\}"), groups(r"\\(?:cite|citep|citet|nocite)(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}"),
            groups(r"\\(?:ref|eqref|autoref|pageref)\s*\{([^{}]+)\}"),
            groups(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}"))


def _normalize(raw: str) -> str:
    return " ".join(_masked(raw).split())


def _plain(raw: str) -> str:
    value = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", "", _masked(raw))
    return " ".join(value.replace("{", "").replace("}", "").split())


def _plastex_unknown(text: str) -> tuple[set[str], str | None]:
    tex = TeX()
    try:
        tex.input(text)
        with _quiet_plastex():
            document = tex.parse()
    except Exception as exc:  # 局部扫描仍可保留原文。
        return set(), str(exc)
    unknown: set[str] = set()
    def walk(node: object) -> None:
        if type(node).__module__ == "plasTeX.Context":
            unknown.add(getattr(node, "nodeName", ""))
        for child in getattr(node, "childNodes", ()):
            walk(child)
    walk(document)
    return unknown, None


def _safe_plastex_input(project: ExpandedProject) -> str:
    """已展开内容不再由 plasTeX 读取外部输入文件。"""
    chars = list(project.text)
    for segment in project.source_map.segments:
        if segment.confidence != "exact":
            for offset in range(segment.expanded_start, segment.expanded_end):
                if chars[offset] not in "\r\n":
                    chars[offset] = " "
    mask = _masked(project.text)
    for match in re.finditer(r"\\(?:input|include)(?![A-Za-z@])", project.text):
        arg = _argument(mask, match.end())
        finish = arg[0] if arg else match.end()
        for offset in range(match.start(), finish):
            if chars[offset] not in "\r\n":
                chars[offset] = " "
    return "".join(chars)


class _quiet_plastex:
    def __enter__(self):
        self.log = logging.getLogger("plasTeX")
        self.level = self.log.level
        self.log.setLevel(logging.ERROR)

    def __exit__(self, *_):
        self.log.setLevel(self.level)


class _Builder:
    def __init__(self, project: ExpandedProject, unknown: set[str], theorem_envs: set[str]):
        self.project, self.text, self.mask = project, project.text, _masked(project.text)
        self.unknown, self.theorem_envs = unknown, theorem_envs
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self.counts: dict[str, int] = {}
        self.diagnostics: list[Diagnostic] = []
        self.sections: list[tuple[int, str, str]] = []

    def add(self, kind: str, start: int, end: int, parent: str | None, *, title: str = "", stable_label: str = "") -> str:
        raw = self.text[start:end]
        origins = self.project.origin_ranges(start, end)
        location = _location(self.project, origins)
        labels, citations, references, assets = _metadata(raw)
        if stable_label:
            labels = tuple(dict.fromkeys((stable_label, *labels)))
        if kind == "bibliography_entry":
            bibitem = re.match(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^{}]+)\}", raw)
            if bibitem:
                labels = tuple(dict.fromkeys((f"bib:{bibitem.group(1)}", *labels)))
        path = tuple(item[2] for item in self.sections)
        if kind in _HEADING:
            path += (title,)
        fingerprint = sha1((kind + "|" + "/".join(path) + "|" + (labels[0] if labels else _normalize(raw))).encode()).hexdigest()[:16]
        base = f"{kind}-{fingerprint}"
        self.counts[base] = self.counts.get(base, 0) + 1
        node_id = base if self.counts[base] == 1 else f"{base}-{self.counts[base]}"
        local: list[Diagnostic] = []
        if location.confidence < 1:
            local.append(_diagnostic("low_confidence_source", "结构来源位置不确定；已保留全部原文片段", location, self.project.source.side))
        if kind == "fallback":
            local.append(_diagnostic("unknown_latex", "未知或无法解析的 LaTeX 已保留原文", location, self.project.source.side))
        self.diagnostics.extend(local)
        self.items[node_id] = dict(kind=kind, start=start, end=end, parent=parent, children=[], path=path,
                                   origins=origins, location=location, labels=labels, citations=citations,
                                   references=references, assets=assets, diagnostics=local)
        self.order.append(node_id)
        if parent:
            self.items[parent]["children"].append(node_id)
        return node_id

    def _inline(self, parent: str, start: int, end: int) -> None:
        i = start
        while i < end:
            if self.mask.startswith("\\(", i):
                close = self.mask.find("\\)", i + 2, end)
                if close >= 0:
                    self.add("inline_math", i, close + 2, parent)
                    i = close + 2
                    continue
            if self.mask[i] == "$":
                close = _math_end(self.mask, i, end)
                if close:
                    self.add("inline_math" if not self.mask.startswith("$$", i) else "equation", i, close, parent)
                    i = close
                    continue
            match = _COMMAND.match(self.mask, i)
            if match:
                name = match.group(1)
                arg = _argument(self.mask, match.end())
                if name in {"cite", "citep", "citet", "ref", "eqref", "autoref", "pageref"} and arg:
                    self.add("citation" if name.startswith("cite") else "reference", i, arg[0], parent)
                    i = arg[0]
                    continue
                uncertain = (self.project.origin_ranges(i, min(arg[0] if arg else match.end(), end))
                             if i < end else ())
                if (name in self.unknown and name not in _SAFE_COMMANDS and name not in self.theorem_envs) or any(
                    part.origin.confidence != "exact" for part in uncertain
                ):
                    finish = arg[0] if arg else match.end()
                    self.add("fallback", i, finish, parent)
                    i = finish
                    continue
                i = match.end()
            else:
                i += 1

    def _flush(self, start: int, end: int, parent: str | None) -> None:
        while start < end and self.mask[start].isspace():
            start += 1
        while end > start and self.mask[end - 1].isspace():
            end -= 1
        if self.mask[start:end].strip():
            node = self.add("paragraph", start, end, parent)
            self._inline(node, start, end)

    def _split_items(self, start: int, end: int, parent: str, marker: str, kind: str) -> None:
        matches = []
        i = start
        while i < end:
            environment = _ENV.match(self.mask, i)
            if environment and environment.group(1) == "begin":
                extent = _environment_end(self.mask, i, end)
                if extent:
                    i = extent[1]
                    continue
            command = _COMMAND.match(self.mask, i)
            if command and command.group(1) == marker:
                matches.append((i, command.end()))
                i = command.end()
            elif command:
                arg = _argument(self.mask, command.end())
                i = arg[0] if arg else command.end()
            else:
                i += 1
        for index, (position, after) in enumerate(matches):
            finish = matches[index + 1][0] if index + 1 < len(matches) else end
            item = self.add(kind, position, finish, parent)
            if kind == "bibliography_entry":
                self._inline(item, after, finish)
            else:
                self.scan(after, finish, item, sections=False)
        if not matches and self.mask[start:end].strip():
            self.scan(start, end, parent, sections=False)

    def _unknown_children(self, parent: str, start: int, end: int) -> None:
        i = start
        while i < end:
            command = _COMMAND.match(self.mask, i)
            if command:
                name = command.group(1)
                if name in self.unknown and name not in _SAFE_COMMANDS:
                    arg = _argument(self.mask, command.end())
                    finish = arg[0] if arg else command.end()
                    occupied = any(self.items[child]["start"] <= i < self.items[child]["end"]
                                   for child in self.items[parent]["children"])
                    if not occupied:
                        self.add("fallback", i, finish, parent)
                    i = finish
                else:
                    i = command.end()
            else:
                i += 1

    def scan(self, start: int, end: int, parent: str | None = None, *, sections: bool = True) -> None:
        i, pending = start, start
        while i < end:
            blank = re.match(r"(?:[ \t]*\r?\n){2,}", self.mask[i:end])
            if blank:
                self._flush(pending, i, parent)
                i += blank.end()
                pending = i
                continue
            env = _ENV.match(self.mask, i)
            if env and env.group(1) == "begin":
                extent = _environment_end(self.mask, i, end)
                if extent:
                    name = env.group(2)
                    if name == "document":
                        self._flush(pending, i, parent)
                        self.scan(env.end(), extent[0], parent, sections=sections)
                        i = extent[1]
                        pending = i
                        continue
                    self._flush(pending, i, parent)
                    kind = ("equation" if name in _MATH_ENV else "list" if name in _LIST_ENV else
                            "figure" if name in _FIGURE_ENV else "table" if name in _TABLE_ENV else
                            "bibliography" if name == "thebibliography" else "theorem" if name in self.theorem_envs else
                            "fallback" if name in self.unknown else "environment")
                    node = self.add(kind, i, extent[1], parent)
                    if kind == "list":
                        self._split_items(env.end(), extent[0], node, "item", "list_item")
                    elif kind == "bibliography":
                        self._split_items(env.end(), extent[0], node, "bibitem", "bibliography_entry")
                    elif kind in {"theorem", "environment"}:
                        self.scan(env.end(), extent[0], node, sections=False)
                    elif kind in {"figure", "table"}:
                        caption = re.search(r"\\caption(?:\s*\[[^\]]*\])?\s*\{", self.mask[env.end():extent[0]])
                        if caption:
                            opening = env.end() + caption.end() - 1
                            closing = _group_end(self.mask, opening)
                            if closing:
                                self._inline(node, opening + 1, closing - 1)
                        self._unknown_children(node, env.end(), extent[0])
                    elif kind == "equation":
                        self._unknown_children(node, env.end(), extent[0])
                    i = extent[1]
                    pending = i
                    continue
            if self.mask.startswith("\\[", i):
                close = self.mask.find("\\]", i + 2, end)
                if close >= 0:
                    self._flush(pending, i, parent)
                    self.add("equation", i, close + 2, parent)
                    i = close + 2
                    pending = i
                    continue
            if self.mask.startswith("$$", i):
                close = _math_end(self.mask, i, end)
                if close:
                    self._flush(pending, i, parent)
                    self.add("equation", i, close, parent)
                    i = close
                    pending = i
                    continue
            command = _COMMAND.match(self.mask, i)
            if command:
                name = command.group(1)
                if sections and name in _HEADING:
                    arg = _argument(self.mask, command.end())
                    if arg:
                        self._flush(pending, i, parent)
                        level = _HEADING[name]
                        while self.sections and self.sections[-1][0] >= level:
                            self.sections.pop()
                        parent = self.sections[-1][1] if self.sections else None
                        title = arg[1].strip()
                        label_match = re.match(r"\s*\\label\s*\{([^{}]+)\}", self.mask[arg[0]:end])
                        label = label_match.group(1) if label_match else ""
                        node = self.add(name, i, arg[0], parent, title=title, stable_label=label)
                        self._inline(node, arg[2], arg[0] - 1)
                        self.sections.append((level, node, title))
                        parent = node
                        i = arg[0]
                        pending = i
                        continue
                if name in {"bibliography", "printbibliography"}:
                    arg = _argument(self.mask, command.end())
                    self._flush(pending, i, parent)
                    finish = arg[0] if arg else command.end()
                    self.add("bibliography", i, finish, parent)
                    i = finish
                    pending = i
                    continue
                arg = _argument(self.mask, command.end())
                i = arg[0] if arg else command.end()
                continue
            if self.mask[i] == "{" and (close := _group_end(self.mask, i)):
                i = close
            elif self.mask[i] == "$" and (close := _math_end(self.mask, i, end)):
                i = close
            else:
                i += 1
        self._flush(pending, end, parent)

    def finish(self) -> ParsedProject:
        source_diagnostics = [_diagnostic(issue.code, issue.message, _issue_location(self.project, issue), issue.side)
                              for issue in self.project.diagnostics]
        self.diagnostics.extend(source_diagnostics)
        nodes = []
        labels: dict[str, str] = {}
        for node_id in self.order:
            item = self.items[node_id]
            raw = self.text[item["start"]:item["end"]]
            attached = list(item["diagnostics"])
            for issue, diagnostic in zip(self.project.diagnostics, source_diagnostics):
                if any(origin.origin.file == issue.file and origin.origin.start < issue.end and issue.start < origin.origin.end
                       for origin in item["origins"]):
                    attached.append(diagnostic)
            review = ReviewNode(node_id, item["kind"], raw, item["parent"], tuple(item["children"]),
                                item["path"], item["location"], _normalize(raw), _plain(raw))
            nodes.append(ParsedNode(review, item["start"], item["end"], item["origins"], item["labels"],
                                    item["citations"], item["references"], item["assets"], tuple(attached)))
            for label in item["labels"]:
                labels.setdefault(label, node_id)
        return ParsedProject(self.project, tuple(nodes), tuple(self.diagnostics), labels)


def parse_project(project: ExpandedProject) -> ParsedProject:
    """解析一侧已展开项目；节点保留精确原文和全部来源片段。"""
    unknown, error = _plastex_unknown(_safe_plastex_input(project))
    theorem_envs = set(_THEOREM_ENV)
    theorem_envs.update(re.findall(r"\\newtheorem\*?\s*\{([^{}]+)\}", _masked(project.text)))
    if error:
        unknown.update(match.group(1) for match in _COMMAND.finditer(_masked(project.text))
                       if match.group(1) not in _SAFE_COMMANDS and match.group(1) not in _HEADING)
        known_environments = _MATH_ENV | _LIST_ENV | _TABLE_ENV | _FIGURE_ENV | theorem_envs | {"document", "thebibliography"}
        unknown.update(match.group(2) for match in _ENV.finditer(_masked(project.text))
                       if match.group(2) not in known_environments)
    builder = _Builder(project, unknown, theorem_envs)
    if error:
        location = SourceLocation(None, None, None, confidence=0, uncertainty_reason="plasTeX 解析失败")
        builder.diagnostics.append(_diagnostic("plastex_parse_error", f"plasTeX 解析失败：{error}", location, project.source.side))
    document = re.search(r"\\begin\s*\{document\}", builder.mask)
    if document:
        extent = _environment_end(builder.mask, document.start(), len(builder.mask))
        builder.scan(document.end(), extent[0] if extent else len(builder.mask))
    else:
        builder.scan(0, len(project.text))
    return builder.finish()
