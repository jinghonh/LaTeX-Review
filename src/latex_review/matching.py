"""双侧审阅节点的确定性、一对一匹配。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from .structure import ParsedNode, ParsedProject
from .text_diff import normalized_text, scan_latex


@dataclass(frozen=True)
class NodePair:
    old_id: str
    new_id: str
    confidence: float
    reason: str
    moved: bool = False


@dataclass(frozen=True)
class UnmatchedNode:
    side: str
    node_id: str
    confidence: float  # 与最佳候选的分数；不是已经建立的配对。
    reason: str


@dataclass(frozen=True)
class NodeMapping:
    pairs: tuple[NodePair, ...]
    old_unmatched: tuple[UnmatchedNode, ...]
    new_unmatched: tuple[UnmatchedNode, ...]

    @property
    def old_to_new(self) -> dict[str, str]:
        return {pair.old_id: pair.new_id for pair in self.pairs}

    @property
    def new_to_old(self) -> dict[str, str]:
        return {pair.new_id: pair.old_id for pair in self.pairs}


def _title(value: str) -> str:
    value = re.sub(r"^\s*(?:第[一二三四五六七八九十百0-9]+[章节]|(?:\d+[.．、])+)\s*", "", value)
    return " ".join(value.casefold().split())


def _scope(node: ParsedNode) -> tuple[str, ...]:
    path = node.review.section_path
    if node.review.type in {"part", "chapter", "section", "subsection", "subsubsection"}:
        path = path[:-1]
    return tuple(_title(part) for part in path)


def _neighbors(nodes: tuple[ParsedNode, ...], index: int) -> tuple[str | None, str | None]:
    node = nodes[index]
    scope = _scope(node)
    previous = next((nodes[j].review.id for j in range(index - 1, -1, -1)
                     if _scope(nodes[j]) == scope and nodes[j].review.type == node.review.type), None)
    following = next((nodes[j].review.id for j in range(index + 1, len(nodes))
                      if _scope(nodes[j]) == scope and nodes[j].review.type == node.review.type), None)
    return previous, following


def _ancestor_labels(node: ParsedNode, by_id: dict[str, ParsedNode]) -> set[str]:
    ancestor_labels: set[str] = set()
    parent_id = node.review.parent_id
    while parent_id and parent_id in by_id:
        ancestor = by_id[parent_id]
        ancestor_labels.update(ancestor.labels)
        parent_id = ancestor.review.parent_id
    return ancestor_labels


def _semantic_raw(node: ParsedNode, by_id: dict[str, ParsedNode]) -> str:
    """标题标签有时落在下一段源码里；它不属于该段正文。"""
    ancestor_labels = _ancestor_labels(node, by_id)
    if not ancestor_labels:
        return node.review.raw_latex
    return re.sub(r"\\label\s*\{([^{}]+)\}",
                  lambda match: "" if match.group(1) in ancestor_labels else match.group(),
                  node.review.raw_latex)


def _matching_text(raw: str, kind: str) -> str:
    if kind == "paragraph":
        return " ".join("⟦引用⟧" if token.kind == "citation" else
                        "⟦公式⟧" if token.kind == "math" else token.text
                        for token in scan_latex(raw)[0])
    return normalized_text(raw)


def match_nodes(old: ParsedProject, new: ParsedProject) -> NodeMapping:
    """唯一标签和唯一内容先锚定，邻域只辅助有证据的剩余候选。"""
    a, b = old.nodes, new.nodes
    a_by_id, b_by_id = old.by_id(), new.by_id()
    a_indices = {node.review.id: i for i, node in enumerate(a)}
    a_scope = [_scope(node) for node in a]
    b_scope = [_scope(node) for node in b]
    a_neighbors = [_neighbors(a, i) for i in range(len(a))]
    b_neighbors = [_neighbors(b, i) for i in range(len(b))]
    a_raw = [_semantic_raw(node, a_by_id) for node in a]
    b_raw = [_semantic_raw(node, b_by_id) for node in b]
    a_effective_labels = [set(node.labels) - _ancestor_labels(node, a_by_id) for node in a]
    b_effective_labels = [set(node.labels) - _ancestor_labels(node, b_by_id) for node in b]
    a_labels = Counter((node.review.type, label) for node, labels in zip(a, a_effective_labels) for label in labels)
    b_labels = Counter((node.review.type, label) for node, labels in zip(b, b_effective_labels) for label in labels)
    a_text = [_matching_text(raw, node.review.type) for raw, node in zip(a_raw, a)]
    b_text = [_matching_text(raw, node.review.type) for raw, node in zip(b_raw, b)]
    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[NodePair] = []
    pair_map: dict[str, str] = {}
    similarities: dict[tuple[int, int], float] = {}

    def add(i: int, j: int, confidence: float, reason: str) -> None:
        used_a.add(i)
        used_b.add(j)
        pairs.append(NodePair(a[i].review.id, b[j].review.id, round(confidence, 3), reason))
        pair_map[a[i].review.id] = b[j].review.id

    def parents_match(i: int, j: int) -> bool:
        old_parent, new_parent = a[i].review.parent_id, b[j].review.parent_id
        return (old_parent is None and new_parent is None) or bool(old_parent and pair_map.get(old_parent) == new_parent)

    def scope_compatible(i: int, j: int) -> bool:
        return a_scope[i] == b_scope[j] or bool(a[i].review.parent_id and parents_match(i, j))

    # 重复标签没有身份语义，不能覆盖先前的标签映射。
    for i, item in enumerate(a):
        if i in used_a:
            continue
        labels = {label for label in a_effective_labels[i]
                  if a_labels[item.review.type, label] == b_labels[item.review.type, label] == 1}
        options = [j for j, candidate in enumerate(b) if j not in used_b and
                   candidate.review.type == item.review.type and labels.intersection(b_effective_labels[j]) and
                   scope_compatible(i, j) and parents_match(i, j)]
        if len(options) == 1:
            add(i, options[0], .99, "两侧唯一标签、类型和章节一致")

    # 前言与摘要是结构容器；同一已配对父节点下唯一的同类容器可以可靠对应。
    # 先建立父容器，再允许其内部段落用常规内容和邻域证据配对。
    for kind in ("frontmatter", "abstract", "keywords"):
        for i, item in enumerate(a):
            if i in used_a or item.review.type != kind:
                continue
            options = [j for j, candidate in enumerate(b) if j not in used_b and candidate.review.type == kind
                       and parents_match(i, j) and scope_compatible(i, j)]
            peers = [k for k, candidate in enumerate(a) if k not in used_a and candidate.review.type == kind
                     and candidate.review.parent_id == item.review.parent_id and a_scope[k] == a_scope[i]]
            if len(options) == len(peers) == 1:
                add(i, options[0], .95, "同一父结构内唯一前言容器对应")

    def short_heading(raw: str) -> str | None:
        match = re.fullmatch(r"\s*\\(?:paragraph|subparagraph)\*?\s*\{([^{}]+)\}\s*", raw)
        return _title(match.group(1).rstrip(".．:： ")) if match else None

    # 唯一规范化内容不依赖绝对序号；重复正文留给邻域阶段。
    for i, item in enumerate(a):
        if i in used_a or not a_text[i]:
            continue
        key = (item.review.type, a_scope[i], a_text[i])
        ai = [k for k, n in enumerate(a) if k not in used_a and (n.review.type, a_scope[k], a_text[k]) == key]
        bj = [k for k, n in enumerate(b) if k not in used_b and parents_match(i, k)
              and scope_compatible(i, k) and n.review.type == item.review.type and b_text[k] == a_text[i]]
        if len(ai) == len(bj) == 1:
            add(i, bj[0], .98, "章节、类型和规范化内容唯一一致")

    # 单独的短标题以标题文字为身份；先配对父章节，再比较末尾标点。
    for i, item in enumerate(a):
        heading = short_heading(item.review.raw_latex)
        if i in used_a or item.review.type != "paragraph" or not heading:
            continue
        options = [j for j, candidate in enumerate(b) if j not in used_b and
                   candidate.review.type == "paragraph" and short_heading(candidate.review.raw_latex) == heading and
                   parents_match(i, j) and scope_compatible(i, j)]
        peers = [k for k, candidate in enumerate(a) if k not in used_a and
                 candidate.review.type == "paragraph" and short_heading(candidate.review.raw_latex) == heading and
                 candidate.review.parent_id == item.review.parent_id]
        if len(options) == len(peers) == 1:
            add(i, options[0], .98, "同一父结构内唯一短标题对应")

    def leading_label(raw: str) -> str | None:
        match = re.match(r"\s*\\label\s*\{([^{}]+)\}", raw)
        return match.group(1) if match else None

    for i, item in enumerate(a):
        label = leading_label(item.review.raw_latex)
        if i in used_a or item.review.type != "paragraph" or not label:
            continue
        options = [j for j, candidate in enumerate(b) if j not in used_b and
                   candidate.review.type == "paragraph" and leading_label(candidate.review.raw_latex) == label and
                   parents_match(i, j) and scope_compatible(i, j)]
        if len(options) == 1:
            j = options[0]
            similarity = SequenceMatcher(None, a_text[i], b_text[j], autojunk=False).ratio()
            if similarity >= .3:
                add(i, j, .94, "同一父结构内段首标签与正文共同确认")

    # 大段改写后，邻段尚未配对会干扰打分；先接受双方明显占优的正文对应。
    for i, item in enumerate(a):
        if i in used_a or item.review.type != "paragraph":
            continue
        options = [(SequenceMatcher(None, a_text[i], b_text[j], autojunk=False).ratio(), j)
                   for j, candidate in enumerate(b) if j not in used_b and candidate.review.type == "paragraph"
                   and parents_match(i, j) and scope_compatible(i, j)]
        options.sort(reverse=True)
        if not options or options[0][0] < .62 or (len(options) > 1 and options[0][0] - options[1][0] < .15):
            continue
        similarity, j = options[0]
        reverse = sorted((SequenceMatcher(None, a_text[k], b_text[j], autojunk=False).ratio(), k)
                         for k, candidate in enumerate(a) if k not in used_a and candidate.review.type == "paragraph"
                         and parents_match(k, j) and scope_compatible(k, j))
        if reverse[-1][1] == i and (len(reverse) == 1 or reverse[-1][0] - reverse[-2][0] >= .15):
            add(i, j, min(.97, .55 + .4 * similarity), "双方唯一占优的正文相似度")

    # 已配对父结构内只剩一对相似段落时，允许较大幅度的局部改写。
    for i, item in enumerate(a):
        if i in used_a or item.review.type != "paragraph" or not item.review.parent_id:
            continue
        old_peers = [k for k, candidate in enumerate(a) if k not in used_a and candidate.review.type == "paragraph"
                     and candidate.review.parent_id == item.review.parent_id]
        new_peers = [j for j, candidate in enumerate(b) if j not in used_b and candidate.review.type == "paragraph"
                     and parents_match(i, j) and scope_compatible(i, j)]
        if len(old_peers) == len(new_peers) == 1:
            j = new_peers[0]
            similarity = SequenceMatcher(None, a_text[i], b_text[j], autojunk=False).ratio()
            if similarity >= .35:
                add(i, j, .65, "已配对父结构内唯一剩余相似段落")

    # 图表与独立公式按同一父结构内的剩余顺序配对；只有两侧剩余数量相等才使用
    # 此锚点。多项内部字段同时改变时，原始字符串相似度不足以可靠建立配对。
    for kind in ("equation", "figure", "table"):
        scopes = sorted({_scope(node) for node in a if node.review.type == kind} |
                        {_scope(node) for node in b if node.review.type == kind})
        for scope in scopes:
            old_indices = [i for i, node in enumerate(a) if i not in used_a and node.review.type == kind and a_scope[i] == scope]
            new_indices = [j for j, node in enumerate(b) if j not in used_b and node.review.type == kind and b_scope[j] == scope]
            if len(old_indices) != len(new_indices):
                continue
            for i, j in zip(old_indices, new_indices):
                if parents_match(i, j) and scope_compatible(i, j):
                    add(i, j, .75, "同一父结构内结构化节点顺序对应")

    def score(i: int, j: int) -> tuple[float, str]:
        left, right = a[i], b[j]
        if left.review.type != right.review.type:
            return 0.0, "节点类型不同"
        if not parents_match(i, j):
            return 0.0, "父节点未配对"
        if not scope_compatible(i, j):
            return 0.0, "章节不同；交由独立移动锚点检查"
        if (i, j) not in similarities:
            similarities[i, j] = SequenceMatcher(None, a_text[i], b_text[j], autojunk=False).ratio()
        similarity = similarities[i, j]
        old_prev, old_next = a_neighbors[i]
        new_prev, new_next = b_neighbors[j]
        neighbor = .5 * (old_prev is not None and pair_map.get(old_prev) == new_prev)
        neighbor += .5 * (old_next is not None and pair_map.get(old_next) == new_next)
        position = 1 - abs(i / max(len(a) - 1, 1) - j / max(len(b) - 1, 1))
        value = .45 * similarity + .15 + .35 * neighbor + .05 * position
        if similarity == 1:
            value = max(value, .8)
        reason = f"内容相似度 {similarity:.2f}；邻域支持 {neighbor:.1f}；章节和类型一致"
        return round(min(value, .97), 3), reason

    # 每轮只提交双方唯一的最佳候选；稳定排序只决定处理次序，不解除歧义。
    while True:
        candidates = {(i, j): score(i, j) for i in range(len(a)) if i not in used_a
                      for j in range(len(b)) if j not in used_b and a[i].review.type == b[j].review.type
                      and parents_match(i, j) and scope_compatible(i, j)}
        by_old = defaultdict(list)
        by_new = defaultdict(list)
        for (i, j), (value, _) in candidates.items():
            by_old[i].append((value, j))
            by_new[j].append((value, i))
        for options in (*by_old.values(), *by_new.values()):
            options.sort(reverse=True)
        eligible = []
        for (i, j), (value, reason) in candidates.items():
            old_options, new_options = by_old[i], by_new[j]
            if value < .45:
                continue
            other_a = old_options[0][0] if old_options[0][1] != j else (
                old_options[1][0] if len(old_options) > 1 else 0)
            other_b = new_options[0][0] if new_options[0][1] != i else (
                new_options[1][0] if len(new_options) > 1 else 0)
            if value - max(other_a, other_b) >= .08:
                eligible.append((value, i, j, reason))
        if not eligible:
            break
        eligible.sort(key=lambda item: (-item[0], item[1], item[2]))
        _, i, j, reason = eligible[0]
        add(i, j, candidates[i, j][0], reason)

    # 跨章节只接受全局唯一的显式标签，或全局唯一且完全一致的正文。
    # 相似文本本身不构成移动证据。
    movable = {"paragraph", "equation", "figure", "table"}
    for i, item in enumerate(a):
        if i in used_a or item.review.type not in movable:
            continue
        options = []
        for j, candidate in enumerate(b):
            if j in used_b or candidate.review.type != item.review.type or a_scope[i] == b_scope[j]:
                continue
            labels = a_effective_labels[i] & b_effective_labels[j]
            old_body = _matching_text(re.sub(r"\\label\s*\{[^{}]*\}", "", a_raw[i]), item.review.type)
            new_body = _matching_text(re.sub(r"\\label\s*\{[^{}]*\}", "", b_raw[j]), item.review.type)
            unique_label = (any(a_labels[item.review.type, label] == b_labels[item.review.type, label] == 1
                                for label in labels) and
                            SequenceMatcher(None, old_body, new_body, autojunk=False).ratio() >= .65)
            exact = (len(a_text[i]) >= 12 and a_text[i] == b_text[j] and
                     sum(n.review.type == item.review.type and t == a_text[i] for n, t in zip(a, a_text)) == 1 and
                     sum(n.review.type == item.review.type and t == b_text[j] for n, t in zip(b, b_text)) == 1)
            if unique_label or exact:
                options.append((j, unique_label))
        if len(options) == 1:
            j, labelled = options[0]
            competing = [k for k, candidate in enumerate(a) if k not in used_a and k != i and
                         candidate.review.type == item.review.type and a_scope[k] != b_scope[j] and
                         (a_effective_labels[k] & b_effective_labels[j] or a_text[k] == b_text[j])]
            if not competing:
                add(i, j, .99 if labelled else .98,
                    "跨章节唯一标签确认移动" if labelled else "跨章节唯一完整内容确认移动")
                pairs[-1] = NodePair(pairs[-1].old_id, pairs[-1].new_id, pairs[-1].confidence,
                                     pairs[-1].reason, True)

    def unique_lis(sequence: list[int]) -> set[int] | None:
        length, count, predecessor = [], [], []
        for i, value in enumerate(sequence):
            previous = [j for j in range(i) if sequence[j] < value]
            best = max((length[j] for j in previous), default=0)
            choices = [j for j in previous if length[j] == best]
            length.append(best + 1)
            count.append(min(2, sum(count[j] for j in choices)) if choices else 1)
            predecessor.append(choices[0] if len(choices) == 1 and count[choices[0]] == 1 else None)
        maximum = max(length, default=0)
        ends = [i for i, value in enumerate(length) if value == maximum]
        if sum(count[i] for i in ends) != 1:
            return None
        kept: set[int] = set()
        index = ends[0]
        while index is not None:
            kept.add(index)
            index = predecessor[index]
        return kept

    # 文档内重排以唯一最长保序锚点识别；并且移动节点本身必须有唯一身份。
    pair_positions = {pair.old_id: index for index, pair in enumerate(pairs)}
    b_indices = {node.review.id: index for index, node in enumerate(b)}
    ambiguous_order: set[str] = set()
    for scope in set(a_scope):
        for kind in movable:
            old_order = [pair for pair in pairs if a[a_indices[pair.old_id]].review.type == kind and
                         a_scope[a_indices[pair.old_id]] == scope and
                         b_scope[b_indices[pair.new_id]] == scope]
            if len(old_order) < 2:
                continue
            old_order.sort(key=lambda pair: a_indices[pair.old_id])
            sequence = [b_indices[pair.new_id] for pair in old_order]
            kept = unique_lis(sequence)
            if kept is None:
                ambiguous_order.update(old_order[index].old_id for index in range(len(sequence))
                                       if any(sequence[index] > sequence[next_index] for next_index in range(index + 1, len(sequence)))
                                       or any(sequence[previous] > sequence[index] for previous in range(index)))
                continue
            for index, pair in enumerate(old_order):
                if index in kept:
                    continue
                i, j = a_indices[pair.old_id], b_indices[pair.new_id]
                identity = ("唯一标签" in pair.reason or
                            a_text[i] == b_text[j] and len(a_text[i]) >= 12 and
                            sum(t == a_text[i] and n.review.type == kind for n, t in zip(a, a_text)) == 1 and
                            sum(t == b_text[j] and n.review.type == kind for n, t in zip(b, b_text)) == 1)
                if identity:
                    pairs[pair_positions[pair.old_id]] = NodePair(pair.old_id, pair.new_id, pair.confidence,
                                                                  pair.reason + "；同章节保序锚点确认移动", True)
                else:
                    ambiguous_order.add(pair.old_id)
    if ambiguous_order:
        for pair in pairs:
            if pair.old_id in ambiguous_order:
                used_a.discard(a_indices[pair.old_id])
                used_b.discard(b_indices[pair.new_id])
                pair_map.pop(pair.old_id, None)
        pairs = [pair for pair in pairs if pair.old_id not in ambiguous_order]

    def unmatched(side: str) -> tuple[UnmatchedNode, ...]:
        own, other, used, other_used = (a, b, used_a, used_b) if side == "old" else (b, a, used_b, used_a)
        output = []
        for index, node in enumerate(own):
            if index in used:
                continue
            options = [(score(index, k) if side == "old" else score(k, index))[0]
                       for k, candidate in enumerate(other) if k not in other_used and
                       candidate.review.type == node.review.type]
            best = max(options, default=0.0)
            if best:
                reason = "候选存在歧义或置信度不足，按删除和新增处理"
            else:
                same_scope = any(candidate.review.type == node.review.type and
                                 (a_scope[index] == b_scope[k] if side == "old" else b_scope[index] == a_scope[k])
                                 for k, candidate in enumerate(other) if k not in other_used)
                reason = "父节点未可靠配对" if same_scope else "无同章节同类型候选"
            output.append(UnmatchedNode(side, node.review.id, best, reason))
        return tuple(output)

    pairs.sort(key=lambda item: a_indices[item.old_id])
    assert len({pair.old_id for pair in pairs}) == len(pairs)
    assert len({pair.new_id for pair in pairs}) == len(pairs)
    return NodeMapping(tuple(pairs), unmatched("old"), unmatched("new"))
