"""把稳定匹配、正文词元差异和可选注释审阅汇入公共契约。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

from .contract import ChangeDetail, Diagnostic, PrimaryChange, ReviewDocument, ReviewNode, SourceLocation, build_summary
from .matching import NodeMapping, _semantic_raw, match_nodes
from .structure import ParsedNode, ParsedProject
from .structured_diff import align_inline_sites, citation_details, equation_details, figure_details, table_details
from .text_diff import TokenEdit, scan_latex, token_edits


_TEXT_TYPES = {"paragraph", "part", "chapter", "section", "subsection", "subsubsection", "keywords"}
_VERBATIM_STARTS = (r"\begin{verbatim", r"\begin{Verbatim", r"\begin{lstlisting", r"\begin{minted")


def _is_text_node(node: ReviewNode) -> bool:
    return node.type in _TEXT_TYPES or (node.type == "fallback" and node.parent_id is None) or (
        node.type in {"environment", "fallback"} and node.raw_latex.startswith(_VERBATIM_STARTS))


@dataclass(frozen=True)
class ComparisonResult:
    mapping: NodeMapping
    document: ReviewDocument
    token_changes: Mapping[str, tuple[TokenEdit, ...]]


def _figure_fingerprints(project: ParsedProject, node: ParsedNode | None) -> dict[str, tuple[str, str]]:
    if node is None:
        return {}
    result = {}
    root = project.expanded.source.root.resolve()
    for asset in node.assets:
        candidates = {dep.file for dep in project.expanded.dependencies
                      if dep.kind == "graphic" and dep.argument == asset and dep.source_start is not None
                      and any(dep.referenced_from == origin.origin.file
                              and dep.include_instance == origin.origin.include_instance
                              and origin.origin.start <= dep.source_start < origin.origin.end
                              for origin in node.origins)}
        if len(candidates) != 1:
            continue
        relative = candidates.pop()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            continue
        try:
            result[asset] = (relative, sha256(path.read_bytes()).hexdigest())
        except OSError:
            continue  # 缺失仍由来源和报告诊断，不能伪装成内容变化。
    return result


def _comment_nodes(project: ParsedProject, side: str) -> tuple[ReviewNode, ...]:
    spans = scan_latex(project.expanded.text)[1]
    nodes = []
    for index, span in enumerate(spans, 1):
        origins = project.expanded.origin_ranges(span.start, span.end)
        if len(origins) == 1 and origins[0].origin.confidence == "exact":
            origin = origins[0].origin
            location = SourceLocation(origin.file, origin.start_line, origin.end_line,
                                      origin.start_column, origin.end_column)
        else:
            location = SourceLocation(None, None, None, confidence=0.3 if origins else 0,
                                      uncertainty_reason="注释跨来源片段或来源不确定")
        nodes.append(ReviewNode(f"comment-{side}-{index:06d}", "comment", span.text, None, (), (), location,
                                span.text, span.text))
    return tuple(nodes)


def _text_of(tokens) -> str:
    return " ".join(token.text for token in tokens)


def _prose_tokens(tokens):
    """去掉行内站点后合并相邻普通空格，保留真实的词间边界。"""
    result = []
    for token in tokens:
        if token.kind in {"citation", "math"}:
            continue
        if token.kind == "space" and token.text == "␠" and result and result[-1].kind == "space" and result[-1].text == "␠":
            continue
        result.append(token)
    return tuple(result)


def _site_spaces(parent: ParsedNode, site: ParsedNode) -> tuple[bool, bool]:
    raw = parent.review.raw_latex
    start = site.expanded_start - parent.expanded_start
    end = site.expanded_end - parent.expanded_start
    return (start > 0 and raw[start - 1].isspace(), end < len(raw) and raw[end].isspace())


def _space_description(space: tuple[bool, bool]) -> str:
    return f"前侧{'有' if space[0] else '无'}空白；后侧{'有' if space[1] else '无'}空白"


def _move_detail(left: ReviewNode, right: ReviewNode, number: int) -> ChangeDetail:
    old_section = " / ".join(left.section_path) or "文档根部"
    new_section = " / ".join(right.section_path) or "文档根部"
    return ChangeDetail(f"detail-{number:03d}", "move", "moved", old_section, new_section,
                        f"位置移动：{old_section} → {new_section}", left.source, right.source)


def compare_projects(old: ParsedProject, new: ParsedProject, *, review_comments: bool = False) -> ComparisonResult:
    """比较解析后的两侧项目；主变更按审阅结构计数。"""
    mapping = match_nodes(old, new)
    a, b = old.by_id(), new.by_id()
    changes: list[PrimaryChange] = []
    diagnostics = [*old.diagnostics, *new.diagnostics]
    token_changes: dict[str, tuple[TokenEdit, ...]] = {}
    added_words = removed_words = 0

    def emit(kind: str, left: ReviewNode | None, right: ReviewNode | None, confidence: float,
             categories: tuple[str, ...], details: tuple[ChangeDetail, ...], summary: str,
             edits: tuple[TokenEdit, ...] = (), *, count_words: bool = True) -> None:
        nonlocal added_words, removed_words
        change_id = f"change-{len(changes) + 1:06d}"
        changes.append(PrimaryChange(change_id, kind, (right or left).type,
                                     left.id if left else None, right.id if right else None,
                                     left.source if left else None, right.source if right else None,
                                     confidence, categories, details, summary))
        if edits:
            token_changes[change_id] = edits
            if count_words:
                removed_words += sum(token.words for edit in edits for token in edit.old)
                added_words += sum(token.words for edit in edits for token in edit.new)

    def text_change(left: ReviewNode | None, right: ReviewNode | None, confidence: float,
                    reason: str, moved: bool = False) -> None:
        old_tokens = scan_latex(_semantic_raw(a[left.id], a))[0] if left else ()
        new_tokens = scan_latex(_semantic_raw(b[right.id], b))[0] if right else ()
        # 公式和引用是完整词元；其语义明细按子节点及其来源位置独立比较。
        text_edits = token_edits(_prose_tokens(old_tokens), _prose_tokens(new_tokens))
        details: list[ChangeDetail] = []
        categories: list[str] = []
        for edit in text_edits:
            old_visible = _text_of(edit.old)
            new_visible = _text_of(edit.new)
            categories.append("text")
            details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "text", edit.kind,
                                        old_visible or None, new_visible or None, "正文词元变化"))
        for detail in citation_details(a[left.id] if left else None, b[right.id] if right else None, old, new):
            categories.append("citation")
            details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", detail.category, detail.kind,
                                        detail.old_text, detail.new_text, detail.summary,
                                        detail.source_old, detail.source_new))
        old_math = [a[child] for child in left.child_ids if a[child].review.type == "inline_math"] if left else []
        new_math = [b[child] for child in right.child_ids if b[child].review.type == "inline_math"] if right else []
        for old_index, new_index in align_inline_sites(a[left.id] if left else None,
                                                        b[right.id] if right else None, old, new, "inline_math"):
            left_inline = old_math[old_index] if old_index is not None else None
            right_inline = new_math[new_index] if new_index is not None else None
            unsupported = any(d.code == "unknown_latex" for node in (left_inline, right_inline) if node
                              for d in node.diagnostics)
            for detail in equation_details(left_inline, right_inline, diagnostics, unsupported=unsupported):
                categories.append("equation")
                details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", detail.category, detail.kind,
                                            detail.old_text, detail.new_text, detail.summary,
                                            detail.source_old, detail.source_new))
        # 站点边界独立于正文词元编辑；即使同段另有词或空白变化也保留此明细。
        if left and right:
            for site_kind in ("citation", "inline_math"):
                old_sites = [a[child] for child in left.child_ids if a[child].review.type == site_kind]
                new_sites = [b[child] for child in right.child_ids if b[child].review.type == site_kind]
                for old_index, new_index in align_inline_sites(a[left.id], b[right.id], old, new, site_kind):
                    if old_index is None or new_index is None:
                        continue
                    old_space = _site_spaces(a[left.id], old_sites[old_index])
                    new_space = _site_spaces(b[right.id], new_sites[new_index])
                    if old_space != new_space:
                        categories.append("text")
                        details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "text", "modified",
                                                    _space_description(old_space), _space_description(new_space),
                                                    "行内内容相邻空白变化"))
                        break
        if moved and left and right:
            categories.append("move")
            details.append(_move_detail(left, right, len(details) + 1))
        if not details:
            return
        kind = "moved" if moved else "modified" if left and right else "removed" if left else "added"
        emit(kind, left, right, confidence, tuple(dict.fromkeys(categories)), tuple(details),
             f"{('关键词' if (right or left).type == 'keywords' else '正文')}{('移动' if kind == 'moved' else '修改' if kind == 'modified' else '删除' if kind == 'removed' else '新增')}；{reason}",
             text_edits, count_words=(right or left).type != "keywords")

    def structured_change(left: ReviewNode | None, right: ReviewNode | None, confidence: float,
                          reason: str, moved: bool = False) -> None:
        kind = (right or left).type
        left_node, right_node = a[left.id] if left else None, b[right.id] if right else None
        if kind == "equation":
            unsupported = any(project.by_id()[child].review.type == "fallback"
                              for project, node in ((old, left_node), (new, right_node)) if node
                              for child in node.review.child_ids)
            details = equation_details(left_node, right_node, diagnostics, unsupported=unsupported)
            old_arrays = [a[child] for child in left.child_ids if a[child].review.type == "math_array"] if left else []
            new_arrays = [b[child] for child in right.child_ids if b[child].review.type == "math_array"] if right else []
            for index in range(max(len(old_arrays), len(new_arrays))):
                for detail in table_details(old_arrays[index] if index < len(old_arrays) else None,
                                            new_arrays[index] if index < len(new_arrays) else None):
                    details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "equation", detail.kind,
                                                detail.old_text, detail.new_text, detail.summary,
                                                detail.source_old, detail.source_new,
                                                detail.row_old, detail.column_old, detail.row_new, detail.column_new))
        elif kind == "figure":
            details = figure_details(left_node, right_node,
                                     _figure_fingerprints(old, left_node), _figure_fingerprints(new, right_node))
        else:
            details = table_details(left_node, right_node)
        if moved and left and right:
            details.append(_move_detail(left, right, len(details) + 1))
        if not details:
            return
        change_kind = "moved" if moved else "modified" if left and right else "removed" if left else "added"
        emit(change_kind, left, right, confidence, tuple(dict.fromkeys(detail.category for detail in details)),
             tuple(details), f"{kind} 整体变化；{reason}")

    def excluded_prose(node: ReviewNode, by_id: dict[str, ParsedNode]) -> bool:
        parent = node.parent_id
        while parent:
            ancestor = by_id[parent].review
            if ancestor.type in {"keywords", "metadata"}:
                return True
            parent = ancestor.parent_id
        return False

    def compare_one(left: ReviewNode | None, right: ReviewNode | None, confidence: float, reason: str,
                    moved: bool = False) -> None:
        try:
            (text_change if _is_text_node(right or left) else structured_change)(left, right, confidence, reason, moved)
        except Exception as exc:
            kind = "moved" if moved else "modified" if left and right else "removed" if left else "added"
            category = (right or left).type if (right or left).type in {"equation", "figure", "table"} else "text"
            detail = ChangeDetail("detail-001", category, kind, left.raw_latex if left else None,
                                  right.raw_latex if right else None, "节点比较失败；保留双侧 LaTeX 原文")
            details = (detail, _move_detail(left, right, 2)) if moved and left and right else (detail,)
            categories = (category, "move") if moved else (category,)
            emit(kind, left, right, confidence, categories, details, "节点源码回退；" + reason)
            diagnostics.append(Diagnostic("node_diff_fallback", "warning", f"节点比较失败，已保留原文：{exc}",
                                          source_old=left.source if left else None,
                                          source_new=right.source if right else None))

    # 配对及未配对节点均按旧侧文档顺序输出，末尾追加新侧新增。
    pairs = {pair.old_id: pair for pair in mapping.pairs}
    unmatched_old = {item.node_id: item for item in mapping.old_unmatched}
    unmatched_new = {item.node_id: item for item in mapping.new_unmatched}
    for node in old.nodes:
        left = node.review
        if excluded_prose(left, a) or not (_is_text_node(left) or left.type in {"equation", "figure", "table"}):
            continue
        if pair := pairs.get(left.id):
            compare_one(left, b[pair.new_id].review, pair.confidence, pair.reason, pair.moved)
        elif item := unmatched_old.get(left.id):
            compare_one(left, None, item.confidence, item.reason)
            if item.confidence:
                diagnostics.append(Diagnostic("low_confidence_match", "warning", "节点未可靠配对；按删除与新增处理：" + item.reason,
                                              source_old=left.source))
    for node in new.nodes:
        right = node.review
        if not excluded_prose(right, b) and (_is_text_node(right) or right.type in {"equation", "figure", "table"}) and (item := unmatched_new.get(right.id)):
            compare_one(None, right, item.confidence, item.reason)
            if item.confidence:
                diagnostics.append(Diagnostic("low_confidence_match", "warning", "节点未可靠配对；按删除与新增处理：" + item.reason,
                                              source_new=right.source))

    old_comments = _comment_nodes(old, "old") if review_comments else ()
    new_comments = _comment_nodes(new, "new") if review_comments else ()
    if review_comments:
        matcher = SequenceMatcher(None, [node.raw_latex for node in old_comments],
                                  [node.raw_latex for node in new_comments], autojunk=False)
        for operation, i, j, k, l in matcher.get_opcodes():
            if operation == "equal":
                continue
            left = old_comments[i:j]
            right = new_comments[k:l]
            overlap = min(len(left), len(right)) if operation == "replace" else 0
            for index in range(overlap):
                detail = ChangeDetail("detail-001", "comment", "modified", left[index].raw_latex,
                                      right[index].raw_latex, "注释内容变化")
                emit("modified", left[index], right[index], .75, ("comment",), (detail,), "注释修改")
            for node in left[overlap:]:
                detail = ChangeDetail("detail-001", "comment", "removed", node.raw_latex, None, "注释删除")
                emit("removed", node, None, 0, ("comment",), (detail,), "注释删除")
            for node in right[overlap:]:
                detail = ChangeDetail("detail-001", "comment", "added", None, node.raw_latex, "注释新增")
                emit("added", None, node, 0, ("comment",), (detail,), "注释新增")

    document = ReviewDocument(old.expanded.source.entry, old.expanded.source.identity,
                              new.expanded.source.identity, old.review_nodes + old_comments,
                              new.review_nodes + new_comments, tuple(changes), tuple(diagnostics),
                              build_summary(tuple(changes), added_words=added_words, removed_words=removed_words))
    return ComparisonResult(mapping, document, MappingProxyType(token_changes))
