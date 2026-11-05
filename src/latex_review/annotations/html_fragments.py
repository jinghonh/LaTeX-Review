"""Conservative root-fragment reuse of the existing annotation HTML extractor.

A hint is never authority: uncertain topology/context falls back to the complete
legacy extractor. Cache entries contain only immutable HTML-derived facts.
"""
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from ..preview_fragments import FragmentPlan, RootFragment
from .content import (_Element, _PreviewParser, _HTMLUnit, _extract_html_units,
                      _freeze_html_units, _node_types)

_DISPLAY_ONLY = frozenset(('asset', 'asset-card', 'asset-metadata', 'asset-fallback', 'structure-number'))
_FRAGMENT_MAX_BYTES = 1024 * 1024
_CONTEXT_MAX_BYTES = 256 * 1024
_CACHE_MAX_BYTES = 16 * 1024 * 1024
_CACHE_MAX_ENTRIES = 8192
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = Lock()


class _ProofParser(_PreviewParser):
    """Use exactly the existing parser, additionally detecting context repair."""
    def __init__(self):
        super().__init__()
        self.balanced = True

    def handle_endtag(self, tag):
        if len(self.stack) <= 1 or self.stack[-1].tag != tag:
            self.balanced = False
        super().handle_endtag(tag)


def _elements(element):
    for child in element.children:
        if isinstance(child, _Element):
            yield child
            yield from _elements(child)


def _size(value):
    return len(value) if value.isascii() else len(value.encode('utf-8', errors='surrogatepass'))


def _ids(elements, attribute):
    values = [element.attrs[attribute] for element in elements if attribute in element.attrs]
    if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
        return None
    return frozenset(values)


def _outside_context(plan):
    if _size(plan.before) + _size(plan.after) > _CONTEXT_MAX_BYTES:
        return None
    parser = _ProofParser()
    parser.feed(plan.before)
    if (not parser.balanced or len(parser.stack) <= 1 or parser.rawdata
            or parser.cdata_elem is not None):
        return None
    side = parser.stack[-1]
    if side.tag != 'section' or side.attrs.get('data-side') != 'new' or 'preview-side' not in side.attrs.get('class', '').split():
        return None
    # Root anchor selectors do not depend on outer sibling positions, and the
    # actual ancestors cannot suppress text or provide an owner across chunks.
    for ancestor in parser.stack[1:]:
        if (ancestor.attrs.get('data-node-id') is not None
                or _DISPLAY_ONLY.intersection(ancestor.attrs.get('class', '').split())):
            return None
    parser.feed(plan.after)
    if not parser.balanced or parser.stack != [parser.root] or parser.rawdata or parser.cdata_elem is not None:
        return None
    elements = tuple(_elements(parser.root))
    sides = [element for element in elements if 'preview-side' in element.attrs.get('class', '').split()]
    if sides != [side] or any('data-node-id' in element.attrs for element in _elements(side)):
        return None
    return _ids(elements, 'id')


@dataclass(frozen=True)
class _FragmentFacts:
    root_id: str
    node_ids: frozenset
    node_types: tuple
    dom_ids: frozenset
    units: tuple[_HTMLUnit, ...]


def _extract_fragment(nodes, root):
    parser = _ProofParser()
    wrapped = '<section class="preview-side" data-side="new">' + root.html + '</section>'
    parser.feed(wrapped)
    if (not parser.balanced or parser.stack != [parser.root] or parser.rawdata
            or parser.cdata_elem is not None or len(parser.root.children) != 1):
        return None
    side = parser.root.children[0]
    if not isinstance(side, _Element):
        return None
    children = [child for child in side.children if isinstance(child, _Element)]
    if (len(children) != 1 or any(isinstance(child, str) and child.strip() for child in side.children)):
        return None
    element = children[0]
    if element.attrs.get('data-node-id') != root.node_id or element.attrs.get('data-side') != 'new':
        return None
    elements = tuple(_elements(side))
    if any('preview-side' in item.attrs.get('class', '').split() or item.attrs.get('data-side', 'new') != 'new'
           for item in elements):
        return None
    node_ids, dom_ids = _ids(elements, 'data-node-id'), _ids(elements, 'id')
    if node_ids is None or dom_ids is None or not node_ids.issubset(nodes):
        return None
    # Every owner must be anchored inside this chunk. No selector may need an
    # ancestor outside it, even if a future renderer adds an unusual element.
    if any('data-node-id' in item.attrs and not item.attrs.get('id') for item in elements):
        return None
    extracted, extracted_ids = _extract_html_units(nodes, wrapped, _parsed=parser)
    if extracted_ids != node_ids:
        return None
    return _FragmentFacts(root.node_id, node_ids, _node_types(nodes, node_ids), dom_ids,
                          _freeze_html_units(extracted))


def _facts(nodes, root):
    global _CACHE_BYTES
    with _CACHE_LOCK:
        cached = _CACHE.get(root.html)
        if cached is not None:
            facts, weight = cached
            if facts.root_id == root.node_id and facts.node_types == _node_types(nodes, facts.node_ids):
                _CACHE.move_to_end(root.html)
                return facts
    facts = _extract_fragment(nodes, root)
    if facts is None:
        return None
    # The UTF-8 key + immutable serialized-fact payload is bounded as well as
    # entry count. No parsed DOM/project/source-bound unit survives this call.
    weight = _size(root.html) + _size(repr(facts))
    if weight <= _CACHE_MAX_BYTES:
        with _CACHE_LOCK:
            old = _CACHE.pop(root.html, None)
            if old is not None:
                _CACHE_BYTES -= old[1]
            _CACHE[root.html] = (facts, weight)
            _CACHE_BYTES += weight
            while len(_CACHE) > _CACHE_MAX_ENTRIES or _CACHE_BYTES > _CACHE_MAX_BYTES:
                _, (_, old_weight) = _CACHE.popitem(last=False)
                _CACHE_BYTES -= old_weight
    return facts


def fragment_units(project, preview_html, plan):
    """Return proven equivalent immutable facts, or None for whole-parser fallback."""
    if (not isinstance(plan, FragmentPlan) or plan.html != preview_html
            or not isinstance(plan.roots, tuple) or not isinstance(plan.before, str) or not isinstance(plan.after, str)
            or any(not isinstance(root, RootFragment) or not isinstance(root.html, str) for root in plan.roots)):
        return None
    nodes = project.by_id()
    roots = tuple(node.review.id for node in project.nodes if node.review.parent_id is None)
    if tuple(root.node_id for root in plan.roots) != roots or len(set(roots)) != len(roots):
        return None
    if any(_size(root.html) > _FRAGMENT_MAX_BYTES for root in plan.roots):
        return None
    if plan.before + ''.join(root.html for root in plan.roots) + plan.after != preview_html:
        return None
    try:
        dom_ids = _outside_context(plan)
        if dom_ids is None:
            return None
        dom_ids, owners, units = set(dom_ids), set(), []
        for root in plan.roots:
            if not root.html:
                # These are the renderer's only deliberately omitted root kinds.
                if nodes[root.node_id].review.type not in ('inline_math', 'citation', 'reference'):
                    return None
                continue
            facts = _facts(nodes, root)
            if facts is None or dom_ids.intersection(facts.dom_ids) or owners.intersection(facts.node_ids):
                return None
            dom_ids.update(facts.dom_ids)
            owners.update(facts.node_ids)
            units.extend(facts.units)
        # Disjoint actual owners prove all per-owner text/formula counters and
        # anonymous-formula IDs match the complete extractor. Global order is
        # assigned freshly by build_content in actual root/DOM sequence.
        return tuple(units)
    except (ValueError, RecursionError):
        return None
