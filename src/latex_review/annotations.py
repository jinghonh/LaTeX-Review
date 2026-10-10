"""Project-shared annotations, versioned preview coordinates and conservative relocation.

All public values are JSON objects. Text offsets are UTF-16 code units, as in DOM
Range. Formula atoms count as one U+FFFC; MathJax descendants never supply offsets.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
import re
from pathlib import Path
import tempfile
import uuid

SCHEMA_VERSION = '1.0'
MANIFEST = 'annotation-content.json'
MAX_BODY = 64 * 1024


class AnnotationError(ValueError):
    pass


class AnnotationConflict(AnnotationError):
    """A stale revision or an import conflict; callers must retain their draft."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name, limit=MAX_BODY):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
        raise AnnotationError(f'{name} 必须是非空文字，且不得超过 {limit} 字节')
    return value


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise AnnotationError(f'{name} 必须是至少 {minimum} 的整数')
    return value


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise AnnotationError('对象字段无效或缺少必填字段')


def _author(value):
    _keys(value, ('name', 'type'), ('name', 'type'))
    _text(value['name'], '作者名称', 256)
    if value['type'] not in ('user', 'agent'):
        raise AnnotationError('作者类型须为 user 或 agent')
    return deepcopy(value)


def _category(value):
    if value not in ('question', 'suggestion', 'note'):
        raise AnnotationError('内容分类须为 question、suggestion 或 note')
    return value


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


def _slice(text, start, end):
    _integer(start, 'start')
    _integer(end, 'end')
    if not start < end <= utf16_length(text):
        raise AnnotationError('选区须为段落内非空区间')
    try:
        return text.encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le')
    except UnicodeError as exc:
        raise AnnotationError('选区边界不能切开 Unicode 字符') from exc


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


def build_content(project, preview_html: str, *, project_root: Path | None = None, changes=()):
    """Extract actual preview text, including unmodified prose and degraded formulas.

    Call while the source snapshot exists. Content identity includes resolved source
    text and bibliography/macro interpretation, not the report or comparison baseline.
    """
    from dataclasses import asdict
    from .cache import dependency_fingerprints
    root = (project_root or project.expanded.source.worktree_origin or project.expanded.source.root).resolve()
    entry = project.expanded.source.entry
    project_id = _hash({'root': str(root), 'entry': entry})
    version_id = _hash({'entry': entry, 'files': dependency_fingerprints(project.expanded),
                        'macros': {k: asdict(v) for k, v in project.macros.items()}})
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

    def contents(element, atoms):
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
            if any(x.tag in block_tags or x.attrs.get('data-node-id') in nodes for x in descendants(element)
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
    return {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'project_root': str(root),
            'entry': entry, 'version_id': version_id, 'coordinate_system': 'utf16-normalized-whitespace-with-formula-atoms',
            'units': units}


def validate_content(content):
    _keys(content, ('schema_version', 'project_id', 'project_root', 'entry', 'version_id', 'coordinate_system', 'units'),
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
        _keys(unit, ('id', 'kind', 'node_id', 'parent_node_id', 'text', 'raw_latex', 'atoms', 'selector',
                     'source', 'section_path', 'labels', 'order', 'change_ids'),
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
        from .contract import SourceLocation
        try:
            SourceLocation(**unit['source'])
        except (TypeError, ValueError) as exc:
            raise AnnotationError('正文源码位置无效') from exc
        offsets = set()
        for atom in unit['atoms']:
            _keys(atom, ('offset', 'formula_node_id', 'raw_latex', 'selector'),
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


def load_content(report):
    root = Path(report).expanduser()
    root = root if root.is_dir() else root.parent
    return validate_content(json.loads((root / MANIFEST).read_text(encoding='utf-8')))


def make_anchor(content, selection):
    """Reject old-side, cross-block and partial-formula selections explicitly."""
    _keys(selection, ('unit_id', 'start', 'end', 'end_unit_id', 'side', 'quote'), ('unit_id',))
    if selection.get('side', 'new') != 'new':
        raise AnnotationError('只有修改后或当前正文可新建批注')
    if selection.get('end_unit_id', selection['unit_id']) != selection['unit_id']:
        raise AnnotationError('不支持跨段落选区')
    unit = next((u for u in content['units'] if u['id'] == selection['unit_id']), None)
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
    return {'version_id': content['version_id'], 'unit_id': unit['id'], 'kind': unit['kind'],
            'start': start, 'end': end, 'quote': quote, 'prefix': prefix, 'suffix': suffix,
            'unit_text': unit['text'], 'raw_latex': unit['raw_latex'], 'node_id': unit['node_id'],
            'source': deepcopy(unit['source']), 'section_path': unit['section_path'][:],
            'labels': unit['labels'][:], 'order': unit['order'], 'selector': unit['selector'],
            'change_ids': unit['change_ids'][:],
            'atoms': [deepcopy(a) for a in unit['atoms'] if start is not None and start <= a['offset'] < end]}



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
        similarity = matcher.ratio()
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
        if mapped_start == mapped_end:
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
            similarity = SequenceMatcher(None, anchor['unit_text'], unit['text'], autojunk=False).ratio()
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
                    score = SequenceMatcher(None, quote, unit['text'][word.start():last.end()], autojunk=False).ratio()
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
    return {'status': ('exact' if automatic['method'] == 'exact' else 'auto_migrated') if automatic else 'needs_relocation',
            'anchor': automatic['anchor'] if automatic else None, 'candidates': candidates,
            'reason': None if automatic else 'ambiguous' if len(candidates) > 1 else 'low_confidence' if candidates else 'missing'}


def _atomic_write(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.annotations-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AnnotationStore:
    """One disk file per project entry, with interprocess locks and revision checks.

    Report manifests contain only identity/location metadata. HTTP callers cannot
    choose paths or submit a new content manifest. CLI users explicitly choose roots.
    """
    def __init__(self, project_root, project_id):
        self.root = Path(project_root).expanduser().resolve()
        if not isinstance(project_id, str) or len(project_id) != 64 or any(c not in '0123456789abcdef' for c in project_id):
            raise AnnotationError('项目 ID 无效')
        self.project_id = project_id
        self.directory = self.root / '.latex-review' / 'annotations'
        self.path = self.directory / f'{project_id}.json'
        self.lock_path = self.directory / f'{project_id}.lock'

    @classmethod
    def for_content(cls, content, project_root=None):
        root = Path(project_root or content.get('project_root', '')).expanduser().resolve()
        if not root.is_dir():
            raise AnnotationError('本机论文项目不存在；请使用报告携带的浏览器批注存储')
        if _hash({'root': str(root), 'entry': content['entry']}) != content['project_id']:
            raise AnnotationError('正文项目身份与本机项目目录不一致')
        return cls(root, content['project_id'])

    @contextmanager
    def _locked(self):
        if any(p.is_symlink() for p in (self.directory, self.directory.parent, self.path, self.lock_path)):
            raise AnnotationError('批注存储路径不可为符号链接')
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                _keys(data, ('schema_version', 'project_id', 'revision', 'threads', 'versions'),
                      ('schema_version', 'project_id', 'revision', 'threads', 'versions'))
                _integer(data['revision'], 'revision')
                if not isinstance(data['threads'], dict) or not isinstance(data['versions'], dict):
                    raise AnnotationError('批注存储线程或版本无效')
                if data.get('schema_version') != SCHEMA_VERSION or data.get('project_id') != self.project_id:
                    raise AnnotationError('批注存储契约或项目身份无效')
            else:
                data = {'schema_version': SCHEMA_VERSION, 'project_id': self.project_id, 'revision': 0,
                        'threads': {}, 'versions': {}}
            yield data
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, data):
        data['revision'] += 1
        _atomic_write(self.path, data)

    def _content(self, data, content):
        validate_content(content)
        if content['project_id'] != self.project_id:
            raise AnnotationError('不能访问其他项目的批注')
        existing = data['versions'].get(content['version_id'])
        if existing:
            def semantic(value):
                return [{k: ([{a: b for a, b in atom.items() if a != 'selector'} for atom in v] if k == 'atoms' else v)
                         for k, v in unit.items() if k not in ('selector', 'change_ids')} for unit in value['units']]
            if semantic(existing) != semantic(content):
                raise AnnotationConflict('同版本正文坐标不一致，请保留草稿并重新生成报告')
        # Render metadata may vary with the comparison baseline; version identity does not.
        data['versions'][content['version_id']] = deepcopy(content)
        return existing

    def read(self, content):
        with self._locked() as data:
            self._content(data, content)
            return self._view(data, content)

    def _view(self, data, content):
        threads = []
        for value in data['threads'].values():
            if value.get('deleted_at'):
                continue
            thread = deepcopy(value)
            association = thread['associations'].get(content['version_id'])
            if association is None:
                association = locate(content, thread['current_anchor'])
                association['persisted'] = False
            association = deepcopy(association)
            if association['anchor']:
                old = association['anchor']
                selection = {'unit_id': old['unit_id']}
                if old['kind'] == 'text':
                    selection.update(start=old['start'], end=old['end'], quote=old['quote'])
                association['anchor'] = make_anchor(content, selection)
            for candidate in association['candidates']:
                old = candidate['anchor']
                selection = {'unit_id': old['unit_id']}
                if old['kind'] == 'text':
                    selection.update(start=old['start'], end=old['end'], quote=old['quote'])
                candidate['anchor'] = make_anchor(content, selection)
            thread['association'] = association
            threads.append(thread)
        threads.sort(key=lambda t: ((t['association'].get('anchor') or {}).get('order', 10**12),
                                    (t['association'].get('anchor') or {}).get('start') or 0, t['created_at'], t['id']))
        return {'schema_version': SCHEMA_VERSION, 'project_id': self.project_id, 'version_id': content['version_id'],
                'revision': data['revision'], 'threads': threads}

    def dispatch(self, content, request):
        """Shared structured mutation API used unchanged by CLI and local HTTP."""
        _keys(request, ('operation', 'expected_revision', 'thread_id', 'message_id', 'selection', 'body',
                        'author', 'category', 'bundle'), ('operation', 'expected_revision'))
        revision = _integer(request['expected_revision'], 'expected_revision')
        operation = request['operation']
        fields = {
            'create': {'selection', 'body', 'author', 'category'},
            'reply': {'thread_id', 'body', 'author', 'category'},
            'edit': {'thread_id', 'message_id', 'body', 'category'},
            'delete_message': {'thread_id', 'message_id'}, 'delete_thread': {'thread_id'},
            'resolve': {'thread_id'}, 'reopen': {'thread_id'},
            'associate': {'thread_id', 'selection'}, 'reconcile': set(), 'import': {'bundle'},
        }
        if not isinstance(operation, str) or operation not in fields:
            raise AnnotationError('未知批注操作')
        _keys(request, fields[operation] | {'operation', 'expected_revision'},
              fields[operation] | {'operation', 'expected_revision'})
        with self._locked() as data:
            if data['revision'] != revision:
                raise AnnotationConflict('批注已被其他审阅者更新，请保留草稿并重新读取')
            self._content(data, content)
            now = _now()
            result = None
            if operation == 'create':
                anchor = make_anchor(content, request['selection'])
                message = self._message(request, now)
                thread_id = str(uuid.uuid4())
                association = {'status': 'exact', 'anchor': anchor, 'candidates': [], 'reason': None}
                data['threads'][thread_id] = {'id': thread_id, 'project_id': self.project_id,
                    'created_version_id': content['version_id'], 'created_at': now, 'updated_at': now,
                    'resolved': False, 'deleted_at': None, 'messages': [message], 'original_anchor': deepcopy(anchor),
                    'current_anchor': deepcopy(anchor), 'associations': {content['version_id']: association},
                    'history': [{'at': now, 'method': 'created', 'anchor': deepcopy(anchor)}]}
                result = {'thread_id': thread_id, 'message_id': message['id']}
            elif operation == 'reconcile':
                for thread in data['threads'].values():
                    if thread['deleted_at'] or content['version_id'] in thread['associations']:
                        continue
                    association = locate(content, thread['current_anchor'])
                    thread['associations'][content['version_id']] = association
                    if association['anchor']:
                        self._associate(thread, association['anchor'], association['status'], now)
            elif operation == 'import':
                result = self._import(data, request['bundle'])
            else:
                thread_id = request['thread_id']
                _text(thread_id, 'thread_id', 256)
                thread = data['threads'].get(thread_id)
                if thread is None or thread['deleted_at']:
                    raise AnnotationError('批注线程不存在或已删除')
                if operation == 'reply':
                    message = self._message(request, now)
                    thread['messages'].append(message)
                    result = {'message_id': message['id']}
                elif operation in ('edit', 'delete_message'):
                    message = next((m for m in thread['messages'] if m['id'] == request['message_id']), None)
                    if message is None or message['deleted_at']:
                        raise AnnotationError('批注内容不存在或已删除')
                    if operation == 'edit':
                        body = _text(request['body'], '批注正文')
                        category = _category(request['category'])
                        message['edits'].append({'body': message['body'], 'category': message['category'], 'at': now})
                        message.update(body=body, category=category, updated_at=now)
                    else:
                        message['deleted_at'] = now
                elif operation == 'delete_thread':
                    thread['deleted_at'] = now
                elif operation in ('resolve', 'reopen'):
                    thread['resolved'] = operation == 'resolve'
                elif operation == 'associate':
                    anchor = make_anchor(content, request['selection'])
                    thread['associations'][content['version_id']] = {'status': 'manual', 'anchor': anchor,
                                                                      'candidates': [], 'reason': None}
                    self._associate(thread, anchor, 'manual', now)
                thread['updated_at'] = now
            self._write(data)
            return {**self._view(data, content), 'result': result}

    @staticmethod
    def _message(request, now):
        return {'id': str(uuid.uuid4()), 'body': _text(request['body'], '批注正文'),
                'author': _author(request['author']), 'category': _category(request['category']),
                'created_at': now, 'updated_at': now, 'deleted_at': None, 'edits': []}

    @staticmethod
    def _associate(thread, anchor, method, now):
        previous = thread['current_anchor']
        # Movement/version-only changes update the association but do not add content history.
        def snapshot(value):
            return (value['quote'], value['unit_text'], value['raw_latex'],
                    [(a['offset'], a['raw_latex']) for a in value['atoms']])
        if snapshot(previous) != snapshot(anchor):
            thread['history'].append({'at': now, 'method': method, 'anchor': deepcopy(anchor)})
        thread['current_anchor'] = deepcopy(anchor)
        thread['updated_at'] = now

    def export(self):
        with self._locked() as data:
            return deepcopy(data)

    def _import(self, data, bundle):
        """Atomic deduplication. Divergent same-ID records return conflicts, never overwrite."""
        _keys(bundle, ('schema_version', 'project_id', 'revision', 'threads', 'versions'),
              ('schema_version', 'project_id', 'revision', 'threads', 'versions'))
        if bundle['schema_version'] != SCHEMA_VERSION or bundle['project_id'] != self.project_id:
            raise AnnotationError('导入契约版本或项目身份不一致')
        if not isinstance(bundle['threads'], dict) or not isinstance(bundle['versions'], dict):
            raise AnnotationError('导入线程及版本须为对象')
        for version_id, content in bundle['versions'].items():
            validate_content(content)
            if content['project_id'] != self.project_id or version_id != content['version_id']:
                raise AnnotationError('导入版本身份不一致')
        for version_id, content in bundle['versions'].items():
            existing = data['versions'].get(version_id)
            if existing:
                self._content(data, content)
        for thread_id, thread in bundle['threads'].items():
            self._validate_thread(thread_id, thread, bundle['versions'])
        conflicts = [key for key, value in bundle['threads'].items()
                     if key in data['threads'] and data['threads'][key] != value]
        if conflicts:
            raise AnnotationConflict('导入存在同 ID 内容冲突：' + ', '.join(conflicts))
        imported = [key for key in bundle['threads'] if key not in data['threads']]
        data['threads'].update(deepcopy(bundle['threads']))
        data['versions'].update(deepcopy(bundle['versions']))
        return {'imported': imported, 'duplicates': len(bundle['threads']) - len(imported)}

    def _validate_thread(self, thread_id, thread, versions):
        _keys(thread, ('id', 'project_id', 'created_version_id', 'created_at', 'updated_at', 'resolved', 'deleted_at',
                       'messages', 'original_anchor', 'current_anchor', 'associations', 'history'),
              ('id', 'project_id', 'created_version_id', 'created_at', 'updated_at', 'resolved', 'deleted_at',
               'messages', 'original_anchor', 'current_anchor', 'associations', 'history'))
        if thread['id'] != thread_id or thread['project_id'] != self.project_id or type(thread['resolved']) is not bool:
            raise AnnotationError('导入线程身份或状态无效')
        for name in ('created_at', 'updated_at'):
            _text(thread[name], name, 256)
        if thread['deleted_at'] is not None:
            _text(thread['deleted_at'], 'deleted_at', 256)
        if not isinstance(thread['messages'], list) or not thread['messages'] or not isinstance(thread['history'], list):
            raise AnnotationError('导入线程内容或历史无效')
        message_ids = set()
        for message in thread['messages']:
            _keys(message, ('id', 'body', 'author', 'category', 'created_at', 'updated_at', 'deleted_at', 'edits'),
                  ('id', 'body', 'author', 'category', 'created_at', 'updated_at', 'deleted_at', 'edits'))
            _text(message['id'], 'message id', 256)
            if message['id'] in message_ids:
                raise AnnotationError('导入回复 ID 重复')
            message_ids.add(message['id'])
            _text(message['body'], '批注正文')
            _author(message['author'])
            _category(message['category'])
            for name in ('created_at', 'updated_at'):
                _text(message[name], name, 256)
            if message['deleted_at'] is not None:
                _text(message['deleted_at'], 'deleted_at', 256)
            if not isinstance(message['edits'], list):
                raise AnnotationError('导入编辑历史无效')
            for edit in message['edits']:
                _keys(edit, ('body', 'category', 'at'), ('body', 'category', 'at'))
                _text(edit['body'], '历史正文')
                _category(edit['category'])
                _text(edit['at'], 'at', 256)
        def check_anchor(anchor):
            if not isinstance(anchor, dict) or anchor.get('version_id') not in versions:
                raise AnnotationError('导入关联缺少正文版本')
            selection = {'unit_id': anchor.get('unit_id')}
            if anchor.get('kind') == 'text':
                selection.update(start=anchor.get('start'), end=anchor.get('end'))
            rebuilt = make_anchor(versions[anchor['version_id']], selection)
            navigation = {'change_ids', 'selector', 'order'}
            def semantic(value):
                return {k: ([{a: b for a, b in atom.items() if a != 'selector'} for atom in v] if k == 'atoms' else v)
                        for k, v in value.items() if k not in navigation}
            if semantic(rebuilt) != semantic(anchor):
                raise AnnotationError('导入锚点与正文不一致')
        check_anchor(thread['original_anchor'])
        check_anchor(thread['current_anchor'])
        if thread['created_version_id'] != thread['original_anchor']['version_id']:
            raise AnnotationError('导入创建版本不一致')
        if not isinstance(thread['associations'], dict):
            raise AnnotationError('导入关联须为对象')
        for version_id, association in thread['associations'].items():
            _keys(association, ('status', 'anchor', 'candidates', 'reason'), ('status', 'anchor', 'candidates', 'reason'))
            if version_id not in versions or association['status'] not in ('exact', 'auto_migrated', 'manual', 'needs_relocation'):
                raise AnnotationError('导入关联状态或版本无效')
            if association['anchor']:
                check_anchor(association['anchor'])
                if association['anchor']['version_id'] != version_id:
                    raise AnnotationError('导入关联版本不一致')
            if not isinstance(association['candidates'], list):
                raise AnnotationError('导入候选须为列表')
            for candidate in association['candidates']:
                _keys(candidate, ('anchor', 'score', 'method', 'context_score'), ('anchor', 'score', 'method'))
                check_anchor(candidate['anchor'])
                if (candidate['anchor']['version_id'] != version_id or type(candidate['score']) not in (int, float)
                        or not 0 <= candidate['score'] <= 1 or candidate['method'] not in ('exact', 'fuzzy')):
                    raise AnnotationError('导入候选无效')
        for event in thread['history']:
            _keys(event, ('at', 'method', 'anchor'), ('at', 'method', 'anchor'))
            _text(event['at'], 'at', 256)
            if event['method'] not in ('created', 'manual', 'exact', 'auto_migrated'):
                raise AnnotationError('导入历史类型无效')
            check_anchor(event['anchor'])


def execute_request(store, content, envelope):
    _keys(envelope, ('project_id', 'version_id', 'request'), ('project_id', 'version_id', 'request'))
    if envelope['project_id'] != content['project_id'] or envelope['version_id'] != content['version_id']:
        raise AnnotationConflict('正文项目或版本已变化，请保留草稿并重新读取正文')
    request = envelope['request']
    if isinstance(request, dict) and request.get('operation') == 'locate':
        _keys(request, ('operation', 'selection'), ('operation', 'selection'))
        return {'anchor': make_anchor(content, request['selection'])}
    return store.dispatch(content, request)


def query_threads(snapshot, *, author_type=None, category=None, resolved=None,
                  location_status=None, search='', sort='position'):
    """Filter/sort the version-specific view; a UI can retain its own cursor."""
    if author_type is not None and author_type not in ('user', 'agent'):
        raise AnnotationError('作者筛选无效')
    if category is not None:
        _category(category)
    if resolved is not None and type(resolved) is not bool:
        raise AnnotationError('解决状态筛选无效')
    if location_status is not None and location_status not in ('exact', 'auto_migrated', 'manual', 'needs_relocation'):
        raise AnnotationError('定位状态筛选无效')
    if sort not in ('position', 'created_at', 'updated_at') or not isinstance(search, str):
        raise AnnotationError('排序或搜索无效')
    result = []
    for thread in snapshot['threads']:
        messages = [m for m in thread['messages'] if not m['deleted_at']]
        if author_type and not any(m['author']['type'] == author_type for m in messages):
            continue
        if category and not any(m['category'] == category for m in messages):
            continue
        if resolved is not None and thread['resolved'] != resolved:
            continue
        if location_status and thread['association']['status'] != location_status:
            continue
        haystack = '\n'.join([thread['original_anchor']['quote'],
                              (thread['association'].get('anchor') or {}).get('quote', ''),
                              *(m['body'] + '\n' + m['author']['name'] for m in messages)])
        if search.casefold() not in haystack.casefold():
            continue
        result.append(thread)
    if sort != 'position':
        result.sort(key=lambda t: (t[sort], t['id']))
    return result


def overlapping_threads(snapshot, unit_id, start=None, end=None):
    """Return every overlapping thread, including resolved threads."""
    result = []
    for thread in snapshot['threads']:
        anchor = thread['association'].get('anchor')
        if not anchor or anchor['unit_id'] != unit_id:
            continue
        if anchor['kind'] == 'formula' or (start is not None and end is not None and
                anchor['start'] < end and start < anchor['end']):
            result.append(thread)
    return result


def content_meta(content):
    """Bind the rendered page to its manifest without introducing browser behavior."""
    validate_content(content)
    return (f'<meta name="latex-review-annotation-project" content="{content["project_id"]}">'
            f'<meta name="latex-review-annotation-version" content="{content["version_id"]}">')
