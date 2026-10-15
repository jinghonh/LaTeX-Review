"""Project-shared annotations, versioned preview coordinates and conservative relocation.

All public values are JSON objects. Text offsets are UTF-16 code units, as in DOM
Range. Formula atoms count as one U+FFFC; MathJax descendants never supply offsets.
"""
from copy import deepcopy
from difflib import SequenceMatcher
from html.parser import HTMLParser
import json
import re
from pathlib import Path

from ._validation import (MANIFEST, SCHEMA_VERSION, AnnotationConflict, AnnotationError, digest, _integer,
                          exact_keys, _slice, _text, utf16_length)



class _Element:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def selector(self):
        if self.attrs.get('data-node-id'):
            return '[data-side="new"] [id=' + json.dumps(self.attrs['id']) + ']'
        siblings = [x for x in self.parent.children if isinstance(x, _Element)]
        return self.parent.selector() + f' > {self.tag}:nth-child({siblings.index(self) + 1})'


class _PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in ('img', 'br', 'hr', 'meta', 'link', 'input', 'source', 'wbr'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _semantic_units(units):
    return [{k: ([{a: b for a, b in atom.items() if a != 'selector'} for atom in v] if k == 'atoms' else v)
             for k, v in unit.items() if k not in ('selector', 'change_ids')} for unit in units]


def build_content(project, preview_html: str, *, project_root: Path | None = None, changes=()):
    """Extract actual preview text, including unmodified prose and degraded formulas.

    Call while the source snapshot exists. Content identity includes resolved source
    text, bibliography/macro interpretation and semantic preview coordinates.
    Selectors and comparison-specific change membership do not affect identity.
    """
    from dataclasses import asdict
    from ..cache import dependency_fingerprints
    root = (project_root or project.expanded.source.worktree_origin or project.expanded.source.root).resolve()
    entry = project.expanded.source.entry
    project_id = digest({'root': str(root), 'entry': entry})
    nodes = {n.review.id: n for n in project.nodes}
    parser = _PreviewParser()
    parser.feed(preview_html)
    blocks, formulas, counters = [], [], {}
    block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'figcaption', 'td', 'th', 'blockquote', 'li'}

    def descendants(element):
        for child in element.children:
            if isinstance(child, _Element):
                yield child
                yield from descendants(child)

    def node_owner(element):
        while element and element.attrs.get('data-node-id') not in nodes:
            element = element.parent
        return nodes[element.attrs['data-node-id']] if element else None

    def display_only(element):
        while element:
            if {'asset', 'asset-card', 'asset-metadata', 'asset-fallback'} & set(element.attrs.get('class', '').split()):
                return True
            element = element.parent
        return False

    def contents(element, atoms):
        if display_only(element):
            return ''
        if 'data-annotation-latex' in element.attrs:
            owner = node_owner(element)
            raw = element.attrs['data-annotation-latex']
            formula_id = owner.review.id if owner and owner.review.type in ('inline_math', 'equation') else None
            atoms.append({'offset': 0, 'formula_node_id': formula_id, 'raw_latex': raw,
                          'selector': element.selector()})
            return '\ufffc'
        text = ''
        for child in element.children:
            if isinstance(child, str):
                text += child
            else:
                child_atoms = []
                piece = contents(child, child_atoms)
                for atom in child_atoms:
                    atom['offset'] += utf16_length(text)
                    atoms.append(atom)
                text += piece
        return text

    for side in descendants(parser.root):
        if 'preview-side' not in side.attrs.get('class', '').split() or side.attrs.get('data-side') != 'new':
            continue
        for element in descendants(side):
            if display_only(element):
                continue
            owner = node_owner(element)
            if not owner:
                continue
            if 'data-annotation-latex' in element.attrs:
                ordinal = sum(f['node_id'] == owner.review.id for f in formulas)
                formulas.append({'id': f'{owner.review.id}:formula:{ordinal}', 'kind': 'formula',
                                 'node_id': owner.review.id, 'parent_node_id': owner.review.parent_id,
                                 'raw_latex': element.attrs['data-annotation-latex'],
                                 'text': element.attrs['data-annotation-latex'], 'selector': element.selector(),
                                 'source': asdict(owner.review.source), 'section_path': list(owner.review.section_path),
                                 'labels': list(owner.labels), 'atoms': []})
                continue
            # Each leaf block is a paragraph boundary. Container nodes cannot merge paragraphs.
            is_block = element.tag in block_tags
            is_leaf_node = (element.attrs.get('data-node-id') in nodes and
                            owner.review.type not in ('equation', 'inline_math', 'math_array', 'comment', 'bibliography_change'))
            if not (is_block or is_leaf_node):
                continue
            if any(x.tag in block_tags or x.attrs.get('data-node-id') in nodes for x in descendants(element) if not display_only(x)
                   if x.attrs.get('data-node-id') not in nodes or nodes[x.attrs['data-node-id']].review.type not in ('inline_math', 'citation', 'reference')):
                continue
            if 'preview-unavailable' in element.attrs.get('class', '').split():
                continue
            atoms = []
            raw_text = contents(element, atoms)
            def normalize(value):
                return re.sub(r'[ \t\r\n\f]+', ' ', value).strip(' ')
            # Sentence highlighting introduces additional DOM whitespace. Coordinates
            # follow visible collapsed whitespace and remain stable across report/live.
            for atom in atoms:
                before = raw_text.encode('utf-16-le')[:atom['offset'] * 2].decode('utf-16-le')
                normalized_before = re.sub(r'[ \t\r\n\f]+', ' ', before).lstrip(' ')
                atom['offset'] = utf16_length(normalized_before)
            text = normalize(raw_text)
            if not text.strip() or text == '\ufffc':
                continue
            ordinal = counters.get(owner.review.id, 0)
            counters[owner.review.id] = ordinal + 1
            blocks.append({'id': f'{owner.review.id}:block:{ordinal}', 'kind': 'text',
                           'node_id': owner.review.id, 'parent_node_id': owner.review.parent_id,
                           'text': text, 'raw_latex': owner.review.raw_latex, 'atoms': atoms,
                           'selector': element.selector(), 'source': asdict(owner.review.source),
                           'section_path': list(owner.review.section_path), 'labels': list(owner.labels)})
            role = element.attrs.get('data-annotation-subunit')
            if element.tag == 'figcaption' or 'table-caption' in element.attrs.get('class', '').split():
                role = 'caption'
            if role:
                subunit = {'type': role}
                for attribute, key in (('data-row', 'row'), ('data-column', 'column'), ('data-table-index', 'table_index')):
                    if attribute in element.attrs:
                        subunit[key] = int(element.attrs[attribute])
                blocks[-1]['subunit'] = subunit
    # DOM order, not parser node ID, defines navigation order.
    positions = {}
    for section in descendants(parser.root):
        if 'preview-side' in section.attrs.get('class', '').split() and section.attrs.get('data-side') == 'new':
            for i, el in enumerate(descendants(section)):
                if node_owner(el):
                    positions[el.selector()] = i
    units = sorted(blocks + formulas, key=lambda u: positions[u['selector']])
    for order, unit in enumerate(units):
        unit['order'] = order
        unit['change_ids'] = [c.id for c in changes if c.new_node_id in (unit['node_id'], unit['parent_node_id'])]
    from ..annotation_sources import enrich_content
    enrich_content(project, units, root)
    # Keep legacy coordinates under their old version. Different parse/render
    # options must never claim the same version with different semantic units.
    version_id = digest({'entry': entry, 'content_model': 3, 'files': dependency_fingerprints(project.expanded),
                        'macros': {k: asdict(v) for k, v in project.macros.items()},
                        'units': _semantic_units(units)})
    return {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'project_root': str(root),
            'entry': entry, 'version_id': version_id, 'coordinate_system': 'utf16-normalized-whitespace-with-formula-atoms',
            'units': units}


def validate_content(content):
    exact_keys(content, ('schema_version', 'project_id', 'project_root', 'entry', 'version_id', 'coordinate_system', 'units'),
          ('schema_version', 'project_id', 'entry', 'version_id', 'coordinate_system', 'units'))
    if content['schema_version'] != SCHEMA_VERSION or content['coordinate_system'] != 'utf16-normalized-whitespace-with-formula-atoms':
        raise AnnotationError('正文契约版本无效')
    for field in ('project_id', 'version_id'):
        value = content[field]
        if not isinstance(value, str) or len(value) != 64 or any(x not in '0123456789abcdef' for x in value):
            raise AnnotationError(f'{field} 无效')
    _text(content['entry'], 'entry', 4096)
    if not isinstance(content['units'], list):
        raise AnnotationError('units 须为列表')
    ids, orders = set(), set()
    for unit in content['units']:
        exact_keys(unit, ('id', 'kind', 'node_id', 'parent_node_id', 'text', 'raw_latex', 'atoms', 'selector',
                     'source', 'section_path', 'labels', 'order', 'change_ids', 'subunit', 'source_span'),
              ('id', 'kind', 'node_id', 'text', 'raw_latex', 'atoms', 'selector', 'source', 'section_path', 'labels', 'order', 'change_ids'))
        _text(unit['id'], 'unit id', 1024)
        if unit['id'] in ids or unit['kind'] not in ('text', 'formula'):
            raise AnnotationError('正文单元 ID 重复或类型无效')
        ids.add(unit['id'])
        _text(unit['text'], '正文', 8 * 1024 * 1024)
        _text(unit['raw_latex'], 'LaTeX', 8 * 1024 * 1024)
        _integer(unit['order'], 'order')
        if unit['order'] in orders:
            raise AnnotationError('正文位置重复')
        orders.add(unit['order'])
        _text(unit['node_id'], 'node_id', 1024)
        _text(unit['selector'], 'selector', 4096)
        if unit.get('parent_node_id') is not None:
            _text(unit['parent_node_id'], 'parent_node_id', 1024)
        if not isinstance(unit['source'], dict) or not isinstance(unit['atoms'], list):
            raise AnnotationError('正文来源或公式原子无效')
        from ..contract import SourceLocation
        try:
            SourceLocation(**unit['source'])
        except (TypeError, ValueError) as exc:
            raise AnnotationError('正文源码位置无效') from exc
        if 'subunit' in unit:
            subunit = unit['subunit']
            exact_keys(subunit, ('type', 'row', 'column', 'table_index'), ('type',))
            if subunit['type'] not in ('caption', 'header', 'cell'):
                raise AnnotationError('子单元类型无效')
            for key in ('row', 'column', 'table_index'):
                if key in subunit:
                    _integer(subunit[key], key, 1)
        if 'source_span' in unit:
            span = unit['source_span']
            exact_keys(span, ('file', 'start', 'end', 'sha256', 'original_chunk', 'reliable', 'writable', 'reason'),
                  ('reliable', 'writable', 'reason'))
            if type(span['reliable']) is not bool or type(span['writable']) is not bool:
                raise AnnotationError('源码区间可靠性无效')
            if span['reliable']:
                exact_keys(span, ('file', 'start', 'end', 'sha256', 'original_chunk', 'reliable', 'writable', 'reason'),
                      ('file', 'start', 'end', 'sha256', 'original_chunk', 'reliable', 'writable', 'reason'))
                _integer(span['start'], 'source start')
                _integer(span['end'], 'source end', span['start'] + 1)
                if (span['file'] != unit['source'].get('file') or span['original_chunk'] != unit['raw_latex']
                        or len(span['original_chunk']) != span['end'] - span['start']
                        or not isinstance(span['sha256'], str) or re.fullmatch('[0-9a-f]{64}', span['sha256']) is None):
                    raise AnnotationError('源码区间与正文单元不一致')
            elif span['writable'] or not isinstance(span['reason'], str) or not span['reason']:
                raise AnnotationError('降级源码区间必须只读并提供理由')
        offsets = set()
        for atom in unit['atoms']:
            exact_keys(atom, ('offset', 'formula_node_id', 'raw_latex', 'selector'),
                  ('offset', 'formula_node_id', 'raw_latex', 'selector'))
            _integer(atom['offset'], '公式原子位置')
            if atom['offset'] in offsets or _slice(unit['text'], atom['offset'], atom['offset'] + 1) != '\ufffc':
                raise AnnotationError('公式原子坐标无效')
            offsets.add(atom['offset'])
            _text(atom['raw_latex'], '公式 LaTeX', 8 * 1024 * 1024)
            _text(atom['selector'], '公式 selector', 4096)
            if atom['formula_node_id'] is not None:
                _text(atom['formula_node_id'], '公式 node_id', 1024)
        if unit['kind'] == 'formula' and (unit['atoms'] or unit['text'] != unit['raw_latex']):
            raise AnnotationError('完整公式单元无效')
        if any(not isinstance(unit[k], list) or any(not isinstance(x, str) for x in unit[k])
               for k in ('section_path', 'labels', 'change_ids')):
            raise AnnotationError('正文上下文字段无效')
    return content


class PreparedContent(dict):
    """Validated server-owned manifest with an index outside its JSON representation."""
    def __init__(self, content):
        super().__init__(content)
        self.unit_index = {unit['id']: unit for unit in self['units']}


def load_content(report):
    root = Path(report).expanduser()
    root = root if root.is_dir() else root.parent
    return validate_content(json.loads((root / MANIFEST).read_text(encoding='utf-8')))


def make_anchor(content, selection):
    """Reject old-side, cross-block and partial-formula selections explicitly."""
    exact_keys(selection, ('unit_id', 'start', 'end', 'end_unit_id', 'side', 'quote'), ('unit_id',))
    if selection.get('side', 'new') != 'new':
        raise AnnotationError('只有修改后或当前正文可新建批注')
    if selection.get('end_unit_id', selection['unit_id']) != selection['unit_id']:
        raise AnnotationError('不支持跨段落选区')
    index = getattr(content, 'unit_index', None)
    unit = index.get(selection['unit_id']) if index is not None else next((u for u in content['units'] if u['id'] == selection['unit_id']), None)
    if unit is None:
        raise AnnotationError('正文单元不存在于该版本')
    if unit['kind'] == 'formula':
        if 'start' in selection or 'end' in selection:
            raise AnnotationError('公式必须选择完整公式节点')
        start, end, quote = None, None, unit['raw_latex']
        prefix = suffix = ''
    else:
        start, end = selection.get('start'), selection.get('end')
        quote = _slice(unit['text'], start, end)
        encoded = unit['text'].encode('utf-16-le')
        prefix = encoded[:start * 2].decode('utf-16-le')[-64:]
        suffix = encoded[end * 2:].decode('utf-16-le')[:64]
    if 'quote' in selection and selection['quote'] != quote:
        raise AnnotationConflict('选区原文与正文版本不一致，请保留草稿并重新定位')
    anchor = {'version_id': content['version_id'], 'unit_id': unit['id'], 'kind': unit['kind'],
            'start': start, 'end': end, 'quote': quote, 'prefix': prefix, 'suffix': suffix,
            'unit_text': unit['text'], 'raw_latex': unit['raw_latex'], 'node_id': unit['node_id'],
            'source': deepcopy(unit['source']), 'section_path': unit['section_path'][:],
            'labels': unit['labels'][:], 'order': unit['order'], 'selector': unit['selector'],
            'change_ids': unit['change_ids'][:],
            'atoms': [deepcopy(a) for a in unit['atoms'] if start is not None and start <= a['offset'] < end]}
    for key in ('subunit', 'source_span'):
        if key in unit:
            anchor[key] = deepcopy(unit[key])
    return anchor



def _formula_text(raw):
    # Environment/delimiter boilerplate cannot inflate confidence for short formulas.
    raw = re.sub(r"\\label\s*\{[^{}]*\}", "", raw).strip()
    if raw.startswith('\\begin{'):
        match = re.fullmatch(r"\\begin\{([^{}]+)\}(.*)\\end\{\1\}", raw, re.S)
        if match:
            raw = match.group(2)
    for opening, closing in (('$$', '$$'), ('$', '$'), ('\\[', '\\]'), ('\\(', '\\)')):
        if raw.startswith(opening) and raw.endswith(closing):
            raw = raw[len(opening):-len(closing)]
            break
    return re.sub(r"\s+", " ", raw).strip()


def _atom_similarity(left, right):
    if len(left['atoms']) != len(right['atoms']):
        return 0.
    return min((SequenceMatcher(None, _formula_text(a['raw_latex']), _formula_text(b['raw_latex']),
                                autojunk=False).ratio() for a, b in zip(left['atoms'], right['atoms'])), default=1.)

def locate(content, anchor):
    """Return all plausible candidates. Only unique, high-confidence results migrate.

    Exact repeated quotations stay ambiguous even if source positions look familiar.
    Fuzzy character alignment projects both selection endpoints; changed/deleted
    selection text is not silently replaced with an unrelated surviving substring.
    """
    candidates = []
    paragraph_matches = []
    paragraph_scores = {}
    for unit in content['units']:
        if unit['kind'] != anchor['kind']:
            continue
        if unit['kind'] == 'formula':
            score = SequenceMatcher(None, _formula_text(anchor['quote']), _formula_text(unit['raw_latex']), autojunk=False).ratio()
            if score >= .45:
                candidates.append({'anchor': make_anchor(content, {'unit_id': unit['id']}),
                                   'score': score, 'method': 'exact' if score == 1 else 'fuzzy'})
            continue
        quote, text = anchor['quote'], unit['text']
        cursor = 0
        exact = []
        while (found := text.find(quote, cursor)) >= 0:
            # Preserve deliberate partial-word selections, but do not match a formerly
            # complete word to a prefix/suffix of a different word.
            end_index = found + len(quote)
            if (quote[0].isalnum() and (not anchor['prefix'] or not anchor['prefix'][-1].isalnum())
                    and found > 0 and text[found - 1].isalnum()):
                cursor = found + 1
                continue
            if (quote[-1].isalnum() and (not anchor['suffix'] or not anchor['suffix'][0].isalnum())
                    and end_index < len(text) and text[end_index].isalnum()):
                cursor = found + 1
                continue
            start, end = utf16_length(text[:found]), utf16_length(text[:end_index])
            result = make_anchor(content, {'unit_id': unit['id'], 'start': start, 'end': end})
            context = (SequenceMatcher(None, anchor['prefix'], result['prefix'], autojunk=False).ratio() +
                       SequenceMatcher(None, anchor['suffix'], result['suffix'], autojunk=False).ratio()) / 2
            similarity = SequenceMatcher(None, anchor['unit_text'], text, autojunk=False).ratio()
            atom_score = _atom_similarity(anchor, result)
            exact.append({'anchor': result, 'score': round(min(max(similarity, context), atom_score), 6),
                          'context_score': context, 'method': 'exact' if atom_score == 1 else 'fuzzy'})
            cursor = found + 1
        if exact:
            candidates.extend(exact)
            continue
        matcher = SequenceMatcher(None, anchor['unit_text'], text, autojunk=False)
        # These are upper bounds, so pruning does not discard valid matches.
        if matcher.real_quick_ratio() < .45 or matcher.quick_ratio() < .45:
            paragraph_scores[unit['id']] = 0
            continue
        similarity = matcher.ratio()
        paragraph_scores[unit['id']] = similarity
        if similarity < .45:
            continue
        old_start = len(anchor['unit_text'].encode('utf-16-le')[:anchor['start'] * 2].decode('utf-16-le'))
        old_end = len(anchor['unit_text'].encode('utf-16-le')[:anchor['end'] * 2].decode('utf-16-le'))
        overlaps = [(tag, max(i, old_start), min(j, old_end), a, b, i, j)
                    for tag, i, j, a, b in matcher.get_opcodes() if i < old_end and j > old_start]
        if not overlaps:
            continue
        mapped_start = mapped_end = None
        for tag, left, right, a, b, i, j in overlaps:
            begin = a + left - i if tag == 'equal' else a
            finish = a + right - i if tag == 'equal' else b
            mapped_start = begin if mapped_start is None else min(mapped_start, begin)
            mapped_end = finish if mapped_end is None else max(mapped_end, finish)
        if (similarity >= .65 and unit['source'].get('file') == anchor['source'].get('file')
                and unit['section_path'] == anchor['section_path']
                and unit.get('subunit') == anchor.get('subunit')):
            paragraph_matches.append((similarity, unit, not text[mapped_start:mapped_end].strip()))
        if not text[mapped_start:mapped_end].strip():
            continue
        if quote[0].isalnum() and (not anchor['prefix'] or not anchor['prefix'][-1].isalnum()):
            while mapped_start > 0 and text[mapped_start - 1].isalnum():
                mapped_start -= 1
        if quote[-1].isalnum() and (not anchor['suffix'] or not anchor['suffix'][0].isalnum()):
            while mapped_end < len(text) and text[mapped_end].isalnum():
                mapped_end += 1
        new_quote = text[mapped_start:mapped_end]
        selection_score = SequenceMatcher(None, quote, new_quote, autojunk=False).ratio()
        score = min(similarity, selection_score)
        if score < .35:
            continue
        result = make_anchor(content, {'unit_id': unit['id'], 'start': utf16_length(text[:mapped_start]),
                                       'end': utf16_length(text[:mapped_end])})
        score = min(score, _atom_similarity(anchor, result))
        candidates.append({'anchor': result, 'score': round(score, 6), 'method': 'fuzzy'})
    # A paragraph can contain several nearly identical sentences. Full-paragraph
    # alignment supplies one projection; also retain competing word-span candidates.
    if anchor['kind'] == 'text' and len(anchor['quote']) >= 8:
        quote = anchor['quote']
        for unit in content['units']:
            if unit['kind'] != 'text':
                continue
            similarity = paragraph_scores.get(unit['id'])
            if similarity is None:
                matcher = SequenceMatcher(None, anchor['unit_text'], unit['text'], autojunk=False)
                if matcher.real_quick_ratio() < .45 or matcher.quick_ratio() < .45:
                    continue
                similarity = matcher.ratio()
            if similarity < .45:
                continue
            words = list(re.finditer(r'\S+', unit['text']))
            for i, word in enumerate(words):
                for j in range(i, len(words)):
                    last = words[j]
                    width = last.end() - word.start()
                    if width > len(quote) * 1.25 + 2:
                        break
                    if width < len(quote) * .75 - 2:
                        continue
                    start, end = utf16_length(unit['text'][:word.start()]), utf16_length(unit['text'][:last.end()])
                    if any(c['anchor']['unit_id'] == unit['id'] and c['anchor']['start'] < end
                           and start < c['anchor']['end'] for c in candidates):
                        continue
                    matcher = SequenceMatcher(None, quote, unit['text'][word.start():last.end()], autojunk=False)
                    if matcher.quick_ratio() < .85:
                        continue
                    score = matcher.ratio()
                    if score < .85:
                        continue
                    result = make_anchor(content, {'unit_id': unit['id'], 'start': start, 'end': end})
                    score = min(score, similarity, _atom_similarity(anchor, result))
                    candidates.append({'anchor': result, 'score': round(score, 6), 'method': 'fuzzy'})
    candidates.sort(key=lambda c: (-c['score'], c['anchor']['order'], c['anchor']['start'] or 0))
    exact = [c for c in candidates if c['method'] == 'exact']
    automatic = None
    if len(exact) == 1 and exact[0]['score'] >= .85 and not any(
            c is not exact[0] and c['score'] >= exact[0]['score'] - .10 for c in candidates):
        automatic = exact[0]
    elif not exact and candidates and candidates[0]['score'] >= .90 and (
            len(candidates) == 1 or candidates[0]['score'] - candidates[1]['score'] >= .10):
        automatic = candidates[0]
    result = {'status': ('exact' if automatic['method'] == 'exact' else 'auto_migrated') if automatic else 'needs_relocation',
            'anchor': automatic['anchor'] if automatic else None, 'candidates': candidates,
            'reason': None if automatic else 'ambiguous' if len(candidates) > 1 else 'low_confidence' if candidates else 'missing'}
    paragraph_matches.sort(key=lambda item: -item[0])
    if not automatic and paragraph_matches and (len(paragraph_matches) == 1 or
            paragraph_matches[0][0] - paragraph_matches[1][0] >= .10):
        _, unit, deleted = paragraph_matches[0]
        result['container'] = {'unit_id': unit['id'], 'kind': unit['kind'], 'selector': unit['selector'],
                               'order': unit['order'], 'source': deepcopy(unit['source']),
                               'section_path': unit['section_path'][:], 'selection_deleted': deleted}
        if 'subunit' in unit:
            result['container']['subunit'] = deepcopy(unit['subunit'])
        if deleted:
            result['reason'] = 'selection_deleted'
    # A labelled table retains its identity even when an entire cell disappears.
    if (not automatic and 'container' not in result and anchor.get('labels')
            and anchor.get('subunit', {}).get('type') in ('cell', 'header')):
        cells = [u for u in content['units'] if u.get('subunit', {}).get('type') in ('cell', 'header')
                 and u['source'].get('file') == anchor['source'].get('file')
                 and set(u['labels']) & set(anchor['labels'])
                 and u['subunit'].get('table_index') == anchor['subunit'].get('table_index')]
        if cells and len({u['node_id'] for u in cells}) == 1:
            same_cell = next((u for u in cells if u['subunit'] == anchor['subunit']), None)
            unit = same_cell or cells[0]
            result['container'] = {'unit_id': unit['id'], 'kind': unit['kind'], 'scope': 'table',
                                   'selector': unit['selector'], 'order': unit['order'],
                                   'source': deepcopy(unit['source']), 'section_path': unit['section_path'][:],
                                   'subunit': deepcopy(unit['subunit']), 'selection_deleted': same_cell is None}
            if same_cell is None:
                result['reason'] = 'selection_deleted'
    return result





def content_meta(content):
    """Bind the rendered page to its manifest without introducing browser behavior."""
    validate_content(content)
    return (f'<meta name="latex-review-annotation-project" content="{content["project_id"]}">'
            f'<meta name="latex-review-annotation-version" content="{content["version_id"]}">')
