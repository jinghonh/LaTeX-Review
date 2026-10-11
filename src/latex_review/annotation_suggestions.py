"""Explicit, version checked replacement of reliable original source intervals."""
from copy import deepcopy
import difflib
import hashlib
import os
from pathlib import Path
import re
import tempfile


def error(message):
    from .annotations import AnnotationError
    raise AnnotationError(message)


def validate(value):
    from .annotations import _keys, _integer
    _keys(value, ('original_chunk', 'replacement_chunk', 'status', 'version_id', 'unit_id',
                  'source_span', 'reason', 'applied_at', 'applied_sha256'), ('original_chunk', 'replacement_chunk'))
    if not isinstance(value, dict) or not isinstance(value.get('original_chunk'), str) or not value['original_chunk'] or not isinstance(value.get('replacement_chunk'), str):
        error('建议需要非空原始 LaTeX 块及替换 LaTeX 块')
    if len(value['original_chunk']) > 1000000 or len(value['replacement_chunk']) > 1000000:
        error('建议 LaTeX 块过长')
    if 'status' in value and value['status'] not in ('pending', 'applied'):
        error('建议应用状态无效')
    if value.get('reason') is not None and not isinstance(value['reason'], str):
        error('建议降级理由无效')
    span = value.get('source_span')
    if span is not None:
        _keys(span, ('file', 'start', 'end', 'sha256', 'original_chunk', 'reliable', 'writable', 'reason'))
        if (not isinstance(span['file'], str) or Path(span['file']).is_absolute()
                or '..' in Path(span['file']).parts or span['reliable'] is not True or span['writable'] is not True
                or not isinstance(span['sha256'], str) or re.fullmatch('[0-9a-f]{64}', span['sha256']) is None):
            error('建议源码区间无效')
        _integer(span['start'], 'source start')
        _integer(span['end'], 'source end', span['start'] + 1)
        if span['original_chunk'] != value['original_chunk'] or len(span['original_chunk']) != span['end'] - span['start']:
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


def execute(store, content, request):
    from .annotations import AnnotationConflict, _now, _keys, _integer
    _keys(request, ('operation', 'expected_revision', 'thread_id', 'message_id'),
          ('operation', 'expected_revision', 'thread_id', 'message_id'))
    _integer(request['expected_revision'], 'expected_revision')
    with store._locked() as data:
        if data['revision'] != request['expected_revision']:
            raise AnnotationConflict('批注版本已变化，请重新读取')
        thread = data['threads'].get(request['thread_id'])
        message = next((m for m in thread['messages'] if m['id'] == request['message_id'] and not m['deleted_at']), None) if thread and not thread['deleted_at'] else None
        if not message or not message.get('suggested_replacement'):
            error('替换建议不存在')
        suggestion = message['suggested_replacement']
        unit = next((u for u in content['units'] if u['id'] == suggestion.get('unit_id')), None)
        owner = unit.get('source_span', {}) if unit else {}
        span = suggestion.get('source_span') or {}
        if suggestion.get('version_id') != content['version_id'] or not owner.get('reliable') or not owner.get('writable') or span.get('file') != owner.get('file') or span.get('sha256') != owner.get('sha256') or not owner['start'] <= span.get('start', -1) < span.get('end', -1) <= owner['end']:
            error(suggestion.get('reason') or '建议源码区间与当前正文不一致，拒绝应用')
        path, original, updated, diff = inspect(store.root, suggestion)
        result = {'thread_id': thread['id'], 'message_id': message['id'], 'diff': diff, 'source_changed': False}
        if request['operation'] == 'suggestion_apply':
            replace(path, updated, expected=original)
            suggestion.update(status='applied', applied_at=_now(), applied_sha256=hashlib.sha256(updated).hexdigest())
            message['updated_at'] = thread['updated_at'] = _now()
            try:
                store._write(data)
            except Exception:
                # Restore the original source if recording the applied state fails.
                if path.read_bytes() == updated:
                    replace(path, original)
                raise
            result.update(source_changed=True, source_file=str(path))
        return {**store._view(data, content), 'result': result}
