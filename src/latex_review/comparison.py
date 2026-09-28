"""把稳定匹配、正文词元差异和可选注释审阅汇入公共契约。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from types import MappingProxyType
from typing import Mapping

from .contract import ChangeDetail, Diagnostic, PrimaryChange, ReviewDocument, ReviewNode, SourceLocation, build_summary
from .matching import NodeMapping, _semantic_raw, match_nodes
from .structure import ParsedProject
from .text_diff import TokenEdit, scan_latex, token_edits


_TEXT_TYPES = {"paragraph", "part", "chapter", "section", "subsection", "subsubsection"}
_VERBATIM_STARTS = (r"\begin{verbatim", r"\begin{Verbatim", r"\begin{lstlisting", r"\begin{minted")


def _is_text_node(node: ReviewNode) -> bool:
    return node.type in _TEXT_TYPES or (node.type in {"environment", "fallback"} and
                                         node.raw_latex.startswith(_VERBATIM_STARTS))


@dataclass(frozen=True)
class ComparisonResult:
    mapping: NodeMapping
    document: ReviewDocument
    token_changes: Mapping[str, tuple[TokenEdit, ...]]


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
             edits: tuple[TokenEdit, ...] = ()) -> None:
        nonlocal added_words, removed_words
        change_id = f"change-{len(changes) + 1:06d}"
        changes.append(PrimaryChange(change_id, kind, (right or left).type,
                                     left.id if left else None, right.id if right else None,
                                     left.source if left else None, right.source if right else None,
                                     confidence, categories, details, summary))
        if edits:
            token_changes[change_id] = edits
            removed_words += sum(token.words for edit in edits for token in edit.old)
            added_words += sum(token.words for edit in edits for token in edit.new)

    def text_change(left: ReviewNode | None, right: ReviewNode | None, confidence: float,
                    reason: str) -> None:
        old_tokens = scan_latex(_semantic_raw(a[left.id], a))[0] if left else ()
        new_tokens = scan_latex(_semantic_raw(b[right.id], b))[0] if right else ()
        edits = token_edits(old_tokens, new_tokens)
        old_citations = set(a[left.id].citations) if left else set()
        new_citations = set(b[right.id].citations) if right else set()
        citation_changed = old_citations != new_citations
        if not edits and not citation_changed:
            return
        details: list[ChangeDetail] = []
        categories = []
        for edit in edits:
            old_visible = _text_of(edit.old)
            new_visible = _text_of(edit.new)
            citation_only = all(token.kind == "citation" for token in (*edit.old, *edit.new))
            if citation_only:
                continue
            categories.append("text")
            details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "text", edit.kind,
                                        old_visible or None, new_visible or None, "正文词元变化"))
        if citation_changed:
            categories.append("citation")
            details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "citation", "modified",
                                        ", ".join(sorted(old_citations)) or None,
                                        ", ".join(sorted(new_citations)) or None, "引用键变化"))
        # 引用命令写法变化而键不变：仍保留可见词元差异。
        if not details and edits:
            categories.append("text")
            for edit in edits:
                details.append(ChangeDetail(f"detail-{len(details) + 1:03d}", "text", edit.kind,
                                            _text_of(edit.old) or None, _text_of(edit.new) or None, "命令写法变化"))
        kind = "modified" if left and right else "removed" if left else "added"
        emit(kind, left, right, confidence, tuple(dict.fromkeys(categories)), tuple(details),
             f"{('正文修改' if kind == 'modified' else '正文删除' if kind == 'removed' else '正文新增')}；{reason}", edits)

    # 配对及未配对节点均按旧侧文档顺序输出，末尾追加新侧新增。
    pairs = {pair.old_id: pair for pair in mapping.pairs}
    unmatched_old = {item.node_id: item for item in mapping.old_unmatched}
    unmatched_new = {item.node_id: item for item in mapping.new_unmatched}
    for node in old.nodes:
        left = node.review
        if not _is_text_node(left):
            continue
        if pair := pairs.get(left.id):
            text_change(left, b[pair.new_id].review, pair.confidence, pair.reason)
        elif item := unmatched_old.get(left.id):
            text_change(left, None, item.confidence, item.reason)
            if item.confidence:
                diagnostics.append(Diagnostic("low_confidence_match", "warning", "节点未可靠配对；按删除与新增处理：" + item.reason,
                                              source_old=left.source))
    for node in new.nodes:
        right = node.review
        if _is_text_node(right) and (item := unmatched_new.get(right.id)):
            text_change(None, right, item.confidence, item.reason)
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
