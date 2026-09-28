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
    for index in range(max(len(old_sites), len(new_sites))):
        old_site = old_sites[index] if index < len(old_sites) else None
        new_site = new_sites[index] if index < len(new_sites) else None
        old_keys = set(old_site.citations) if old_site else set()
        new_keys = set(new_site.citations) if new_site else set()
        if old_keys == new_keys:
            continue
        removed, added = sorted(old_keys - new_keys), sorted(new_keys - old_keys)
        summary = f"第 {index + 1} 处引用：删 {', '.join(removed) or '无'}；增 {', '.join(added) or '无'}"
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
