"""The cached interval sweep preserves the former adapter's exact ordering."""
from random import Random
from types import SimpleNamespace

from latex_review.checks.document import CheckDocument


def test_unit_sweep_matches_all_node_reference_for_nested_crossing_and_ties():
    for seed in range(12):
        random = Random(seed)
        spans = [(0, 100), (10, 20), (10, 20), (15, 30), (20, 20)]
        spans += [(left := random.randrange(100), left + random.randrange(0, 40)) for _ in range(80)]
        nodes = tuple(SimpleNamespace(expanded_start=left, expanded_end=right,
                                     review=SimpleNamespace(id=str(index), type='paragraph', section_path=('Section',)))
                      for index, (left, right) in enumerate(spans))
        document = CheckDocument(None, SimpleNamespace(nodes=nodes))
        object.__setattr__(document, 'evidence', lambda left, right: [{'start': left, 'end': right}])
        boundaries = sorted({value for span in spans for value in span})
        expected = []
        for left, right in zip(boundaries, boundaries[1:]):
            owners = [node for node in nodes if node.expanded_start <= left and right <= node.expanded_end]
            if not owners or left == right:
                continue
            owner = min(owners, key=lambda node: node.expanded_end - node.expanded_start)
            expected.append({'node_id': owner.review.id, 'kind': 'paragraph', 'section_path': ['Section'],
                             'reading_order': len(expected), 'expanded_start': left, 'expanded_end': right,
                             'representation': 'raw_latex', 'evidence': [{'start': left, 'end': right}]})
        assert document.units == expected
        assert document.units is document.units
