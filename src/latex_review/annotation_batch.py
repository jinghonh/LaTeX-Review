"""Locked batch mutations with persistent request idempotency."""
from copy import deepcopy

from .annotations import AnnotationConflict, AnnotationError, _atomic_write, _hash, _integer, _keys, _text

OPERATIONS = {'create', 'reply', 'edit', 'delete_message', 'delete_thread', 'resolve', 'reopen', 'associate'}


def execute_batch(store, content, request, progress=None):
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
    from . import annotation_preflight
    def report(phase, completed=0):
        if progress:
            progress(phase, completed, len(items))
    report('lock')
    with store._locked() as data:
        previous = data.get('requests', {}).get(request_id)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise AnnotationConflict('请求 ID 已用于不同载荷')
            if not dry_run:
                return deepcopy(previous['response'])
        plan = None if dry_run else annotation_preflight.load(store, content, request, fingerprint, data['revision'])
        if revision != data['revision']:
            raise AnnotationConflict('批注已被其他审阅者更新，请保留草稿并重新读取')
        report('validate')
        # Copy only containers and affected threads, never all stored versions per item.
        working = {**data, 'threads': dict(data['threads']), 'versions': dict(data['versions']),
                   'requests': dict(data.get('requests', {}))}
        store._content(working, content)
        results = deepcopy(plan['results']) if plan else []
        changed = {}
        if plan:
            working['threads'].update(deepcopy(plan['threads']))
            report('reuse', len(items))
        for index, item in enumerate([] if plan else items):
            candidate = {**working, 'threads': dict(working['threads'])}
            target = item.get('thread_id')
            if isinstance(target, str) and target in candidate['threads']:
                candidate['threads'][target] = deepcopy(candidate['threads'][target])
            try:
                if 'error' in item:
                    _keys(item, ('id', 'operation', 'error'), ('id', 'operation', 'error'))
                    if not isinstance(item['operation'], str) or item['operation'] not in OPERATIONS:
                        raise AnnotationError('未知批量批注操作')
                    _keys(item['error'], ('code', 'message', 'candidates'), ('code', 'message'))
                    _text(item['error']['code'], 'error code', 256)
                    _text(item['error']['message'], 'error message')
                    if 'candidates' in item['error'] and not isinstance(item['error']['candidates'], list):
                        raise AnnotationError('error candidates 须为列表')
                    results.append({'id': item['id'], 'operation': item['operation'], 'ok': False,
                                    'error': deepcopy(item['error'])})
                    continue
                mutation = {key: value for key, value in item.items() if key != 'id'}
                if not isinstance(mutation.get('operation'), str) or mutation['operation'] not in OPERATIONS:
                    raise AnnotationError('未知批量批注操作')
                mutation['expected_revision'] = revision
                store._validate_request(mutation)
                result = store._apply(candidate, content, mutation) or {}
                if item['operation'] == 'edit':
                    result['message_id'] = item['message_id']
                working = candidate
                thread_id = result.get('thread_id', item.get('thread_id'))
                if thread_id:
                    changed[thread_id] = working['threads'][thread_id]
                results.append({'id': item['id'], 'operation': item['operation'], 'ok': True,
                                'thread_id': result.get('thread_id', item.get('thread_id')),
                                **result})
            except AnnotationError as exc:
                results.append({'id': item['id'], 'operation': item.get('operation'), 'ok': False,
                                'error': {'code': 'conflict' if isinstance(exc, AnnotationConflict) else 'validation',
                                          'message': str(exc)}})
            finally:
                report('preflight' if dry_run else 'apply', index + 1)
        failed = any(not result['ok'] for result in results)
        committed = not dry_run and any(result['ok'] for result in results) and not (atomic and failed)
        if atomic and failed:
            for result in results:
                if result['ok']:
                    result.update(ok=False, error={'code': 'atomic_rollback', 'message': '整批校验失败，未提交'})
                    result.pop('thread_id', None)
                    result.pop('message_id', None)
            working = {**data, 'requests': dict(data.get('requests', {}))}
            changed = {}
        if dry_run:
            plan = annotation_preflight.save(store, content, request, fingerprint, revision, changed, results)
            results = deepcopy(results)
            for result in results:
                result.pop('thread_id', None) if result['operation'] == 'create' else None
                result.pop('message_id', None)
        response = {'request_id': request_id, 'atomic': atomic, 'dry_run': dry_run, 'committed': committed,
                    'version_id': content['version_id'], 'revision': data['revision'] + int(committed),
                    'start_revision': data['revision'], 'start_thread_ids': [t['id'] for t in data['threads'].values() if not t.get('deleted_at')],
                    'results': results, 'preflight_reused': bool(plan and not dry_run)}
        if plan:
            response.update(preflight_token=plan['token'], preflight_expires_at=plan['expires_at'])
        if not dry_run:
            report('write', len(items))
            working.setdefault('requests', {})[request_id] = {'fingerprint': fingerprint, 'response': deepcopy(response)}
            if committed:
                store._write(working)
            else:
                # An atomic rejection records only its receipt, without advancing annotation revision.
                _atomic_write(store.path, working)
            if plan:
                annotation_preflight.path_for(store, request_id).unlink(missing_ok=True)
        report('complete', len(items))
        return response
