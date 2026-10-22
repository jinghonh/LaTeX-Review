"""Collect authoritative preview numbers during an explicitly requested compilation."""
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import json
import re

from .numbering import _rows
from .structure import _argument, _masked, parse_project

_PREFIX = 'latex-review-number-'
_HELPER = r'''
\makeatletter
\newcommand{\latexreviewnumber}[1]{%
  \@ifundefined{ifmeasuring@}{\latexreviewwritenumber{#1}}{%
    \ifmeasuring@\else\latexreviewwritenumber{#1}\fi}%
}
\newcommand{\latexreviewwritenumber}[1]{%
  \ifcsname latexreviewcount#1\endcsname\else
    \expandafter\newcount\csname latexreviewcount#1\endcsname
  \fi
  \global\advance\csname latexreviewcount#1\endcsname by 1
  \@ifundefined{df@label}{\latexreviewlabel{#1}}{%
    \ifx\df@label\@empty\latexreviewlabel{#1}\else
      \protected@write\@auxout{}{\string\latexreviewalias
        {latex-review-number-#1-\number\csname latexreviewcount#1\endcsname}{\df@label}}%
    \fi}%
}
\newcommand{\latexreviewlabel}[1]{\label{latex-review-number-#1-\number\csname latexreviewcount#1\endcsname}}
\providecommand{\latexreviewalias}[2]{}
\makeatother
'''
# Keep SyncTeX line numbers identical to the original source.
_HELPER = ''.join(line.split('%', 1)[0].strip() for line in _HELPER.splitlines())


def instrument(source, directory: Path) -> dict:
    """Insert only zero-width labels in the private copy; original files stay untouched."""
    project = parse_project(source.expand())
    insertions = defaultdict(dict)
    occurrences = defaultdict(int)
    mapping = {'nodes': {}, 'rows': {}}

    def add(node, offset, row=None):
        origins = project.expanded.origin_ranges(offset - 1, offset)
        if len(origins) != 1 or origins[0].origin.confidence != 'exact':
            return
        origin = origins[0].origin
        key = sha256(f'{origin.file}:{origin.end}'.encode()).hexdigest()[:20]
        occurrences[key] += 1
        label = f'{_PREFIX}{key}-{occurrences[key]}'
        insertions[origin.file][origin.end] = r'\latexreviewnumber{' + key + '}'
        if row is None:
            mapping['nodes'][node.review.id] = label
        else:
            mapping['rows'].setdefault(node.review.id, {})[str(row)] = label

    for node in sorted(project.nodes, key=lambda node: node.expanded_start):
        raw = _masked(node.review.raw_latex)
        if node.review.type in {'part', 'chapter', 'section', 'subsection', 'subsubsection'}:
            if not re.match(r'\\[A-Za-z]+\*', raw):
                add(node, node.expanded_end)
        elif node.review.type in {'figure', 'table'}:
            caption = re.search(r'\\caption(?![A-Za-z@*])', raw)
            arg = _argument(raw, caption.end()) if caption else None
            if arg:
                add(node, node.expanded_start + arg[0])
        elif node.review.type == 'equation':
            match = re.match(r'\\begin\s*\{([^{}]+)\}(?:\s*\[[^\]]*\])?', raw)
            if not match or match.group(1).rstrip('*') not in {'equation', 'align', 'alignat', 'flalign', 'gather', 'multline'}:
                continue
            env = match.group(1)
            start = match.end()
            if env.rstrip('*') == 'alignat':
                arg = _argument(raw, start)
                start = arg[0] if arg else start
            body = raw[start:raw.rfind(r'\end')]
            rows = _rows(body) if env.rstrip('*') in {'align', 'alignat', 'flalign', 'gather'} else [(0, len(body))]
            for index, (left, right) in enumerate(rows):
                row = body[left:right]
                # amsmath permits only one label per row. Existing labels already carry the exact number.
                labels = re.findall(r'\\label\s*\{([^{}]+)\}', row)
                if labels:
                    mapping['rows'].setdefault(node.review.id, {})[str(index)] = labels[0]
                elif (not env.endswith('*') and not re.search(r'\\(?:nonumber|notag)\b', row)) or re.search(r'\\tag\*?\s*\{', row):
                    add(node, node.expanded_start + start + right, index)
    for file, edits in insertions.items():
        path = directory / file
        raw = path.read_text(encoding='utf-8')
        for offset, label in sorted(edits.items(), reverse=True):
            raw = raw[:offset] + label + raw[offset:]
        path.write_text(raw, encoding='utf-8')
    prepare_entry(directory / source.entry)
    return mapping


def prepare_entry(entry: Path) -> None:
    """Shared instrumented files may also be included by a companion entry."""
    raw = entry.read_text(encoding='utf-8')
    if _HELPER in raw:
        return
    document = re.search(r'\\begin\s*\{document\}', _masked(raw))
    if document:
        raw = raw[:document.start()] + _HELPER + raw[document.start():]
        entry.write_text(raw, encoding='utf-8')


def collect(build: Path, mapping: dict, entry: str | None = None) -> bytes:
    labels = {}
    aliases = {}
    paths = sorted(build.rglob('*.aux'), key=lambda path: (path.stem == entry, str(path)))
    for path in paths:
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            continue
        text = _masked(path.read_text(encoding='utf-8', errors='replace'))
        for match in re.finditer(r'\\newlabel\s*\{([^{}]+)\}\s*\{', text):
            arg = _argument(text, match.end() - 1)
            number = _argument(arg[1], 0) if arg else None
            if number and len(number[1]) < 256:
                labels[match.group(1)] = number[1]
        aliases.update(re.findall(r'\\latexreviewalias\s*\{([^{}]+)\}\s*\{([^{}]+)\}', text))
    for key, target in aliases.items():
        if target in labels:
            labels[key] = labels[target]
    return json.dumps({'labels': {key: value for key, value in labels.items() if not key.startswith(_PREFIX)},
                       'nodes': {key: labels[value] for key, value in mapping['nodes'].items() if value in labels},
                       'rows': {key: {index: labels[label] for index, label in rows.items() if label in labels}
                                for key, rows in mapping['rows'].items()}}, ensure_ascii=False).encode('utf-8')
