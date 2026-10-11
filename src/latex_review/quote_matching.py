"""Exact LaTeX quote matching and bounded, advisory failure candidates."""
import difflib
import re

from .annotations import utf16_length

MATH = re.compile(r'(?<!\\)\$\$(.*?)\$\$|(?<!\\)\$(.*?)\$|\\\((.*?)\\\)|\\\[(.*?)\\\]', re.S)


def normalize_quote(text):
    return re.sub(r'[ \t\r\n\f]+', ' ', text).strip(' ')


def normalize_formula(text):
    match = MATH.fullmatch(text.strip())
    body = next(group for group in match.groups() if group is not None) if match else text.strip()
    # Spaces in textual math commands carry meaning. Keep them conservatively.
    if re.search(r'\\(?:text|textrm|textsf|texttt|mbox|hbox)\s*\{', body):
        return body.strip()
    # Whitespace separates control words from following letters. Preserve that boundary.
    return re.sub(r'\s+', '', re.sub(r'(\\[A-Za-z]+)\s+(?=[A-Za-z])', lambda m: m.group(1) + '\x00', body))


def quote_atoms(text):
    formulas = []
    def replace(match):
        formulas.append(normalize_formula(match.group()))
        return '\ufffc'
    return normalize_quote(MATH.sub(replace, text)), formulas


def matches(unit, text, formulas):
    start = 0
    while (index := unit['text'].find(text, start)) >= 0:
        begin = utf16_length(unit['text'][:index])
        end = begin + utf16_length(text)
        atoms = [normalize_formula(a['raw_latex']) for a in unit['atoms'] if begin <= a['offset'] < end]
        # Legacy U+FFFC quotes intentionally do not specify formula content.
        if not formulas or (text.count('\ufffc') == len(formulas) and atoms == formulas):
            yield {'unit_id': unit['id'], 'side': 'new', 'quote': text, 'start': begin, 'end': end}
        start = index + 1


def distance(left, right):
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        new = [i]
        for j, b in enumerate(right, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = new
    return row[-1]


def nearest(units, quote, limit=3):
    candidates = []
    for unit in units:
        text = unit['raw_latex'] if unit['kind'] == 'formula' else unit['text']
        if unit['kind'] == 'text':
            for atom in reversed(unit['atoms']):
                prefix = 0
                for index, char in enumerate(text):
                    if prefix == atom['offset']:
                        text = text[:index] + atom['raw_latex'] + text[index + 1:]
                        break
                    prefix += utf16_length(char)
        normalized = normalize_quote(text)
        # Bound diagnostic work and output, without modifying locating content.
        requested = quote[:512]
        block = difflib.SequenceMatcher(None, requested, normalized, autojunk=False).find_longest_match()
        start = max(0, block.b - block.a)
        comparison = normalized[start:start + len(requested)]
        candidates.append({'unit_id': unit['id'], 'source': unit['source'],
                           'section_path': unit['section_path'],
                           'context': normalized[max(0, start - 80):start + len(comparison) + 80],
                           'text': comparison, 'distance': distance(requested, comparison),
                           'diff': list(difflib.ndiff([requested], [comparison]))})
    return sorted(candidates, key=lambda c: c['distance'])[:limit]
