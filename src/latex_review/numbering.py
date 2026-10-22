"""Read compiled labels and evaluate common LaTeX counter declarations without TeX execution."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING

from .structure import _argument, _group_end, _masked

if TYPE_CHECKING:
    from .structure import ParsedProject

_HEADINGS = ('part', 'chapter', 'section', 'subsection', 'subsubsection')
_COMMAND = re.compile(r'\\([A-Za-z@]+|.)', re.S)


def _ungroup(value: str) -> str:
    value = value.strip()
    while value.startswith('{') and _group_end(value, 0) == len(value):
        value = value[1:-1].strip()
    box = re.fullmatch(r'\\(?:mbox|textnormal|textrm)\s*\{(.*)\}', value, re.S)
    if box:
        return _ungroup(box.group(1))
    return value


@dataclass
class Numbering:
    nodes: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # Replacement TeX uses explicit tags, so each side has independent counters.
    equations: dict[str, str] = field(default_factory=dict)
    unknown: set[str] = field(default_factory=set)


def _roman(value: int) -> str:
    result = ''
    for number, symbol in ((1000, 'm'), (900, 'cm'), (500, 'd'), (400, 'cd'), (100, 'c'),
                           (90, 'xc'), (50, 'l'), (40, 'xl'), (10, 'x'), (9, 'ix'), (5, 'v'), (4, 'iv'), (1, 'i')):
        count, value = divmod(max(0, value), number)
        result += symbol * count
    return result


class _Counters:
    def __init__(self, book: bool):
        self.values = dict.fromkeys((*_HEADINGS, 'paragraph', 'subparagraph', 'figure', 'table', 'equation'), 0)
        self.values['secnumdepth'] = 2 if book else 3
        self.formats = {name: r'\arabic{' + name + '}' for name in self.values}
        self.formats['part'] = r'\Roman{part}'
        self.resets = {'chapter': {'section', 'figure', 'table', 'equation'}, 'section': {'subsection'},
                       'subsection': {'subsubsection'}} if book else {'section': {'subsection'}, 'subsection': {'subsubsection'}}
        for name, parent in (('section', 'chapter'), ('subsection', 'section'), ('subsubsection', 'subsection')):
            if parent != 'chapter' or book:
                self.formats[name] = '\\the' + parent + '.' + self.formats[name]
        if book:
            for name in ('figure', 'table', 'equation'):
                self.formats[name] = r'\thechapter.\arabic{' + name + '}'

    def step(self, name: str, seen: frozenset[str] = frozenset()) -> None:
        self.values[name] = self.values.get(name, 0) + 1
        def reset(parent, visited):
            for child in self.resets.get(parent, ()):
                if child in visited:
                    continue
                self.values[child] = 0
                reset(child, visited | {child})
        reset(name, seen | {name})

    def expand(self, text: str, seen: frozenset[str] = frozenset()) -> str | None:
        def replace(match):
            style, name = match.group(1), match.group(2)
            value = self.values.get(name)
            if value is None or abs(value) > 10000:
                raise ValueError
            if style == 'arabic':
                return str(value)
            if style in {'roman', 'Roman'}:
                result = _roman(value)
                return result.upper() if style == 'Roman' else result
            if style in {'alph', 'Alph'} and 0 <= value <= 26:
                result = chr(96 + value) if value else ''
                return result.upper() if style == 'Alph' else result
            raise ValueError
        try:
            text = re.sub(r'\\(arabic|roman|Roman|alph|Alph)\s*\{([^{}]+)\}', replace, text)
            def macro(match):
                name = match.group(1)
                if name in seen or name not in self.formats or len(seen) >= 16:
                    raise ValueError
                result = self.expand(self.formats[name], seen | {name})
                if result is None:
                    raise ValueError
                return result
            text = re.sub(r'\\the([A-Za-z@]+)', macro, text)
        except ValueError:
            return None
        # Unknown TeX is never presented as a confirmed number.
        return None if '\\' in text or '#' in text else text.replace('{', '').replace('}', '').strip()

    def number(self, name: str) -> str | None:
        return self.expand(self.formats.get(name, ''), frozenset({name}))


def _compiled_labels(project: ParsedProject) -> dict[str, str]:
    """Only read root-contained, current aux files. Never execute their contents."""
    source = project.expanded.source
    root = source.root.resolve()
    entry = source.entry
    from pathlib import Path
    stem = Path(entry).with_suffix('.aux')
    candidates = (root / stem, root / 'build' / stem, root / 'build' / stem.name)
    inputs = {root / entry, *(root / origin.origin.file for node in project.nodes for origin in node.origins
                              if origin.origin.file)}
    inputs.update(root / dependency.file for dependency in project.expanded.dependencies
                  if dependency.kind in {'input', 'include'})
    try:
        newest = max(path.stat().st_mtime_ns for path in inputs if path.is_file())
    except (OSError, ValueError):
        return {}
    for candidate in candidates:
        labels = {}
        visited = set()
        def read(path):
            path = path.resolve()
            if path in visited:
                return
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError
            if path.stat().st_mtime_ns < newest or len(visited) >= 128:
                raise ValueError
            visited.add(path)
            text = _masked(path.read_text(encoding='utf-8'))
            for match in re.finditer(r'\\newlabel\s*\{([^{}]+)\}\s*\{', text):
                arg = _argument(text, match.end() - 1)
                first = _argument(arg[1], 0) if arg else None
                if first and '\\' not in first[1] and len(first[1]) < 256:
                    labels[match.group(1)] = _ungroup(first[1])
            for match in re.finditer(r'\\@input\s*\{([^{}]+\.aux)\}', text):
                read(candidate.parent / match.group(1))
        try:
            read(candidate)
        except (OSError, UnicodeError, ValueError):
            continue
        if labels:
            return labels
    return {}


def _rows(raw: str) -> list[tuple[int, int]]:
    """Split AMS rows, while keeping nested aligned/split/matrix rows intact."""
    start = 0
    depth = 0
    rows = []
    for match in re.finditer(r'\\(begin|end)\s*\{[^{}]+\}|\\\\(?:\*)?(?:\s*\[[^\]]*\])?', _masked(raw)):
        if match.group(1) == 'begin':
            depth += 1
        elif match.group(1) == 'end':
            depth -= 1
        elif depth == 0:
            rows.append((start, match.start()))
            start = match.end()
    if raw[start:].strip():
        rows.append((start, len(raw)))
    return rows


def resolve_numbering(project: ParsedProject, metadata: dict | None = None) -> Numbering:
    result = Numbering()
    text = _masked(project.expanded.text)
    class_match = re.search(r'\\documentclass(?:\[[^\]]*\])?\s*\{([^{}]+)\}', text)
    book = bool(class_match and class_match.group(1) in {'book', 'report', 'scrbook', 'scrreprt', 'memoir'})
    counters = _Counters(book)
    if class_match and class_match.group(1) not in {'article', 'book', 'report'}:
        # A custom class can redefine every printed counter; do not guess article defaults.
        for name in (*_HEADINGS, 'figure', 'table', 'equation'):
            counters.formats[name] = r'\unknown'
    metadata = metadata or {}
    compiled = {key: _ungroup(value) for key, value in {**_compiled_labels(project), **metadata.get('labels', {})}.items()}
    nodes = {node.expanded_start: node for node in project.nodes
             if node.review.type in {*_HEADINGS, 'equation'}}
    floats = [node for node in project.nodes if node.review.type in {'figure', 'table'}]
    current = None
    cursor = 0
    subequations = []

    def assign(node, value):
        value = metadata.get('nodes', {}).get(node.review.id, value)
        value = _ungroup(value) if value is not None else None
        if value is None:
            result.unknown.add(node.review.id)
        elif value:
            result.nodes[node.review.id] = value

    def equation(node):
        raw = node.review.raw_latex
        match = re.match(r'\\begin\s*\{([^{}]+)\}(?:\s*\[[^\]]*\])?', raw)
        if not match:
            return
        env = match.group(1)
        if env not in {'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*', 'flalign', 'flalign*', 'gather', 'gather*', 'multline', 'multline*'}:
            return
        start = match.end()
        if env.rstrip('*') == 'alignat':
            arg = _argument(raw, start)
            start = arg[0] if arg else start
        end = raw.rfind(r'\end')
        body = raw[start:end]
        rows = _rows(body) if env.rstrip('*') in {'align', 'alignat', 'flalign', 'gather'} else [(0, len(body))]
        edits = []
        for index, (left, right) in enumerate(rows):
            row = body[left:right]
            tag = re.search(r'\\tag(\*)?\s*\{', _masked(row))
            argument = _argument(row, tag.end() - 1) if tag else None
            numbered = not env.endswith('*') and not re.search(r'\\(?:nonumber|notag)(?![A-Za-z@])', _masked(row))
            keys = re.findall(r'\\label\s*\{([^{}]+)\}', _masked(row))
            value = argument[1] if argument else None
            if tag is None and numbered:
                counters.step('equation')
                value = counters.number('equation')
            value = next((compiled[key] for key in keys if key in compiled), value)
            value = metadata.get('rows', {}).get(node.review.id, {}).get(str(index), value)
            value = _ungroup(value) if value is not None else None
            if numbered or tag:
                assign(node, value)
            for key in keys:
                if value is not None:
                    result.labels[key] = value
            if value is not None:
                if argument:
                    edits.append((start + left + tag.start(), start + left + argument[0], ''))
                edits.append((start + right, start + right, r'\tag' + ('*' if tag and tag.group(1) else '') + '{' + value + '}'))
        for left, right, replacement in sorted(edits, reverse=True):
            raw = raw[:left] + replacement + raw[right:]
        result.equations[node.review.id] = raw

    while match := _COMMAND.search(text, cursor):
        name = match.group(1)
        at = match.start()
        cursor = match.end()
        if name in {'begin', 'end'}:
            env = _argument(text, cursor)
            if env and env[1] == 'subequations':
                if name == 'begin':
                    counters.step('equation')
                    parent = counters.number('equation')
                    subequations.append((counters.values['equation'], counters.formats['equation']))
                    current = parent
                    counters.values['equation'] = 0
                    counters.formats['equation'] = (parent + r'\alph{equation}') if parent is not None else r'\unknown'
                elif subequations:
                    counters.values['equation'], counters.formats['equation'] = subequations.pop()
                cursor = env[0]
                continue
        node = nodes.get(at)
        if node and node.review.type == 'equation':
            equation(node)
            cursor = node.expanded_end
            continue
        if name in {'newcommand', 'renewcommand', 'providecommand', 'DeclareRobustCommand', 'def', 'gdef', 'edef', 'xdef'}:
            target = _argument(text, cursor)
            direct = re.match(r'\s*\\([A-Za-z@]+)', text[cursor:])
            if direct:
                macro = direct.group(1)
                after = cursor + direct.end()
            elif target:
                macro = target[1].lstrip('\\')
                after = target[0]
            else:
                continue
            body = _argument(text, after)
            if body:
                if macro.startswith('the'):
                    counters.formats[macro[3:]] = body[1]
                cursor = body[0]
            continue
        if name in {'setcounter', 'addtocounter', 'counterwithin', 'counterwithout', 'numberwithin', 'newcounter'}:
            first = _argument(text, cursor)
            second = _argument(text, first[0]) if first else None
            if not first:
                continue
            key = first[1]
            if name == 'newcounter':
                counters.values[key] = 0
                counters.formats[key] = r'\arabic{' + key + '}'
                cursor = first[0]
                continue
            if not second:
                continue
            cursor = second[0]
            if name in {'setcounter', 'addtocounter'}:
                try:
                    value = int(second[1])
                    counters.values[key] = (counters.values.get(key, 0) if name == 'addtocounter' else 0) + value
                except ValueError:
                    counters.formats[key] = r'\unknown'
            else:
                parent = second[1]
                counters.resets.setdefault(parent, set())
                if name == 'counterwithout':
                    counters.resets[parent].discard(key)
                else:
                    counters.resets[parent].add(key)
                if not text[match.end():].startswith('*'):
                    counters.formats[key] = (('\\the' + parent + '.') if name != 'counterwithout' else '') + r'\arabic{' + key + '}'
            continue
        if name in {'stepcounter', 'refstepcounter'}:
            arg = _argument(text, cursor)
            if arg:
                counters.step(arg[1])
                if name == 'refstepcounter':
                    current = counters.number(arg[1])
                cursor = arg[0]
            continue
        if name == 'appendix':
            main = 'chapter' if book else 'section'
            counters.values[main] = counters.values['section' if book else 'subsection'] = 0
            counters.formats[main] = r'\Alph{' + main + '}'
            continue
        if node and name in _HEADINGS:
            arg = _argument(text, cursor)
            if arg:
                cursor = arg[0]
            level = {'part': -1 if book else 0, 'chapter': 0, 'section': 1, 'subsection': 2, 'subsubsection': 3}[name]
            if not text[match.end():].startswith('*') and level <= counters.values['secnumdepth']:
                counters.step(name)
                current = counters.number(name)
                current = next((compiled[key] for key in node.labels if key in compiled), current)
                assign(node, current)
            continue
        if name == 'caption':
            owner = next((item for item in floats if item.expanded_start < at < item.expanded_end), None)
            if owner and not text[match.end():].startswith('*'):
                counters.step(owner.review.type)
                current = counters.number(owner.review.type)
                current = next((compiled[key] for key in owner.labels if key in compiled), current)
                assign(owner, current)
            arg = _argument(text, cursor)
            cursor = arg[0] if arg else cursor
            continue
        if name == 'label':
            arg = _argument(text, cursor)
            if arg:
                value = compiled.get(arg[1], current)
                if value is not None:
                    result.labels[arg[1]] = value
                cursor = arg[0]
        # Do not interpret counter commands inside ordinary command arguments.
        elif name not in {'begin', 'end'}:
            arg = _argument(text, cursor)
            if arg:
                cursor = arg[0]
    result.labels.update(compiled)
    return result
