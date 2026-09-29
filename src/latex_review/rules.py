"""在两侧已展开源码上做保守的论文规则检查。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from .contract import Diagnostic, SourceLocation
from .structure import ParsedNode, ParsedProject, _COMMAND, _argument, _location, _masked
from .text_diff import CITATION_COMMANDS


_REF_COMMANDS = {"ref", "eqref", "autoref", "pageref"}
_VERBATIM = re.compile(r"\\begin\s*\{(verbatim\*?|Verbatim|lstlisting|minted)\}.*?\\end\s*\{\1\}", re.S)
_BIB_COMMAND = re.compile(r"\\(?:bibliography|addbibresource)(?![A-Za-z@])")
_RULE_NAMES = {
    "bibliography_key_unresolved": "引用键无法解析",
    "unresolved_reference": "交叉引用目标不存在",
    "duplicate_label": "标签重复定义",
    "figure_unreferenced": "图未被引用",
    "table_unreferenced": "表未被引用",
}
_STATUS_NAMES = {"existing": "既有", "new": "新增", "resolved": "已解决"}


@dataclass(frozen=True)
class _Site:
    key: str
    location: SourceLocation
    offset: int
    owner: ParsedNode | None = None


def _body_range(mask: str) -> tuple[int, int]:
    start = re.search(r"\\begin\s*\{document\}", mask)
    if start is None:
        return 0, len(mask)
    end = re.search(r"\\end\s*\{document\}", mask[start.end():])
    return start.end(), start.end() + end.start() if end else len(mask)


def _rule_mask(text: str) -> str:
    """屏蔽注释和逐字片段，保持每个原文下标及换行位置。"""
    chars = list(text)
    index = 0
    while index < len(text):
        if text[index] == "%":
            end = index
            while end < len(text) and text[end] not in "\r\n":
                end += 1
        elif text[index] == "\\":
            environment = _VERBATIM.match(text, index) if text.startswith(r"\begin", index) else None
            if environment:
                end = environment.end()
            elif text.startswith(r"\verb", index) and (index + 5 == len(text) or not text[index + 5].isalpha()):
                delimiter_at = index + 5 + (index + 5 < len(text) and text[index + 5] == "*")
                if delimiter_at >= len(text) or text[delimiter_at] in "\r\n":
                    index += 5
                    continue
                line_end = min((end for end in (text.find("\r", delimiter_at), text.find("\n", delimiter_at))
                                if end >= 0), default=len(text))
                closing = text.find(text[delimiter_at], delimiter_at + 1, line_end)
                end = closing + 1 if closing >= 0 else line_end
            else:
                command = _COMMAND.match(text, index)
                index = command.end() if command else index + 1
                continue
        else:
            index += 1
            continue
        for offset in range(index, end):
            if chars[offset] not in "\r\n":
                chars[offset] = " "
        index = end
    return "".join(chars)


def _sites(project: ParsedProject) -> tuple[dict[str, list[_Site]], dict[str, list[_Site]], dict[str, list[_Site]]]:
    masked = _rule_mask(project.expanded.text)
    start, end = _body_range(masked)
    labels: dict[str, list[_Site]] = defaultdict(list)
    citations: dict[str, list[_Site]] = defaultdict(list)
    references: dict[str, list[_Site]] = defaultdict(list)
    owners = [node for node in project.nodes if node.review.type in {"equation", "figure", "table"}]
    for match in _COMMAND.finditer(masked, start, end):
        command = match.group(1)
        if command not in {"label", *_REF_COMMANDS, *CITATION_COMMANDS}:
            continue
        argument = _argument(masked, match.end())
        if argument is None or argument[0] > end:
            continue
        owner = (min((node for node in owners if node.expanded_start <= match.start() < node.expanded_end),
                     key=lambda node: node.expanded_end - node.expanded_start, default=None)
                 if command == "label" else None)
        location = _location(project.expanded, project.expanded.origin_ranges(match.start(), argument[0]))
        target = labels if command == "label" else citations if command in CITATION_COMMANDS else references
        keys = argument[1].split(",") if command in CITATION_COMMANDS else (argument[1],)
        for key in (part.strip() for part in keys):
            if key:
                target[key].append(_Site(key, location, match.start(), owner))
    return labels, citations, references


def _bibliography_reliable(project: ParsedProject) -> bool:
    if any(item.code in {"bibliography_unreadable", "bibliography_unsupported"} for item in project.diagnostics):
        return False
    for issue in project.expanded.diagnostics:
        source = project.expanded.source_map.files.get(issue.file, "")
        if _BIB_COMMAND.search(source[issue.start:issue.end]):
            return False
    return True


def _findings(project: ParsedProject) -> dict[tuple[str, str], list[_Site]]:
    labels, citations, references = _sites(project)
    findings: dict[tuple[str, str], list[_Site]] = {}
    bibliography_reliable = _bibliography_reliable(project)
    for key, sites in citations.items():
        if bibliography_reliable and key not in project.bibliography and f"bib:{key}" not in project.labels:
            findings[("bibliography_key_unresolved", key)] = sites
    for key, sites in references.items():
        if key not in labels:
            findings[("unresolved_reference", key)] = sites
    for key, sites in labels.items():
        if len(sites) > 1:
            findings[("duplicate_label", key)] = sites
        if len(sites) == 1 and key not in references:
            kind = sites[0].owner.review.type if sites[0].owner else None
            if kind in {"figure", "table"}:
                findings[(f"{kind}_unreferenced", key)] = sites
    return findings


def _location_text(location: SourceLocation) -> str:
    if location.file is None or location.start_line is None:
        return f"来源未知（{location.uncertainty_reason or '无法定位'}）"
    return f"{location.file}:{location.start_line}:{location.start_column or 1}"


def _rule_diagnostic(code: str, key: str, old: list[_Site], new: list[_Site]) -> Diagnostic:
    status = "existing" if old and new else "new" if new else "resolved"
    old_sources = tuple(site.location for site in old)
    new_sources = tuple(site.location for site in new)
    basis = {
        "bibliography_key_unresolved": "在已读取的参考文献条目及手写文献键中未找到该键",
        "unresolved_reference": "在该侧正文标签定义中未找到该键",
        "duplicate_label": "同一侧有多处标签定义",
        "figure_unreferenced": "唯一图标签未见受支持的交叉引用",
        "table_unreferenced": "唯一表标签未见受支持的交叉引用",
    }[code]
    evidence = (f"键：{key}", basis,
                *(f"旧侧第 {index} 处：{_location_text(site.location)}" for index, site in enumerate(old, 1)),
                *(f"新侧第 {index} 处：{_location_text(site.location)}" for index, site in enumerate(new, 1)))
    severity = "warning" if code in {"bibliography_key_unresolved", "duplicate_label"} else "info"
    return Diagnostic(code, severity, f"{_STATUS_NAMES[status]}：{_RULE_NAMES[code]}（{key}）",
                      old_sources[0] if old else None, new_sources[0] if new else None,
                      status, evidence, old_sources, new_sources)


def _equations(project: ParsedProject) -> dict[str, tuple[int, ParsedNode, str | None]]:
    labels, _, _ = _sites(project)
    equation_nodes = [node for node in project.nodes if node.review.type == "equation" and
                      re.match(r"\\begin\s*\{equation\}", node.review.raw_latex)]
    result = {}
    for key, sites in labels.items():
        if len(sites) != 1 or sites[0].owner not in equation_nodes:
            continue
        node = sites[0].owner
        raw = _masked(node.review.raw_latex)
        tags = []
        for match in _COMMAND.finditer(raw):
            if match.group(1) == "tag":
                argument = _argument(raw, match.end())
                if argument:
                    tags.append(argument[1].strip())
        tag = tags[0] if len(tags) == 1 and tags[0] and "\\" not in tags[0] else None
        result[key] = (equation_nodes.index(node), node, tag)
    return result


def check_rules(old: ParsedProject, new: ParsedProject) -> tuple[Diagnostic, ...]:
    """合并两侧规则命中；只有显式单值标签可证明公式显示编号变化。"""
    before, after = _findings(old), _findings(new)
    bibliography_comparable = _bibliography_reliable(old) and _bibliography_reliable(new)
    diagnostics = [_rule_diagnostic(code, key, before.get((code, key), []), after.get((code, key), []))
                   for code, key in sorted(before.keys() | after.keys())
                   if code != "bibliography_key_unresolved" or bibliography_comparable]
    old_equations, new_equations = _equations(old), _equations(new)
    for key in sorted(old_equations.keys() & new_equations.keys()):
        old_index, old_node, old_tag = old_equations[key]
        new_index, new_node, new_tag = new_equations[key]
        if old_tag is not None and new_tag is not None:
            if old_tag == new_tag:
                continue
            code, severity = "equation_number_changed", "warning"
            message = f"新增：公式 {key} 的显式编号由 {old_tag} 变为 {new_tag}"
        elif old_index != new_index or old_tag != new_tag or old_node.review.section_path != new_node.review.section_path:
            code, severity = "equation_number_unknown", "info"
            message = f"新增：公式 {key} 的最终编号未知"
        else:
            continue
        old_source, new_source = old_node.review.source, new_node.review.source
        diagnostics.append(Diagnostic(code, severity, message, old_source, new_source, "new",
                                      (f"公式标签：{key}", f"旧侧显式编号：{old_tag or '未知'}",
                                       f"新侧显式编号：{new_tag or '未知'}",
                                       f"旧侧结构序位：{old_index + 1}；新侧结构序位：{new_index + 1}"),
                                      (old_source,), (new_source,)))
    return tuple(diagnostics)
