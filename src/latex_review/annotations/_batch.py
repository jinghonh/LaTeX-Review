"""Batch request validation; AnnotationStore owns execution and persistence."""
from ._validation import AnnotationError, _hash, _integer, _keys, _text

OPERATIONS = {'create', 'reply', 'edit', 'delete_message', 'delete_thread', 'resolve', 'reopen', 'associate', 'update_metadata'}


def validate_request(content, request):
    _keys(request, ('operation', 'expected_revision', 'request_id', 'atomic', 'dry_run', 'items', 'preflight_token'),
          ('operation', 'expected_revision', 'request_id', 'items'))
    if request['operation'] != 'batch':
        raise AnnotationError('批量操作须为 batch')
    revision = _integer(request['expected_revision'], 'expected_revision')
    request_id = _text(request['request_id'], 'request_id', 256)
    atomic, dry_run = request.get('atomic', False), request.get('dry_run', False)
    if type(atomic) is not bool or type(dry_run) is not bool:
        raise AnnotationError('atomic 和 dry_run 须为布尔值')
    items = request['items']
    if not isinstance(items, list) or not items:
        raise AnnotationError('items 须为非空列表')
    if len(items) > 100:
        raise AnnotationError('批量操作最多 100 条；请拆分请求')
    import json
    if len(json.dumps(request, ensure_ascii=False).encode()) > 1024 * 1024:
        raise AnnotationError('批量请求最多 1 MiB；请拆分请求')
    ids = set()
    for item in items:
        if not isinstance(item, dict):
            raise AnnotationError('批量项须为对象')
        item_id = _text(item.get('id'), 'item id', 256)
        if item_id in ids:
            raise AnnotationError('批量项 ID 重复')
        ids.add(item_id)
    # Revision is a concurrency precondition, not part of the requested mutation.
    fingerprint = _hash({'project_id': content['project_id'], 'version_id': content['version_id'],
                         'items': items, 'atomic': atomic})
    return revision, request_id, atomic, dry_run, items, fingerprint
