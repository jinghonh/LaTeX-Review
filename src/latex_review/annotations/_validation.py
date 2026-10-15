"""Shared request validation helpers for the annotation package."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import tempfile

SCHEMA_VERSION = '1.0'
MANIFEST = 'annotation-content.json'
MAX_BODY = 64 * 1024


class AnnotationError(ValueError):
    pass


class AnnotationConflict(AnnotationError):
    """A stale revision or an import conflict; callers must retain their draft."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def thread_metadata(value):
    """Validate optional thread metadata without changing legacy threads."""
    result = {}
    if 'title' in value:
        title = value['title']
        if not isinstance(title, str) or len(title.encode('utf-8')) > 512:
            raise AnnotationError('title 须为不超过 512 字节的文字；空字符串恢复自动标题')
        result['title'] = title.strip()
    if 'tags' in value:
        tags = value['tags']
        if not isinstance(tags, list) or len(tags) > 32:
            raise AnnotationError('tags 须为最多 32 个标签的列表')
        result['tags'] = [_text(tag, 'tag', 128).strip() for tag in tags]
        if len(set(result['tags'])) != len(result['tags']):
            raise AnnotationError('tags 不得包含重复标签')
    return result


def digest(value):
    from ..identity import identity_hash
    return identity_hash(value)


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


def exact_keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise AnnotationError('对象字段无效或缺少必填字段')


def _author(value):
    exact_keys(value, ('name', 'type'), ('name', 'type'))
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


def atomic_write(path, data, *, compact=False):
    fd, temporary = tempfile.mkstemp(prefix='.annotations-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=None if compact else 2,
                      separators=(',', ':') if compact else None)
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

