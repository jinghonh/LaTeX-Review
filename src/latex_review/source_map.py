"""展开文本与原始文件之间的无损字符位置映射。"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class OriginRange:
    file: str
    start: int
    end: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    include_instance: str
    confidence: str = "exact"


@dataclass(frozen=True)
class MappedRange:
    """展开区间及对应的一个原文区间，均为 Unicode 字符零起始半开区间。"""

    expanded_start: int
    expanded_end: int
    origin: OriginRange


@dataclass(frozen=True)
class SourceSegment:
    expanded_start: int
    expanded_end: int
    file: str
    source_start: int
    source_end: int
    include_instance: str
    confidence: str = "exact"


class SourceMap:
    """精确段保留原文字符；调用者负责将多来源节点保留为多个映射结果。"""

    def __init__(self, text: str, files: dict[str, str], segments: tuple[SourceSegment, ...]):
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

    def _line_column(self, file: str, offset: int) -> tuple[int, int]:
        starts = self._line_starts[file]
        index = bisect_right(starts, offset) - 1
        return index + 1, offset - starts[index] + 1

    def map_range(self, start: int, end: int) -> tuple[MappedRange, ...]:
        """返回每个相交来源；不得将跨文件或重复包含合并为一个范围。"""
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
