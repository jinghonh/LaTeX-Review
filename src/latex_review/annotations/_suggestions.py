"""Explicit, version checked replacement of reliable original source intervals."""
from copy import deepcopy
import difflib
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile

from jsonschema import Draft7Validator

SCHEMA = json.loads(files('latex_review').joinpath('schemas/annotation-suggestion.schema.json').read_text(encoding='utf-8'))
_validator = Draft7Validator(SCHEMA)


def delivery_script():
    """Inline the storage codec and shared suggestion contract for portable reports."""
    script = files('latex_review').joinpath('annotation_delivery.js').read_text(encoding='utf-8')
    codec = files('latex_review').joinpath('annotation_codec.js').read_text(encoding='utf-8')
    return script.replace('__ANNOTATION_CODEC__', codec).replace('__SUGGESTION_SCHEMA__', json.dumps(SCHEMA).replace('<', '\\u003c'))


def error(message):
    from ._validation import AnnotationError
    raise AnnotationError(message)


def validate(value):
    from ._validation import _integer
    if not _validator.is_valid(value):
        error('建议字段、应用状态或源码区间无效')
    span = value.get('source_span')
    if span is not None:
        _integer(span['start'], 'source start')
        _integer(span['end'], 'source end', span['start'] + 1)
        if span['end'] <= span['start'] or span['original_chunk'] != value['original_chunk'] or len(span['original_chunk']) != span['end'] - span['start']:
            error('建议源码区间与原文不一致')
    return value


def prepare(content, anchor, value):
    validate(value)
    suggestion = {'original_chunk': value['original_chunk'], 'replacement_chunk': value['replacement_chunk'],
                  'status': 'pending', 'version_id': content['version_id'], 'unit_id': anchor['unit_id']}
    unit = next((u for u in content['units'] if u['id'] == anchor['unit_id']), None)
    span = unit.get('source_span', {}) if unit else {}
    if not span.get('reliable') or not span.get('writable'):
        suggestion.update(source_span=None, reason=span.get('reason') or '源码位置不可靠，建议仅供参考')
        return suggestion
    chunk = span['original_chunk']
    original = value['original_chunk']
    if chunk.count(original) != 1:
        error('建议原文必须在所属源码单元中唯一出现')
    offset = chunk.index(original)
    suggestion.update(source_span={**deepcopy(span), 'start': span['start'] + offset,
                                  'end': span['start'] + offset + len(original), 'original_chunk': original}, reason=None)
    return suggestion


def inspect(root, suggestion):
    validate(suggestion)
    if suggestion.get('status') == 'applied':
        error('建议已应用，禁止重复应用')
    span = suggestion.get('source_span')
    if not span or not span.get('reliable') or not span.get('writable'):
        error(suggestion.get('reason') or '源码位置不可靠，无法应用建议')
    root = Path(root).resolve()
    path = root / span['file']
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        error('源码路径不可越过项目目录或使用符号链接')
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    if hashlib.sha256(raw).hexdigest() != span['sha256']:
        error('源码版本已变化，建议已过期')
    start, end = span['start'], span['end']
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text) or text[start:end] != suggestion['original_chunk']:
        error('源码原文已变化，拒绝应用建议')
    replacement = suggestion['replacement_chunk']
    # Match the existing file's line endings without universal-newline translation.
    if b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b''):
        replacement = replacement.replace('\r\n', '\n').replace('\n', '\r\n')
    updated = text[:start] + replacement + text[end:]
    rows = difflib.unified_diff(text.splitlines(keepends=True), updated.splitlines(keepends=True),
                                      fromfile=span['file'], tofile=span['file'])
    diff = ''.join(row if row.endswith('\n') else row + '\n\\ No newline at end of file\n' for row in rows)
    return path, raw, updated.encode('utf-8'), diff


def replace(path, raw, *, expected=None):
    mode = path.stat().st_mode & 0o7777
    fd, temporary = tempfile.mkstemp(prefix='.latex-review-source-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if expected is not None and path.read_bytes() != expected:
            error('写入前源码已变化，拒绝覆盖新内容')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
