"""Conservative original-source ranges for annotation units.

Offsets are Unicode character offsets, zero based and half open. A range is
reliable only when its owner and the requested range map continuously to one
original file. Rendered text is never searched across a whole source file.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import re

from .structure import _argument, _masked, _SAFE_COMMANDS
from .table_model import parse_table


def _unavailable(reason: str) -> dict:
    return {'reliable': False, 'writable': False, 'reason': reason}


def _continuous(project, start: int, end: int):
    origins = project.expanded.origin_ranges(start, end)
    if not origins or origins[0].expanded_start != start or origins[-1].expanded_end != end:
        return None
    first = origins[0].origin
    previous = origins[0]
    if first.confidence != 'exact':
        return None
    for mapped in origins[1:]:
        origin = mapped.origin
        if (origin.confidence != 'exact' or origin.file != first.file
                or origin.include_instance != first.include_instance
                or previous.expanded_end != mapped.expanded_start
                or previous.origin.end != origin.start):
            return None
        previous = mapped
    return first, origins[-1].origin


def _span(project, start: int, end: int) -> tuple[dict, dict | None]:
    mapped = _continuous(project, start, end)
    if mapped is None:
        return _unavailable('源码区间不是可靠、连续、单文件的原始区间'), None
    first, last = mapped
    full_text = project.expanded.source_map.files[first.file]
    chunk = full_text[first.start:last.end]
    if chunk != project.expanded.text[start:end]:
        return _unavailable('展开内容与原始源码不一致'), None
    writable = project.expanded.source.identity.kind != 'git'
    span = {'file': first.file, 'start': first.start, 'end': last.end,
            'sha256': sha256(full_text.encode('utf-8')).hexdigest(),
            'original_chunk': chunk, 'reliable': True, 'writable': writable,
            'reason': None if writable else 'Git 历史比较版本只读'}
    location = {'file': first.file, 'start_line': first.start_line,
                'end_line': last.end_line, 'start_column': first.start_column,
                'end_column': last.end_column, 'confidence': 1.0,
                'uncertainty_reason': None}
    return span, location


def _caption_range(raw: str, macros: dict) -> tuple[tuple[int, int] | None, str]:
    mask = _masked(raw)
    captions = list(re.finditer(r'\\caption(?![A-Za-z@])', mask))
    if len(captions) != 1:
        return None, '图注数量或结构不确定'
    argument = _argument(mask, captions[0].end())
    if argument is None:
        return None, '图注参数无法解析'
    start, end = argument[2], argument[0] - 1
    for command in re.finditer(r'\\([A-Za-z@]+)', mask[start:end]):
        if (command.group(1) in macros or command.group(1) not in _SAFE_COMMANDS
                or command.group(1) in ('begin', 'end', 'caption')):
            return None, '图注含复杂宏'
    return (start, end), ''


def enrich_content(project, units: list[dict], project_root: Path | None = None) -> None:
    """Enrich units in place without changing IDs, kinds or change membership.

    The preview extractor passes ``subunit`` as ``{type: caption|header|cell,
    row?, column?, table_index?}``. Table coordinates and table_index start at 1.
    First-row cells use the header identity; this is a structural convention,
    not an assertion that the author declared a semantic table header.
    """
    nodes = project.by_id()
    counts = Counter(unit['node_id'] for unit in units if unit['kind'] == 'text')
    for unit in units:
        owner = nodes.get(unit['node_id'])
        if owner is None or _continuous(project, owner.expanded_start, owner.expanded_end) is None:
            unit['source_span'] = _unavailable('所属节点的原始源码区间不可靠或跨文件')
            continue
        raw = owner.review.raw_latex
        if raw != project.expanded.text[owner.expanded_start:owner.expanded_end]:
            unit['source_span'] = _unavailable('所属节点不是原始源码内容')
            continue
        subunit = unit.get('subunit')
        interval = None
        reason = '预览子块不能唯一对应所属源码节点'
        if subunit and subunit.get('type') == 'caption':
            interval, reason = _caption_range(raw, project.macros)
        elif subunit and subunit.get('type') in ('cell', 'header'):
            grid, reason = parse_table(raw)
            if grid is not None and subunit.get('table_index', 1) == 1:
                row, column = subunit.get('row', 0), subunit.get('column', 0)
                if (isinstance(row, int) and isinstance(column, int)
                        and 1 <= row <= len(grid.rows) and 1 <= column <= grid.columns):
                    interval = grid.cell_ranges[row - 1][column - 1]
                    if any(command.group(1) in project.macros for command in
                           re.finditer(r'\\([A-Za-z@]+)', _masked(raw[interval[0]:interval[1]]))):
                        interval, reason = None, '单元格含复杂宏'
                else:
                    reason = '单元格结构坐标不确定'
            elif grid is not None:
                reason = '多个表格无法可靠对应'
        elif unit['kind'] == 'formula':
            expression = unit['raw_latex']
            if expression == raw:
                interval = (0, len(raw))
            elif expression:
                matches = list(re.finditer(re.escape(expression), raw))
                if len(matches) == 1:
                    interval = (matches[0].start(), matches[0].end())
        elif counts[unit['node_id']] == 1 and owner.review.type not in ('table', 'figure'):
            interval = (0, len(raw))
        if interval is None:
            unit['source_span'] = _unavailable(reason)
            continue
        start, end = interval
        span, location = _span(project, owner.expanded_start + start, owner.expanded_start + end)
        unit['source_span'] = span
        if location is not None:
            unit['source'] = location
            unit['raw_latex'] = raw[start:end]
