"""Annotation transaction: locking, revisions, batch receipts, preflight and source rollback."""
from contextlib import contextmanager
from copy import deepcopy, copy
import fcntl
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import uuid

from ..version_storage import unpack_storage
from ._validation import (SCHEMA_VERSION, AnnotationConflict, AnnotationError, thread_metadata, _hash, _now,
                          _text, _integer, _keys, _author, _category)
from .content import _semantic_units, locate, make_anchor, validate_content


class AnnotationStore:
    """One disk file per project entry, with interprocess locks and revision checks.

    Owns mutation locking, revision checks, batch receipts, preflight lifecycle
    and source rollback. Batch validation and source inspection stay internal.

    Report manifests contain only identity/location metadata. HTTP callers cannot
    choose paths or submit a new content manifest. CLI users explicitly choose roots.
    """
    def __init__(self, project_root, project_id):
        self.root = Path(project_root).expanduser().resolve()
        if not isinstance(project_id, str) or len(project_id) != 64 or any(c not in '0123456789abcdef' for c in project_id):
            raise AnnotationError('项目 ID 无效')
        self.project_id = project_id
        self.directory = self.root / '.latex-review' / 'annotations'
        self.path = self.directory / f'{project_id}.sqlite3'
        self.legacy_path = self.directory / f'{project_id}.json'
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
    def _locked(self, *, restore_versions=True):
        from ._storage import Database
        if any(p.is_symlink() for p in (self.directory, self.directory.parent, self.path,
                                       self.legacy_path, self.legacy_path.with_suffix('.json.migrated'), self.lock_path,
                                       Path(str(self.path) + '-wal'), Path(str(self.path) + '-shm'))):
            raise AnnotationError('批注存储路径不可为符号链接')
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            database = Database(self.path, self.project_id, SCHEMA_VERSION)
            if not self.path.exists():
                legacy = None
                if self.legacy_path.exists():
                    try:
                        legacy = unpack_storage(json.loads(self.legacy_path.read_text(encoding='utf-8')))
                        if legacy['project_id'] != self.project_id or legacy['schema_version'] != SCHEMA_VERSION:
                            raise ValueError('批注项目身份无效')
                        self._import({'versions': {}, 'threads': {}},
                                     {k: v for k, v in legacy.items() if k != 'requests'})
                    except (ValueError, TypeError, KeyError) as error:
                        raise AnnotationError('批注存储读取失败') from error
                temporary_fd, temporary_name = tempfile.mkstemp(prefix='.migration-', suffix='.sqlite3', dir=self.directory)
                os.close(temporary_fd)
                temporary = Path(temporary_name)
                try:
                    Database(temporary, self.project_id, SCHEMA_VERSION).initialize(legacy)
                    os.replace(temporary, self.path)
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    for path in (temporary, Path(str(temporary)+'-wal'), Path(str(temporary)+'-shm')):
                        path.unlink(missing_ok=True)
                if legacy is not None:
                    backup = self.legacy_path.with_suffix('.json.migrated')
                    if backup.is_symlink():
                        raise AnnotationError('迁移备份不可为符号链接')
                    os.replace(self.legacy_path, backup)
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            with database.transaction() as data:
                if getattr(self, '_database_keeper', None):
                    data['threads'].connection.execute('PRAGMA wal_autocheckpoint=0')
                yield data
        except sqlite3.DatabaseError as error:
            raise AnnotationError('批注数据库读取或提交失败') from error
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, data):
        data['revision'] += 1
        self._persist(data)

    def _persist(self, data):
        from ._storage import Database
        Database.persist(data)

    def prepare(self, content):
        from ._storage import Database
        if not getattr(self, '_database_keeper', None):
            # Keep WAL open between HTTP requests; closing the final connection checkpoints it.
            with self._locked():
                pass
            self._database_keeper = Database(self.path, self.project_id, SCHEMA_VERSION).connect()
            from threading import Event, Thread
            self._checkpoint_stop = Event()
            def checkpoint():
                import sqlite3
                connection = Database(self.path, self.project_id, SCHEMA_VERSION).connect()
                try:
                    while not self._checkpoint_stop.wait(1):
                        try:
                            connection.execute('PRAGMA wal_checkpoint(PASSIVE)')
                        except sqlite3.OperationalError:
                            pass  # Another writer/checkpointer owns the database; retry next interval.
                finally:
                    connection.close()
            self._checkpoint_thread = Thread(target=checkpoint, name='annotation-checkpoint', daemon=True)
            self._checkpoint_thread.start()
        with self._locked() as data:
            self._content(data, content)
            self._persist(data)
        self._prepared_content = content
        self._prepared_inode = self.path.stat().st_ino
        self._prepared_content_hash = _hash(content)

    def close(self):
        if getattr(self, '_database_keeper', None):
            self._checkpoint_stop.set()
            self._checkpoint_thread.join()
            self._database_keeper.close()
            self._database_keeper = None

    def _content(self, data, content):
        if getattr(self, '_prepared_content', None) is content and self.path.stat().st_ino == self._prepared_inode:
            return None
        validate_content(content)
        if content['project_id'] != self.project_id:
            raise AnnotationError('不能访问其他项目的批注')
        existing = data['versions'].get(content['version_id'])
        if existing:
            if _semantic_units(existing['units']) != _semantic_units(content['units']):
                raise AnnotationConflict('同版本正文坐标不一致，请保留草稿并重新生成报告')
        # Render metadata may vary with the comparison baseline; version identity does not.
        data['versions'][content['version_id']] = deepcopy(content)
        return existing

    def read(self, content, progress=None):
        if progress:
            progress('read', 0, 0)
        with self._locked() as data:
            self._content(data, content)
            return self._view(data, content, progress=progress)

    def read_thread(self, content, thread_id):
        with self._locked() as data:
            self._content(data, content)
            thread = data['threads'].get(thread_id)
            return self._view({**data, 'threads': {thread_id: thread} if thread else {}}, content)

    def metadata(self, content):
        """Read identity/revision without constructing or relocating any thread."""
        with self._locked(restore_versions=False) as data:
            return {'project_id': self.project_id, 'version_id': content['version_id'], 'revision': data['revision']}

    def changes(self, content, since):
        _integer(since, 'since')
        with self._locked() as data:
            if since > data['revision']:
                raise AnnotationConflict('客户端修订号超过当前存储，请重新读取')
            self._content(data, content)
            ids = [row[0] for row in data['threads'].connection.execute(
                'SELECT DISTINCT thread_id FROM changes WHERE revision>?', (since,))]
            return self._delta(data, content, ids, since)

    def inventory(self, content):
        """Audit inventory does not need anchors, histories or location computation."""
        with self._locked(restore_versions=False) as data:
            threads = [{**{k: deepcopy(v) for k, v in t.items() if k in ('id', 'resolved', 'title', 'tags')}, 'messages': [{k: v for k, v in m.items() if k in
                       ('id', 'author', 'body', 'reply_to', 'deleted_at')} for m in t['messages']]}
                       for t in data['threads'].values() if not t.get('deleted_at')]
            return {'project_id': self.project_id, 'version_id': content['version_id'],
                    'revision': data['revision'], 'threads': threads}

    def _view(self, data, content, progress=None):
        from ._locations import LocationCache, expand_location
        threads = []
        cache = None
        total = sum(not t.get('deleted_at') for t in data['threads'].values())
        for value in data['threads'].values():
            if value.get('deleted_at'):
                continue
            # JSON round-trip restores independent public anchor objects, even when a transaction reuses immutable anchors.
            thread = json.loads(json.dumps(value, ensure_ascii=False))
            association = thread['associations'].get(content['version_id'])
            if association is None:
                if cache is None:
                    cache = LocationCache(self, content)
                cached = cache.get(thread['current_anchor'])
                if cached is None:
                    association = locate(content, thread['current_anchor'])
                    cache.put(thread['current_anchor'], association)
                else:
                    association = expand_location(content, cached)
                association['persisted'] = False
            association = deepcopy(association)
            if association.get('container'):
                container = association['container']
                unit = next(u for u in content['units'] if u['id'] == container['unit_id'])
                container.update(selector=unit['selector'], order=unit['order'])
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
            if progress:
                progress('locate', len(threads), total)
        if cache:
            cache.save()
        threads.sort(key=lambda t: ((t['association'].get('anchor') or {}).get('order', 10**12),
                                    (t['association'].get('anchor') or {}).get('start') or 0, t['created_at'], t['id']))
        return {'schema_version': SCHEMA_VERSION, 'project_id': self.project_id, 'version_id': content['version_id'],
                'revision': data['revision'], 'threads': threads}

    def dispatch(self, content, request, progress=None, *, incremental=False):
        """Shared structured mutation API used unchanged by CLI and local HTTP."""
        if isinstance(request, dict) and request.get('operation') == 'batch':
            return self.batch(content, request, progress=progress, incremental=incremental)
        if isinstance(request, dict) and request.get('operation') in ('suggestion_preview', 'suggestion_apply'):
            return self._suggestion(content, request)
        self._validate_request(request)
        revision = request['expected_revision']
        with self._locked() as data:
            request_id = request.get('request_id')
            fingerprint = _hash({k: v for k, v in request.items() if k != 'expected_revision'}) if request_id else None
            if request_id:
                previous = data['requests'].get(request_id)
                if previous:
                    if previous['fingerprint'] != fingerprint:
                        raise AnnotationConflict('请求 ID 已用于不同载荷')
                    return previous['response']
            if data['revision'] != revision:
                raise AnnotationConflict(f"批注已被其他审阅者更新（期望修订号 {revision}，实际修订号 {data['revision']}），请保留草稿并重新读取",
                    {'code': 'revision_conflict', 'reasons': ['annotation_revision_changed'],
                     'expected_revision': revision, 'actual_revision': data['revision']})
            self._content(data, content)
            if progress:
                progress('apply', 0, 1)
            result = self._apply(data, content, request)
            if progress:
                progress('write', 1, 1)
            if request['operation'] != 'reconcile' or result['reconciled']:
                self._write(data)
            if incremental:
                ids = result['thread_ids'] if request['operation'] == 'reconcile' else [result['thread_id']] if request['operation'] == 'create' else [request['thread_id']]
                response = {**self._delta(data, content, ids, revision), 'result': result}
            else:
                response = {**self._view(data, content, progress=progress), 'result': result}
            if request_id:
                data['requests'][request_id] = {'fingerprint': fingerprint, 'response': response}
                self._persist(data)
            return response

    def _delta(self, data, content, ids, start_revision):
        affected = {key: data['threads'][key] for key in dict.fromkeys(ids)}
        threads, anchors = [], {}
        def page_anchor(old):
            key = (old['unit_id'], old.get('start'), old.get('end'), old['quote'])
            if key not in anchors:
                selection = {'unit_id': old['unit_id']}
                if old['kind'] == 'text':
                    selection.update(start=old['start'], end=old['end'], quote=old['quote'])
                anchors[key] = make_anchor(content, selection)
            return anchors[key]
        for key, thread in affected.items():
            if thread.get('deleted_at'):
                continue
            association = thread['associations'].get(content['version_id'])
            if association is None:
                # Preparation/relocation is outside the normal exact-association fast path.
                rendered = self._view({**data, 'threads': {key: thread}}, content)['threads'][0]
                association = rendered['association']
            else:
                association = {**association,
                    'anchor': page_anchor(association['anchor']) if association['anchor'] else None,
                    'candidates': [{**candidate, 'anchor': page_anchor(candidate['anchor'])}
                                   for candidate in association['candidates']]}
                if association.get('container'):
                    container = dict(association['container'])
                    index = getattr(content, 'unit_index', None)
                    unit = index[container['unit_id']] if index else next(u for u in content['units'] if u['id'] == container['unit_id'])
                    container.update(selector=unit['selector'], order=unit['order'])
                    association['container'] = container
            # Transaction-local values are serialized before they escape; no live store graph is shared.
            rendered = {k: v for k, v in thread.items() if k not in
                ('original_anchor', 'current_anchor', 'associations', 'messages', 'history')}
            rendered.update(original_anchor={k: thread['original_anchor'][k] for k in ('quote', 'kind')},
                messages=[{k: v for k, v in message.items() if k != 'edits'} for message in thread['messages']],
                history=[{k: event[k] for k in ('at', 'method')} for event in thread['history']], association=association)
            threads.append(rendered)
        threads.sort(key=lambda t: ((t['association'].get('anchor') or {}).get('order', 10**12),
                                   (t['association'].get('anchor') or {}).get('start') or 0, t['created_at'], t['id']))
        return {'schema_version': SCHEMA_VERSION, 'project_id': self.project_id, 'version_id': content['version_id'],
                'revision': data['revision'], 'threads': threads, 'incremental': True,
                'start_revision': start_revision,
                'deleted_thread_ids': [key for key, thread in affected.items() if thread.get('deleted_at')]}

    @staticmethod
    def _validate_request(request):
        _keys(request, ('operation', 'expected_revision', 'thread_id', 'message_id', 'selection', 'body',
                        'author', 'category', 'bundle', 'reply_to', 'suggested_replacement', 'title', 'tags', 'resolve', 'request_id'), ('operation', 'expected_revision'))
        revision = _integer(request['expected_revision'], 'expected_revision')
        operation = request['operation']
        fields = {
            'create': {'selection', 'body', 'author', 'category'},
            'reply': {'thread_id', 'body', 'author', 'category'},
            'edit': {'thread_id', 'message_id', 'body', 'category'},
            'delete_message': {'thread_id', 'message_id'}, 'delete_thread': {'thread_id'},
            'resolve': {'thread_id'}, 'reopen': {'thread_id'}, 'update_metadata': {'thread_id'},
            'associate': {'thread_id', 'selection'}, 'reconcile': set(), 'import': {'bundle'},
        }
        if not isinstance(operation, str) or operation not in fields:
            raise AnnotationError('未知批注操作')
        _keys(request, fields[operation] | {'operation', 'expected_revision', 'request_id'} | ({'reply_to', 'resolve'} if operation == 'reply' else set()) | ({'suggested_replacement'} if operation in ('create', 'reply') else set()) | ({'title', 'tags'} if operation in ('create', 'update_metadata') else set()),
              fields[operation] | {'operation', 'expected_revision'})
        if 'request_id' in request:
            _text(request['request_id'], 'request_id', 256)
        if 'resolve' in request and type(request['resolve']) is not bool:
            raise AnnotationError('resolve 须为布尔值')
        if operation in ('create', 'update_metadata'):
            thread_metadata(request)
        if operation == 'update_metadata' and not {'title', 'tags'}.intersection(request):
            raise AnnotationError('更新元数据需要 title 或 tags')

    def _apply(self, data, content, request):
        operation = request['operation']
        now = _now()
        result = None
        if operation == 'create':
            anchor = make_anchor(content, request['selection'])
            message = self._message(request, now)
            if 'suggested_replacement' in request:
                from ._suggestions import prepare
                message['suggested_replacement'] = prepare(content, anchor, request['suggested_replacement'])
            thread_id = str(uuid.uuid4())
            association = {'status': 'exact', 'anchor': anchor, 'candidates': [], 'reason': None}
            data['threads'][thread_id] = {'id': thread_id, 'project_id': self.project_id,
                'created_version_id': content['version_id'], 'created_at': now, 'updated_at': now,
                'resolved': False, 'deleted_at': None, 'messages': [message], 'original_anchor': anchor,
                'current_anchor': anchor, 'associations': {content['version_id']: association},
                'history': [{'at': now, 'method': 'created', 'anchor': anchor}],
                **thread_metadata(request)}
            result = {'thread_id': thread_id, 'message_id': message['id']}
        elif operation == 'reconcile':
            from ._locations import LocationCache, expand_location
            cache, count = None, 0
            changed_ids = []
            if hasattr(data['threads'], 'connection'):
                path = '$.associations."' + content['version_id'] + '"'
                ids = [row[0] for row in data['threads'].connection.execute(
                    'SELECT id FROM threads WHERE deleted=0 AND json_type(payload,?) IS NULL', (path,))]
                targets = (data['threads'][key] for key in ids)
            else:
                targets = data['threads'].values()
            for thread in targets:
                if thread['deleted_at'] or content['version_id'] in thread['associations']:
                    continue
                if cache is None:
                    cache = LocationCache(self, content)
                cached = cache.get(thread['current_anchor'])
                if cached is None:
                    association = locate(content, thread['current_anchor'])
                    cache.put(thread['current_anchor'], association)
                else:
                    association = expand_location(content, cached)
                thread['associations'][content['version_id']] = association
                count += 1
                changed_ids.append(thread['id'])
                if association['anchor']:
                    self._associate(thread, association['anchor'], association['status'], now)
            if cache:
                cache.save()
            result = {'reconciled': count, 'thread_ids': changed_ids}
        elif operation == 'import':
            result = self._import(data, request['bundle'])
        else:
            thread_id = request['thread_id']
            _text(thread_id, 'thread_id', 256)
            thread = data['threads'].get(thread_id)
            if thread is None or thread['deleted_at']:
                raise AnnotationError('批注线程不存在或已删除')
            if operation == 'reply':
                parent_id = request.get('reply_to', thread['messages'][0]['id'])
                if not any(m['id'] == parent_id and not m['deleted_at'] for m in thread['messages']):
                    raise AnnotationError('回复对象不存在或已删除')
                message = self._message(request, now)
                if 'suggested_replacement' in request:
                    from ._suggestions import prepare
                    association = thread['associations'].get(content['version_id'], {})
                    anchor = association.get('anchor')
                    if not anchor:
                        raise AnnotationError('建议需要当前版本的可靠关联')
                    message['suggested_replacement'] = prepare(content, anchor, request['suggested_replacement'])
                message['reply_to'] = parent_id
                thread['messages'].append(message)
                if request.get('resolve'):
                    thread['resolved'] = True
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
            elif operation == 'update_metadata':
                thread.update(thread_metadata(request))
                result = {'thread_id': thread_id}
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
        return result

    def batch(self, content, request, progress=None, *, incremental=False):
        """Own batch locking, preflight reuse, receipts and revision advancement."""
        from ._batch import OPERATIONS, validate_request
        revision, request_id, atomic, dry_run, items, fingerprint = validate_request(content, request)
        from . import _preflight as annotation_preflight
        def report(phase, completed=0):
            if progress:
                progress(phase, completed, len(items))
        report('lock')
        with self._locked() as data:
            previous = data.get('requests', {}).get(request_id)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise AnnotationConflict('请求 ID 已用于不同载荷')
                if not dry_run:
                    return previous['response']
            plan = None if dry_run else annotation_preflight.load(self, content, request, fingerprint, data['revision'], connection=data['threads'].connection)
            if revision != data['revision']:
                raise AnnotationConflict(f"批注已被其他审阅者更新（期望修订号 {revision}，实际修订号 {data['revision']}），请保留草稿并重新读取",
                        {'code': 'revision_conflict', 'reasons': ['annotation_revision_changed'],
                         'expected_revision': revision, 'actual_revision': data['revision']})
            report('validate')
            # Copy only containers and affected threads, never all stored versions per item.
            working = {**data, 'threads': copy(data['threads']), 'versions': copy(data['versions']),
                       'requests': copy(data['requests'])}
            self._content(working, content)
            results = deepcopy(plan['results']) if plan else []
            changed = {}
            if plan:
                working['threads'].update(deepcopy(plan['threads']))
                report('reuse', len(items))
            for index, item in enumerate([] if plan else items):
                candidate = {**working, 'threads': copy(working['threads'])}
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
                    self._validate_request(mutation)
                    result = self._apply(candidate, content, mutation) or {}
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
                working = {**data, 'requests': copy(data['requests'])}
                changed = {}
            if dry_run:
                plan = annotation_preflight.save(self, content, request, fingerprint, revision, changed, results, connection=data['threads'].connection)
                results = deepcopy(results)
                for result in results:
                    result.pop('thread_id', None) if result['operation'] == 'create' else None
                    result.pop('message_id', None)
            response = {'request_id': request_id, 'atomic': atomic, 'dry_run': dry_run, 'committed': committed,
                        'version_id': content['version_id'], 'revision': data['revision'] + int(committed),
                        'start_revision': data['revision'], **({'start_thread_count': data['threads'].connection.execute('SELECT count(*) FROM threads WHERE deleted=0').fetchone()[0]}
                           if incremental else {'start_thread_ids': data['threads'].active_ids()}),
                        'results': results, 'preflight_reused': bool(plan and not dry_run)}
            if plan:
                response.update(preflight_token=plan['token'], preflight_expires_at=plan['expires_at'])
            if not dry_run:
                report('write', len(items))
                if incremental:
                    response.update(self._delta(working, content,
                        [r['thread_id'] for r in results if r['ok'] and r.get('thread_id')], revision))
                    response['revision'] = data['revision'] + int(committed)
                working.setdefault('requests', {})[request_id] = {'fingerprint': fingerprint, 'response': response}
                if committed:
                    self._write(working)
                else:
                    # An atomic rejection records only its receipt, without advancing annotation revision.
                    self._persist(working)
                if plan:
                    data['threads'].connection.execute('DELETE FROM preflights WHERE id=?', (request_id,))
            report('complete', len(items))
            return response

    def _suggestion(self, content, request):
        """Commit a source suggestion, restoring source if storage fails."""
        from ._suggestions import error, inspect, replace
        import hashlib
        _keys(request, ('operation', 'expected_revision', 'thread_id', 'message_id'),
              ('operation', 'expected_revision', 'thread_id', 'message_id'))
        _integer(request['expected_revision'], 'expected_revision')
        with self._locked() as data:
            if data['revision'] != request['expected_revision']:
                exp = request['expected_revision']
                act = data['revision']
                raise AnnotationConflict(f"批注版本已变化（期望修订号 {exp}，实际修订号 {act}），请重新读取",
                    {'code': 'revision_conflict', 'reasons': ['annotation_revision_changed'],
                     'expected_revision': exp, 'actual_revision': act})
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
            path, original, updated, diff = inspect(self.root, suggestion)
            result = {'thread_id': thread['id'], 'message_id': message['id'], 'diff': diff, 'source_changed': False}
            if request['operation'] == 'suggestion_apply':
                replace(path, updated, expected=original)
                def rollback_source():
                    if path.read_bytes() == updated:
                        replace(path, original)
                data['rollback_source'] = rollback_source
                suggestion.update(status='applied', applied_at=_now(), applied_sha256=hashlib.sha256(updated).hexdigest())
                message['updated_at'] = thread['updated_at'] = _now()
                try:
                    self._write(data)
                except Exception:
                    # Restore the original source if recording the applied state fails.
                    if path.read_bytes() == updated:
                        replace(path, original)
                    raise
                result.update(source_changed=True, source_file=str(path))
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
            return {key: deepcopy(value) for key, value in data.items() if key != 'requests'}

    def compact(self):
        """Validate and atomically rewrite storage without changing its revision."""
        with self._locked() as data:
            bundle = {key: deepcopy(value) for key, value in data.items() if key != 'requests'}
            self._import({'versions': {}, 'threads': {}}, bundle)
            self._persist(data)

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
                       'messages', 'original_anchor', 'current_anchor', 'associations', 'history', 'title', 'tags'),
              ('id', 'project_id', 'created_version_id', 'created_at', 'updated_at', 'resolved', 'deleted_at',
               'messages', 'original_anchor', 'current_anchor', 'associations', 'history'))
        thread_metadata(thread)
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
            _keys(message, ('id', 'body', 'author', 'category', 'created_at', 'updated_at', 'deleted_at', 'edits', 'reply_to', 'suggested_replacement'),
                  ('id', 'body', 'author', 'category', 'created_at', 'updated_at', 'deleted_at', 'edits'))
            _text(message['id'], 'message id', 256)
            if message['id'] in message_ids:
                raise AnnotationError('导入回复 ID 重复')
            if 'reply_to' in message and message['reply_to'] not in message_ids:
                raise AnnotationError('回复对象必须是同一线程中的先前消息')
            message_ids.add(message['id'])
            if 'suggested_replacement' in message:
                from ._suggestions import validate
                validate(message['suggested_replacement'])
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
            _keys(association, ('status', 'anchor', 'candidates', 'reason', 'container'), ('status', 'anchor', 'candidates', 'reason'))
            if version_id not in versions or association['status'] not in ('exact', 'auto_migrated', 'manual', 'needs_relocation'):
                raise AnnotationError('导入关联状态或版本无效')
            if association['anchor']:
                check_anchor(association['anchor'])
                if association['anchor']['version_id'] != version_id:
                    raise AnnotationError('导入关联版本不一致')
            if not isinstance(association['candidates'], list):
                raise AnnotationError('导入候选须为列表')
            if 'container' in association:
                container = association['container']
                _keys(container, ('unit_id', 'kind', 'selector', 'order', 'source', 'section_path', 'selection_deleted', 'subunit', 'scope'),
                      ('unit_id', 'kind', 'selector', 'order', 'source', 'section_path', 'selection_deleted'))
                unit = next((u for u in versions[version_id]['units'] if u['id'] == container['unit_id']), None)
                if not unit or type(container['selection_deleted']) is not bool or any(
                        container[k] != unit[k] for k in ('kind', 'source', 'section_path')):
                    raise AnnotationError('导入容器关联无效')
                if 'scope' in container and (container['scope'] != 'table' or
                        unit.get('subunit', {}).get('type') not in ('cell', 'header')):
                    raise AnnotationError('导入容器范围无效')
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


def execute_request(store, content, envelope, progress=None, *, incremental=False):
    _keys(envelope, ('project_id', 'version_id', 'request'), ('project_id', 'version_id', 'request'))
    if envelope['project_id'] != content['project_id'] or envelope['version_id'] != content['version_id']:
        raise AnnotationConflict('正文项目或版本已变化，请保留草稿并重新读取正文')
    request = envelope['request']
    if isinstance(request, dict) and request.get('operation') == 'batch':
        return store.batch(content, request, progress=progress, incremental=incremental)
    if isinstance(request, dict) and request.get('operation') == 'locate':
        _keys(request, ('operation', 'selection'), ('operation', 'selection'))
        return {'anchor': make_anchor(content, request['selection'])}
    fast = incremental and request.get('operation') not in ('import', 'suggestion_preview', 'suggestion_apply')
    if not fast:
        return store.dispatch(content, request, progress=progress) if progress else store.dispatch(content, request)
    return store.dispatch(content, request, progress=progress, incremental=True)


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

