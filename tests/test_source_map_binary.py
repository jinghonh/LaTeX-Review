"""Independent pre-index linear oracle and geometry-only fast-path proofs."""
from bisect import bisect_right
from itertools import product
import random
import re
from types import SimpleNamespace

import pytest

from latex_review import source_map as module
from latex_review.source_map import SourceMap, SourceSegment, OriginRange, MappedRange


class LinearSourceMap:
    """Frozen original constructor/projection; deliberately no indexed helpers."""
    def __init__(self, text, files, segments):
        self.text = text
        self.files = dict(files)
        self.segments = segments
        self._line_starts = {
            name: (0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", content)))
            for name, content in files.items()
        }
        cursor = 0
        for segment in segments:
            if segment.expanded_start != cursor or segment.expanded_end < cursor:
                raise ValueError("展开映射必须连续且有序")
            if segment.source_end - segment.source_start != segment.expanded_end - segment.expanded_start:
                raise ValueError("精确映射的原文与展开字符数必须相同")
            cursor = segment.expanded_end
        if cursor != len(text):
            raise ValueError("展开映射未覆盖全文")

    def _line_column(self, file, offset):
        starts = self._line_starts[file]
        index = bisect_right(starts, offset) - 1
        return index + 1, offset - starts[index] + 1

    def map_range(self, start, end):
        if start < 0 or end < start or end > len(self.text):
            raise ValueError("展开区间超出范围")
        result = []
        for segment in self.segments:
            left, right = max(start, segment.expanded_start), min(end, segment.expanded_end)
            if left >= right:
                continue
            origin_start = segment.source_start + left - segment.expanded_start
            origin_end = origin_start + right - left
            start_line, start_column = self._line_column(segment.file, origin_start)
            end_line, end_column = self._line_column(segment.file, origin_end)
            result.append(MappedRange(left, right, OriginRange(
                segment.file, origin_start, origin_end, start_line, start_column,
                end_line, end_column, segment.include_instance, segment.confidence,
            )))
        return tuple(result)


def outcome(mapping, start, end):
    try:
        # repr also distinguishes bool/float coordinates and handles legacy NaN.
        return ('returned', repr(mapping.map_range(start, end)))
    except Exception as error:
        return ('raised', type(error), str(error))


def pair(text, files, segments):
    return SourceMap(text, files, segments), LinearSourceMap(text, files, segments)


def assert_queries(mappings, queries):
    current, original = mappings
    for start, end in queries:
        assert outcome(current, start, end) == outcome(original, start, end), (start, end)


@pytest.mark.parametrize('seed', range(12))
def test_seeded_forty_file_unicode_repeated_includes_boundaries_and_random_ranges(seed):
    rng = random.Random(seed)
    alphabet = 'ab éα漢字😀\r\n '
    files = {f'file{i}.tex': f'File {i}\r\nUnicode😀漢字\rmore\n' + ''.join(rng.choice(alphabet) for _ in range(100))
             for i in range(40)}
    segments, parts, cursor = [], [], 0
    for index in range(120):
        name = f'file{rng.randrange(40)}.tex'
        left = rng.randrange(len(files[name]) + 1)
        right = left if index % 5 == 0 else rng.randrange(left, len(files[name]) + 1)
        raw = files[name][left:right]
        segments.append(SourceSegment(cursor, cursor + len(raw), name, left, right,
                                      f'include-{index}', 'exact' if index % 3 else 'unknown'))
        parts.append(raw)
        cursor += len(raw)
    text, segments = ''.join(parts), tuple(segments)
    mappings = pair(text, files, segments)
    assert mappings[0]._indexed_segments is segments
    points = {0, len(text)}
    for segment in segments:
        for boundary in (segment.expanded_start, segment.expanded_end):
            points.update(point for point in (boundary - 1, boundary, boundary + 1) if 0 <= point <= len(text))
    queries = [(point, point) for point in points]
    queries.extend((max(0, point - 1), min(len(text), point + 1)) for point in points)
    queries.extend((0, point) for point in sorted(points)[::7])
    queries.extend((point, len(text)) for point in sorted(points)[::7])
    for _ in range(250):
        start = rng.randrange(len(text) + 1)
        queries.append((start, rng.randrange(start, len(text) + 1)))
    queries.extend([(-1, 0), (0, -1), (2, 1), (0, len(text) + 1), (len(text) + 1, len(text) + 1), (0, 2**100)])
    assert_queries(mappings, queries)
    # Equal file/source fragments are not coalesced across include instances.
    actual = mappings[0].map_range(0, len(text))
    assert len(actual) == sum(s.expanded_start < s.expanded_end for s in segments)
    assert len({span.origin.include_instance for span in actual}) == len(actual)


def test_exhaustive_small_geometry_including_zero_width_duplicate_ends_and_empty_files():
    files = {'same.tex': 'α\r\n😀漢字', 'empty.tex': ''}
    for lengths in product(range(3), repeat=4):
        cursor, segments, parts = 0, [], []
        for index, length in enumerate(lengths):
            name = 'same.tex' if length else 'empty.tex'
            segments.append(SourceSegment(cursor, cursor + length, name, 0, length, f'occurrence-{index}'))
            parts.append(files[name][:length])
            cursor += length
        mappings = pair(''.join(parts), files, tuple(segments))
        assert_queries(mappings, [(start, end) for start in range(cursor + 1) for end in range(start, cursor + 1)])
    assert_queries(pair('', {}, ()), [(0, 0), (-1, 0), (0, 1)])


def test_last_character_query_does_not_visit_linear_prefix(monkeypatch):
    size = 4096
    segments = tuple(SourceSegment(i, i + 1, 'same.tex', i, i + 1, str(i)) for i in range(size))
    current, original = pair('x' * size, {'same.tex': 'x' * size}, segments)
    expected = original.map_range(size - 1, size)
    original_get = SourceSegment.__getattribute__
    visited = []
    def observed_get(self, name):
        if name in ('expanded_start', 'expanded_end'):
            visited.append(name)
        return original_get(self, name)
    monkeypatch.setattr(SourceSegment, '__getattribute__', observed_get)
    actual = current.map_range(size - 1, size)
    assert actual == expected and len(actual) == 1
    assert len(visited) <= 6  # Independent of the 4,095 preceding segments.


def test_reassignment_to_new_or_unsorted_tuple_and_list_preserves_original_scan(monkeypatch):
    segments = tuple(SourceSegment(i, i + 1, 'a.tex', i, i + 1, str(i)) for i in range(3))
    current, original = pair('abc', {'a.tex': 'abc'}, segments)
    bisect = module.bisect_right
    searches = []
    def tracked(values, offset):
        if values is current._segment_ends:
            searches.append(offset)
        return bisect(values, offset)
    monkeypatch.setattr(module, 'bisect_right', tracked)
    for replacement in (tuple(list(segments)), tuple(reversed(segments)), list(reversed(segments))):
        assert replacement is not segments
        current.segments = original.segments = replacement
        assert_queries((current, original), [(0, 1), (0, 3), (1, 2), (3, 3)])
        assert searches == []
    current.segments = original.segments = segments
    assert_queries((current, original), [(0, 1)])
    assert searches == [0]


def test_initial_mutable_list_and_mutable_duck_segments_keep_linear_semantics():
    segments = [SourceSegment(0, 1, 'a.tex', 0, 1, 'one'), SourceSegment(1, 2, 'a.tex', 1, 2, 'two')]
    current, original = pair('ab', {'a.tex': 'ab'}, segments)
    assert current._indexed_segments is None
    segments.reverse()
    assert_queries((current, original), [(0, 1), (0, 2)])
    ducks = tuple(SimpleNamespace(**vars(s)) for s in reversed(segments))
    current, original = pair('ab', {'a.tex': 'ab'}, ducks)
    assert current._indexed_segments is None
    ducks[0].expanded_start, ducks[0].expanded_end = 1, 2
    assert_queries((current, original), [(0, 1), (0, 2)])


def test_generator_constructor_and_later_generator_reassignment_preserve_consumption():
    segments = (SourceSegment(0, 1, 'a.tex', 0, 1, 'one'),)
    current = SourceMap('a', {'a.tex': 'a'}, iter(segments))
    original = LinearSourceMap('a', {'a.tex': 'a'}, iter(segments))
    assert current._indexed_segments is None
    assert_queries((current, original), [(0, 1), (0, 1)])  # Constructor consumed both generators.
    current, original = pair('a', {'a.tex': 'a'}, segments)
    current.segments, original.segments = iter(segments), iter(segments)
    assert_queries((current, original), [(0, 1), (0, 1)])  # First query consumes replacements.


def test_unindexed_none_reassignment_preserves_exact_legacy_iteration_error():
    current, original = pair('a', {'a.tex': 'a'}, [SourceSegment(0, 1, 'a.tex', 0, 1, 'one')])
    assert current._indexed_segments is None
    current.segments = original.segments = None
    assert_queries((current, original), [(0, 1)])
    assert outcome(current, 0, 1) == ('raised', TypeError, "'NoneType' object is not iterable")


class SegmentSubclass(SourceSegment):
    pass


class TupleSubclass(tuple):
    pass


@pytest.mark.parametrize('segments', [
    TupleSubclass((SourceSegment(0, 1, 'a.tex', 0, 1, 'one'),)),
    (SourceSegment(False, True, 'a.tex', 0, 1, 'one'),),
    (SegmentSubclass(0, 1, 'a.tex', 0, 1, 'one'),),
    (SourceSegment(0.0, 1.0, 'a.tex', 0, 1, 'one'),),
])
def test_nonstandard_initial_segment_geometry_falls_back(segments):
    current, original = pair('a', {'a.tex': 'a'}, segments)
    assert current._indexed_segments is None
    assert_queries((current, original), [(0, 1), (0, 0)])


class FancyInt(int):
    pass


@pytest.mark.parametrize('query', [(False, True), (0.0, 1.0), (FancyInt(0), FancyInt(1)),
                                 (float('nan'), 1), (0, float('nan')), (None, 1), ('0', 1)])
def test_nonstandard_query_types_keep_legacy_return_and_error_semantics(query, monkeypatch):
    current, original = pair('ab', {'a.tex': 'ab'}, (SourceSegment(0, 2, 'a.tex', 0, 2, 'one'),))
    bisect = module.bisect_right
    def no_index(values, offset):
        assert values is not current._segment_ends
        return bisect(values, offset)
    monkeypatch.setattr(module, 'bisect_right', no_index)
    assert_queries((current, original), [query])


def test_text_length_and_line_tables_are_still_read_fresh_without_result_memo():
    current, original = pair('a\nb', {'a.tex': 'a\nb'}, (SourceSegment(0, 3, 'a.tex', 0, 3, 'one'),))
    first = current.map_range(1, 3)
    assert first is not current.map_range(1, 3)
    for mapping in (current, original):
        mapping._line_starts['a.tex'] = (0, 1, 2)
        mapping.files['a.tex'] = 'ignored by the original projection too'
    assert_queries((current, original), [(1, 3)])
    assert current.map_range(1, 3) != first
    for mapping in (current, original):
        mapping.text = 'a'
    assert_queries((current, original), [(1, 3), (0, 1)])
    for mapping in (current, original):
        mapping._line_starts.pop('a.tex')
    assert_queries((current, original), [(0, 1)])


def test_overridden_line_column_method_is_called_fresh():
    current, original = pair('a', {'a.tex': 'a'}, (SourceSegment(0, 1, 'a.tex', 0, 1, 'one'),))
    for position in ((11, 12), (21, 22)):
        current._line_column = original._line_column = lambda file, offset: position
        assert_queries((current, original), [(0, 1)])
        assert current.map_range(0, 1)[0].origin.start_line == position[0]


@pytest.mark.parametrize('text,segments', [
    ('a', (SourceSegment(1, 2, 'a.tex', 0, 1, 'one'),)),
    ('a', (SourceSegment(0, -1, 'a.tex', 0, -1, 'one'),)),
    ('a', (SourceSegment(0, 1, 'a.tex', 0, 2, 'one'),)),
    ('aa', (SourceSegment(0, 1, 'a.tex', 0, 1, 'one'),)),
    ('aa', (SourceSegment(0, 1, 'a.tex', 0, 1, 'one'), SourceSegment(0, 1, 'a.tex', 0, 1, 'two'))),
])
def test_invalid_constructor_error_type_and_message_are_unchanged(text, segments):
    errors = []
    for constructor in (SourceMap, LinearSourceMap):
        with pytest.raises(ValueError) as error:
            constructor(text, {'a.tex': 'aa'}, segments)
        errors.append((type(error.value), str(error.value)))
    assert errors[0] == errors[1]
