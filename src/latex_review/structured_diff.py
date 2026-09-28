"""公式、引用、图和表的整体差异；行内明细归属父结构。"""

from __future__ import annotations

from difflib import SequenceMatcher
import re

from .contract import ChangeDetail, Diagnostic
from .structure import ParsedNode, ParsedProject, _argument, _masked


_MATH_TOKEN = re.compile(r"\\(?:[A-Za-z@]+|.)|[A-Za-z]+|[0-9]+|[^\s]")
_META_COMMAND = re.compile(r"\\(?:label|tag\*|tag|notag|nonumber)(?![A-Za-z@])")


def _detail(details: list[ChangeDetail], category: str, kind: str, old: str | None, new: str | None,
            summary: str, left: ParsedNode | None = None, right: ParsedNode | None = None) -> None:
    details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", category, kind, old, new, summary,
                                left.review.source if left else None, right.review.source if right else None))


def _kind(left: object | None, right: object | None) -> str:
    return "modified" if left is not None and right is not None else "removed" if left is not None else "added"


def _site_context(parent: ParsedNode | None, sites: list[ParsedNode], by_id: dict[str, ParsedNode]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    if parent is None:
        return []
    raw = list(parent.review.raw_latex)
    for child_id in parent.review.child_ids:
        child = by_id[child_id]
        if child.review.type in {"citation", "inline_math", "reference"}:
            start = child.expanded_start - parent.expanded_start
            end = child.expanded_end - parent.expanded_start
            raw[start:end] = " " * (end - start)
    text = "".join(raw)
    result = []
    for site in sites:
        start = site.expanded_start - parent.expanded_start
        end = site.expanded_end - parent.expanded_start
        before = re.findall(r"\w+|[^\s\w]", text[:start])[-2:]
        after = re.findall(r"\w+|[^\s\w]", text[end:])[:2]
        result.append((tuple(before), tuple(after)))
    return result


def align_inline_sites(left: ParsedNode | None, right: ParsedNode | None,
                       old_project: ParsedProject, new_project: ParsedProject, kind: str) -> list[tuple[int | None, int | None]]:
    """以相邻正文为主、内容为辅，对齐段内站点；插删不挪用相邻来源。"""
    old_by_id, new_by_id = old_project.by_id(), new_project.by_id()
    old_sites = [old_by_id[child] for child in left.review.child_ids if old_by_id[child].review.type == kind] if left else []
    new_sites = [new_by_id[child] for child in right.review.child_ids if new_by_id[child].review.type == kind] if right else []
    old_context = _site_context(left, old_sites, old_by_id)
    new_context = _site_context(right, new_sites, new_by_id)

    def identity(node: ParsedNode):
        if kind == "citation":
            return tuple(sorted(set(node.citations)))
        tokens, metadata, ok = _math_parts(node.review.raw_latex)
        return tokens if ok else node.review.raw_latex

    def score(i: int, j: int) -> float:
        if kind == "citation":
            old_before, old_after = old_context[i]
            new_before, new_after = new_context[j]
            same_left = bool(old_before and new_before and old_before[-1] == new_before[-1])
            same_right = bool(old_after and new_after and old_after[0] == new_after[0])
            empty_both = not (old_before or old_after or new_before or new_after)
            if not (same_left or same_right or empty_both):
                return -1e6  # 同键但两侧相邻正文均变，不能视为原位置未变。
        context = sum(SequenceMatcher(None, old_context[i][side], new_context[j][side], autojunk=False).ratio()
                      for side in (0, 1))
        return .45 * context + (.3 if identity(old_sites[i]) == identity(new_sites[j]) else 0)

    count_old, count_new = len(old_sites), len(new_sites)
    values = [[0.0] * (count_new + 1) for _ in range(count_old + 1)]
    actions = [[""] * (count_new + 1) for _ in range(count_old + 1)]
    for i in range(1, count_old + 1):
        values[i][0], actions[i][0] = -.2 * i, "removed"
    for j in range(1, count_new + 1):
        values[0][j], actions[0][j] = -.2 * j, "added"
    for i in range(1, count_old + 1):
        for j in range(1, count_new + 1):
            candidates = ((values[i - 1][j - 1] + score(i - 1, j - 1), "paired"),
                          (values[i - 1][j] - .2, "removed"),
                          (values[i][j - 1] - .2, "added"))
            values[i][j], actions[i][j] = max(candidates, key=lambda item: item[0])
    aligned = []
    i, j = count_old, count_new
    while i or j:
        action = actions[i][j]
        if action == "paired":
            aligned.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif action == "removed":
            aligned.append((i - 1, None))
            i -= 1
        else:
            aligned.append((None, j - 1))
            j -= 1
    return list(reversed(aligned))


def _math_parts(raw: str) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """剥离定界符及编号元信息；不对未知结构编造精细数学差异。"""
    mask = _masked(raw)
    if raw.startswith(r"\begin{"):
        opening = re.match(r"\s*\\begin\s*\{[^{}]+\}", mask)
        closing = re.search(r"\\end\s*\{[^{}]+\}\s*$", mask)
        if not opening or not closing:
            return (), (), False
        body = raw[opening.end():closing.start()]
    elif raw.startswith(("$$", r"\[", r"\(", "$")):
        opening, closing = (("$$", "$$") if raw.startswith("$$") else
                            (r"\[", r"\]") if raw.startswith(r"\[") else
                            (r"\(", r"\)") if raw.startswith(r"\(") else ("$", "$"))
        if not raw.endswith(closing):
            return (), (), False
        body = raw[len(opening):-len(closing)]
    else:
        return (), (), False
    mask = _masked(body)
    metadata: list[str] = []
    chunks: list[str] = []
    cursor = 0
    for command in _META_COMMAND.finditer(mask):
        chunks.append(body[cursor:command.start()])
        end = command.end()
        if command.group() not in (r"\notag", r"\nonumber"):
            argument = _argument(mask, end)
            if argument is None:
                return (), (), False
            end = argument[0]
        metadata.append(" ".join(body[command.start():end].split()))
        cursor = end
    chunks.append(body[cursor:])
    content = "".join(chunks)
    # 词元摘要只对括号平衡的内容可靠；失败时仍保留双侧原文。
    depth = 0
    for token in _MATH_TOKEN.findall(_masked(content)):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        if depth < 0:
            return (), tuple(metadata), False
    if depth:
        return (), tuple(metadata), False
    return tuple(_MATH_TOKEN.findall(_masked(content))), tuple(metadata), True


def equation_details(left: ParsedNode | None, right: ParsedNode | None,
                     diagnostics: list[Diagnostic], *, unsupported: bool = False) -> list[ChangeDetail]:
    details: list[ChangeDetail] = []
    old_raw = left.review.raw_latex if left else None
    new_raw = right.review.raw_latex if right else None
    if old_raw is not None and old_raw == new_raw:
        return details
    old_tokens, old_meta, old_ok = _math_parts(old_raw) if old_raw else ((), (), True)
    new_tokens, new_meta, new_ok = _math_parts(new_raw) if new_raw else ((), (), True)
    if unsupported or not old_ok or not new_ok:
        _detail(details, "equation", _kind(left, right), old_raw, new_raw, "公式词元无法可靠解析；保留两侧原文", left, right)
        diagnostics.append(Diagnostic("equation_diff_fallback", "warning", "公式词元无法可靠解析；已保留双侧原文",
                                      left.review.source if left else None, right.review.source if right else None))
        return details
    if old_tokens != new_tokens:
        matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
        removed = [token for op, a, b, _, _ in matcher.get_opcodes() if op != "equal" for token in old_tokens[a:b]]
        added = [token for op, _, _, c, d in matcher.get_opcodes() if op != "equal" for token in new_tokens[c:d]]
        summary = f"数学内容词元：{' '.join(removed) or '∅'} → {' '.join(added) or '∅'}"
        _detail(details, "equation", _kind(left, right), old_raw, new_raw, summary, left, right)
    if old_meta != new_meta:
        _detail(details, "equation", _kind(left, right), ", ".join(old_meta) or None,
                ", ".join(new_meta) or None, "公式标签或编号标记变化（数学内容未由此判定改变）", left, right)
    return details


def citation_details(left: ParsedNode | None, right: ParsedNode | None,
                     old_project: ParsedProject, new_project: ParsedProject) -> list[ChangeDetail]:
    """同一父结构内按引用位置比较集合，重复键与重复出现均保留。"""
    old_by_id, new_by_id = old_project.by_id(), new_project.by_id()
    old_sites = [old_by_id[child] for child in left.review.child_ids if old_by_id[child].review.type == "citation"] if left else []
    new_sites = [new_by_id[child] for child in right.review.child_ids if new_by_id[child].review.type == "citation"] if right else []
    details: list[ChangeDetail] = []
    for old_index, new_index in align_inline_sites(left, right, old_project, new_project, "citation"):
        old_site = old_sites[old_index] if old_index is not None else None
        new_site = new_sites[new_index] if new_index is not None else None
        old_keys = set(old_site.citations) if old_site else set()
        new_keys = set(new_site.citations) if new_site else set()
        if old_keys == new_keys:
            continue
        removed, added = sorted(old_keys - new_keys), sorted(new_keys - old_keys)
        position = old_index if old_index is not None else new_index
        summary = f"第 {position + 1} 处引用：删 {', '.join(removed) or '无'}；增 {', '.join(added) or '无'}"
        _detail(details, "citation", _kind(old_site, new_site), ", ".join(sorted(old_keys)) or None,
                ", ".join(sorted(new_keys)) or None, summary, old_site, new_site)
    return details


def _caption(raw: str) -> str | None:
    mask = _masked(raw)
    command = re.search(r"\\caption(?![A-Za-z@])", mask)
    argument = _argument(mask, command.end()) if command else None
    return raw[argument[2]:argument[0] - 1] if argument else None


def figure_details(left: ParsedNode | None, right: ParsedNode | None) -> list[ChangeDetail]:
    details: list[ChangeDetail] = []
    fields = (
        ("资源路径", left.assets if left else (), right.assets if right else ()),
        ("图注", _caption(left.review.raw_latex) if left else None, _caption(right.review.raw_latex) if right else None),
        ("标签", left.labels if left else (), right.labels if right else ()),
    )
    for name, old, new in fields:
        if old == new:
            continue
        old_value = ", ".join(old) if isinstance(old, tuple) else old
        new_value = ", ".join(new) if isinstance(new, tuple) else new
        _detail(details, "figure", _kind(old if old else None, new if new else None), old_value or None,
                new_value or None, f"图{name}变化", left, right)
    return details


def table_details(left: ParsedNode | None, right: ParsedNode | None) -> list[ChangeDetail]:
    old = left.review.raw_latex if left else None
    new = right.review.raw_latex if right else None
    if old is not None and new is not None and " ".join(_masked(old).split()) == " ".join(_masked(new).split()):
        return []
    details: list[ChangeDetail] = []
    _detail(details, "table", _kind(left, right), old, new,
            "表格整体差异；双侧原文可查看，不提供单元格定位", left, right)
    return details
