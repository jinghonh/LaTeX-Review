"""Durable M4 helper; parent owns authenticated routes and coordinator lifecycle.

Uses namespaced versioned tables in checks.sqlite3, never changes shared tables or knowledge.
Only locally-created requests become import authority. Network calls are opt-in.
"""
from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Event, RLock, BoundedSemaphore
from types import MappingProxyType
from uuid import uuid4

from ..identity import identity_hash
from .document import build_check_document
from .engine import evaluate_current
from .semantic import (CallerConfig, SemanticCaller, SemanticRequest, SemanticError,
                       build_request, validate_candidate, MAX_RESPONSE_BYTES, _load, _json)
from .snapshot import capture_snapshot


def _fail(code, message, status=422):
    from .service import CheckError
    raise CheckError(code, message, status)


class SemanticService:
    """Attach once to CheckCoordinator. Parent must call close() on shutdown.

    start/import are asynchronous and return request_id/state; get polls results.
    Export is permitted without configuring a model. No imported request endpoint
    exists. confirm_issue is an author decision, never confirmed knowledge.
    """
    def __init__(self, coordinator, *, transport=None):
        self.coordinator = coordinator
        from .semantic import openai_transport
        self.transport = transport or openai_transport
        self.network_slots = BoundedSemaphore(2)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="semantic-task")
        self.lock = RLock()
        self.databases, self.cancels, self.callers, self.futures = {}, {}, {}, {}
        self.closed = False
        self.leases = {}
        self.active_network = 0

    @contextmanager
    def _connect(self, path):
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _database(self, paper_id):
        context = self.coordinator._paper(paper_id)
        with self.lock:
            if self.closed:
                _fail('unavailable', '语义服务已关闭', 503)
            if paper_id not in self.leases:
                from ..local_state import file_lock
                path = context['store'].path
                lease = file_lock(path.with_name(path.name + '.semantic-owner.lock'), blocking=False)
                try:
                    lease.__enter__()
                except BlockingIOError:
                    # A live process owns pending work. Read-only polling/export
                    # is safe; never recover it, dispatch, import, or decide here.
                    with self._connect(path) as db:
                        initialized = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_tasks'").fetchone()
                    if not initialized:
                        _fail('semantic_initializing', '另一个进程正在初始化语义服务', 503)
                    self.databases[paper_id] = path
                    return context, path
                self.leases[paper_id] = lease
                # Kernel lease, not PID heuristics: process exit releases it.
                # Initialization/recovery is small and never waits for paid work.
                with self._connect(path) as db:
                    db.execute('BEGIN EXCLUSIVE')
                    db.execute('CREATE TABLE IF NOT EXISTS semantic_schema (version INTEGER NOT NULL)')
                    row = db.execute('SELECT version FROM semantic_schema').fetchone()
                    version = row[0] if row else 0
                    if version not in (0, 1):
                        _fail('semantic_store_version', '不支持的语义数据库版本', 409)
                    if version == 0:
                        db.execute('CREATE TABLE semantic_tasks (request_id TEXT PRIMARY KEY, state TEXT NOT NULL, mode TEXT NOT NULL, task TEXT NOT NULL, called INTEGER NOT NULL DEFAULT 0, authority_json TEXT, result_json TEXT, error_json TEXT)')
                        db.execute('CREATE TABLE semantic_decisions (decision_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, candidate_id TEXT NOT NULL, input_id TEXT NOT NULL, fingerprint TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL)')
                        db.execute('CREATE INDEX semantic_rejected_fingerprint ON semantic_decisions(input_id,fingerprint,action)')
                        db.execute('INSERT INTO semantic_schema VALUES(1)')
                    # Restart recovery never launches a paid continuation.
                    db.execute("UPDATE semantic_tasks SET state='interrupted', error_json=? WHERE state IN ('queued','running','importing','cancelling')", (_json({'code': 'interrupted', 'message': '服务重启，任务未自动恢复'}),))
                self.databases[paper_id] = path
        return context, self.databases[paper_id]

    def _require_owner(self, paper_id):
        if paper_id not in self.leases:
            _fail('semantic_owner_active', '另一个本地进程持有语义执行租约；请连接原协调器，不会重复调用', 409)

    def _release_leases_if_idle(self, _future=None):
        with self.lock:
            if self.closed and not self.active_network and all(f.done() for f in self.futures.values()):
                for lease in self.leases.values():
                    lease.__exit__(None, None, None)
                self.leases.clear()

    def _track(self, paper_id, request_id, future):
        self.futures[(paper_id, request_id)] = future
        future.add_done_callback(self._release_leases_if_idle)

    def _network(self, endpoint, key, body, timeout):
        from time import monotonic
        started = monotonic()
        if not self.network_slots.acquire(timeout=timeout):
            raise TimeoutError('semantic provider capacity deadline')
        try:
            remaining = timeout - (monotonic() - started)
            payload = json.loads(body['messages'][1]['content'])
            event = self.cancels.get((payload['versions']['paper_id'], payload['request_id']))
            with self.lock:
                if remaining <= 0 or self.closed or (event and event.is_set()):
                    raise TimeoutError('semantic task no longer dispatchable')
                self.active_network += 1
            try:
                return self.transport(endpoint, key, body, remaining)
            finally:
                with self.lock:
                    self.active_network -= 1
                    self._release_leases_if_idle()
        finally:
            self.network_slots.release()

    def _capture(self, context):
        config = self.coordinator._configuration(context)
        knowledge_state = self.coordinator._knowledge(context, config).read()
        if knowledge_state['status'] not in {'ok', 'missing'}:
            _fail('knowledge_unavailable', '知识损坏或来源不可用；基础规则可继续，请先恢复知识', 409)
        knowledge = knowledge_state['registry']
        paper = context['paper']
        snapshot = capture_snapshot(paper['entry'], root=paper['root'], configuration=config, include_title=True)
        document = build_check_document(snapshot)
        report = evaluate_current(document, knowledge=knowledge, configuration=config)
        return document, report['input'], knowledge, config

    @staticmethod
    def _authority(request):
        return {'payload': request._json, 'input': request._input_json,
                'files': {name: b64encode(raw).decode('ascii') for name, raw in request._files.items()}}

    @staticmethod
    def _restore(raw):
        # This data is read only from host-created database records, never HTTP.
        value = json.loads(raw)
        return SemanticRequest(value['payload'], MappingProxyType({n: b64decode(b) for n, b in value['files'].items()}), value['input'])

    def _row(self, path, request_id):
        with self._connect(path) as db:
            row = db.execute('SELECT * FROM semantic_tasks WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            _fail('unknown_request', '没有本地主机创建的对应请求', 404)
        return dict(row)

    def _update(self, path, request_id, **fields):
        allowed = {'state', 'authority_json', 'result_json', 'error_json'}
        if not set(fields) <= allowed:
            raise ValueError('internal update fields')
        with self._connect(path) as db:
            db.execute('UPDATE semantic_tasks SET ' + ','.join(k + '=?' for k in fields) + ' WHERE request_id=?', (*fields.values(), request_id))

    @staticmethod
    def _check_page(document, config, page_snapshot):
        if page_snapshot is None:
            return
        frozen = page_snapshot.get('identity', page_snapshot)
        if (document.snapshot.identity['files'] != frozen.get('files')
                or identity_hash(config) != page_snapshot.get('configuration_hash', frozen.get('configuration_hash'))):
            _fail('page_input_conflict', '排队期间页面输入发生变化，请重建预览', 409)

    def start(self, paper_id, body, *, page_snapshot=None):
        if not isinstance(body, dict) or set(body) - {'task', 'mode', 'unit_indexes', 'input_id', 'page_version', 'paper_id'}:
            _fail('unknown_field', '不支持的语义任务字段')
        from .semantic import TASK_KINDS
        task, mode = body.get('task'), body.get('mode', 'export')
        if task not in TASK_KINDS or mode not in {'export', 'call'}:
            _fail('semantic_task', '请选择有限任务和 export/call 模式')
        indexes = body.get('unit_indexes')
        if indexes is not None and (not isinstance(indexes, list) or len(indexes) > 512 or any(type(i) is not int or i < 0 for i in indexes)):
            _fail('unit_selection', '片段选择无效或超限')
        selection_input_id = body.get('input_id')
        if indexes is not None and (not indexes or not isinstance(selection_input_id, str) or len(selection_input_id) != 64):
            _fail('unit_selection', '显式片段选择需绑定当前检查 input_id')
        context, path = self._database(paper_id)
        self._require_owner(paper_id)
        if mode == 'call':
            config = self.coordinator._configuration(context).get('semantic', {})
            CallerConfig(**config).validate()
        request_id = uuid4().hex
        with self.lock:
            if self.closed:
                _fail('unavailable', '语义服务已关闭', 503)
            self.cancels[(paper_id, request_id)] = Event()
            with self._connect(path) as db:
                db.execute("INSERT INTO semantic_tasks(request_id,state,mode,task,called) VALUES(?,'queued',?,?,?)", (request_id, mode, task, int(mode == 'call')))
            self._track(paper_id, request_id, self.executor.submit(self._create, paper_id, context, path, request_id, task, mode, indexes, deepcopy(page_snapshot), selection_input_id))
        return self.get(paper_id, request_id)

    def _create(self, paper_id, context, path, request_id, task, mode, indexes, page_snapshot, selection_input_id):
        cancel = self.cancels[(paper_id, request_id)]
        try:
            if cancel.is_set():
                self._update(path, request_id, state='cancelled')
                return
            self._update(path, request_id, state='running')
            document, current, knowledge, config = self._capture(context)
            self._check_page(document, config, page_snapshot)
            if selection_input_id is not None and identity_hash(current) != selection_input_id:
                _fail('stale_input', '片段选择的检查输入已过期，请重新加载', 409)
            request = build_request(document, current, task, knowledge=knowledge, unit_indexes=indexes, request_id=request_id)
            self._update(path, request_id, authority_json=_json(self._authority(request)))
            if cancel.is_set():
                self._update(path, request_id, state='cancelled')
            elif mode == 'export':
                self._update(path, request_id, state='ready')
            else:
                settings = CallerConfig(**config.get('semantic', {}))
                settings.validate()
                # Shared caller enforces <=2 actual provider requests across papers.
                with self.lock:
                    key = _json(settings.__dict__)
                    if key not in self.callers:
                        self.callers[key] = SemanticCaller(settings, transport=self._network)
                    caller = self.callers[key]
                result = caller.run(request, lambda: self._capture(context)[1], cancel=cancel)
                result_data = result.validation.to_dict() if result.validation else {'status': result.status, 'candidates': [], 'errors': [result.error] if result.error else []}
                result_data['execution'] = result.to_dict()
                result_data['execution'].pop('validation', None)
                self._save_result(path, request, result_data, cancel)
        except Exception as exc:
            self._update(path, request_id, state='cancelled' if cancel.is_set() else 'failed',
                         error_json=_json({'code': getattr(exc, 'code', 'semantic_failed'), 'message': str(exc)}))

    def invoke(self, paper_id, request_id, *, page_snapshot=None):
        context, path = self._database(paper_id)
        self._require_owner(paper_id)
        row = self._row(path, request_id)
        if row['state'] in {'queued', 'running', 'importing', 'cancelling'} or not row['authority_json']:
            _fail('not_ready', '请求未准备好或任务正在运行', 409)
        config = self.coordinator._configuration(context).get('semantic', {})
        settings = CallerConfig(**config)
        settings.validate()
        request = self._restore(row['authority_json'])
        with self.lock:
            # An import may have queued while config validation ran above.
            if self._row(path, request_id)['state'] in {'queued', 'running', 'importing', 'cancelling'}:
                _fail('not_ready', '任务正在运行，请等待', 409)
            if self.closed:
                _fail('unavailable', '语义服务已关闭', 503)
            cancel = Event()
            with self._connect(path) as db:
                changed = db.execute("UPDATE semantic_tasks SET called=1,state='running' WHERE request_id=? AND called=0", (request_id,)).rowcount
                if not changed:
                    _fail('already_called', '此请求已使用调用预算；请创建新的手动请求', 409)
            self.cancels[(paper_id, request_id)] = cancel
            self._track(paper_id, request_id, self.executor.submit(self._execute_call, context, path, request, settings, cancel, deepcopy(page_snapshot)))
        return self.get(paper_id, request_id)

    def _execute_call(self, context, path, request, settings, cancel, page_snapshot):
        try:
            if cancel.is_set():
                self._update(path, request.request_id, state='cancelled')
                return
            if page_snapshot is not None:
                document, _, _, config = self._capture(context)
                self._check_page(document, config, page_snapshot)
            with self.lock:
                key = _json(settings.__dict__)
                if key not in self.callers:
                    self.callers[key] = SemanticCaller(settings, transport=self._network)
                caller = self.callers[key]
            run = caller.run(request, lambda: self._capture(context)[1], cancel=cancel)
            result = run.validation.to_dict() if run.validation else {'status': run.status, 'candidates': [], 'errors': [run.error] if run.error else []}
            result['execution'] = run.to_dict()
            result['execution'].pop('validation', None)
            self._save_result(path, request, result, cancel)
        except Exception as exc:
            self._update(path, request.request_id, state='cancelled' if cancel.is_set() else 'failed', error_json=_json({'code': getattr(exc, 'code', 'semantic_failed'), 'message': str(exc)}))

    def _save_result(self, path, request, result, cancel):
        with self.lock:
            self._save_result_locked(path, request, result, cancel)

    def _save_result_locked(self, path, request, result, cancel):
        if cancel.is_set():
            self._update(path, request.request_id, state='cancelled')
            return
        with self._connect(path) as db:
            for candidate in result.get('candidates', []):
                # Evidence identity, not model wording: paraphrases of a rejected
                # hypothesis must not re-enter the queue for the same full input.
                fingerprint = identity_hash({'kind': candidate['kind'],
                    'sources': sorted(candidate['sources'], key=_json),
                    'object_ids': sorted(candidate.get('object_ids', []))})
                candidate['fingerprint'] = fingerprint
                rejected = db.execute("SELECT 1 FROM semantic_decisions WHERE input_id=? AND fingerprint=? AND action='reject'", (request.input_id, fingerprint)).fetchone()
                previous = db.execute('SELECT action FROM semantic_decisions WHERE request_id=? AND candidate_id=? ORDER BY rowid DESC LIMIT 1', (request.request_id, candidate['candidate_id'])).fetchone()
                candidate['review_state'] = 'rejected' if rejected else 'confirmed' if previous and previous[0] == 'confirm_issue' else 'unreviewed'
            db.execute('UPDATE semantic_tasks SET state=?,result_json=? WHERE request_id=?', (result['status'], _json(result), request.request_id))

    def get(self, paper_id, request_id):
        context, path = self._database(paper_id)
        row = self._row(path, request_id)
        settings = self.coordinator._configuration(context).get('semantic', {})
        authority = self._restore(row['authority_json']) if row['authority_json'] else None
        effective_provider = settings.get('provider', CallerConfig().provider)
        effective_thinking = settings.get('thinking', CallerConfig().thinking)
        return {'request_id': request_id, 'state': row['state'], 'task': row['task'], 'mode': row['mode'],
                'called': bool(row['called']), 'model_configured': settings.get('enabled') is True,
                'budget': {k: settings.get(k, getattr(CallerConfig(), k)) for k in ('max_requests', 'max_input_tokens', 'max_output_tokens', 'concurrency', 'timeout')},
                'provider': effective_provider, 'thinking': effective_thinking,
                'model': settings.get('model') if settings.get('enabled') is True else None,
                'input_id': authority.input_id if authority else None,
                'preview': {'entry': authority.payload['versions']['entry'], 'unit_count': len(authority.payload['evidence_units']),
                            'sections': list(dict.fromkeys(' / '.join(u['section_path']) for u in authority.payload['evidence_units']))} if authority else None,
                'result': json.loads(row['result_json']) if row['result_json'] else None,
                'error': json.loads(row['error_json']) if row['error_json'] else None}

    def export(self, paper_id, request_id):
        _, path = self._database(paper_id)
        row = self._row(path, request_id)
        if not row['authority_json']:
            _fail('not_ready', '请求片段尚未准备完成', 409)
        return self._restore(row['authority_json']).payload

    def import_response(self, paper_id, body):
        if not isinstance(body, dict) or set(body) - {'request_id', 'response', 'page_version', 'paper_id'} or not isinstance(body.get('request_id'), str) or 'response' not in body:
            _fail('semantic_import', '导入必须指定本地 request_id 和 response')
        response = _load(body['response'], MAX_RESPONSE_BYTES)
        context, path = self._database(paper_id)
        self._require_owner(paper_id)
        request_id = body['request_id']
        with self.lock:
            if self.closed:
                _fail('unavailable', '语义服务已关闭', 503)
            row = self._row(path, request_id)
            if row['state'] in {'queued', 'running', 'importing', 'cancelling'}:
                _fail('task_running', '请等待当前任务完成', 409)
            if not row['authority_json']:
                _fail('unknown_request', '本地请求缺少不可变证据快照', 409)
            request = self._restore(row['authority_json'])
            cancel = Event()
            self.cancels[(paper_id, request_id)] = cancel
            self._update(path, request_id, state='importing', error_json=None)
            self._track(paper_id, request_id, self.executor.submit(self._import, context, path, request, response, cancel))
        return self.get(paper_id, request_id)

    def _import(self, context, path, request, response, cancel):
        try:
            result = validate_candidate(request, response, self._capture(context)[1])
            self._save_result(path, request, result.to_dict(), cancel)
        except Exception as exc:
            self._update(path, request.request_id, state='cancelled' if cancel.is_set() else 'failed', error_json=_json({'code': getattr(exc, 'code', 'semantic_import_failed'), 'message': str(exc)}))

    def cancel(self, paper_id, request_id):
        _, path = self._database(paper_id)
        self._require_owner(paper_id)
        with self.lock:
            row = self._row(path, request_id)
            event = self.cancels.get((paper_id, request_id))
            if event and row['state'] in {'queued', 'running', 'importing', 'cancelling'}:
                event.set()
                self._update(path, request_id, state='cancelled')
        return self.get(paper_id, request_id)

    def decide(self, paper_id, body):
        with self.lock:
            return self._decide_locked(paper_id, body)

    def _decide_locked(self, paper_id, body):
        fields = {'request_id', 'candidate_id', 'input_id', 'action', 'reason', 'decision_id'}
        if not isinstance(body, dict) or set(body) != fields or any(not isinstance(body[k], str) or not body[k] or len(body[k]) > 4096 for k in fields):
            _fail('semantic_decision', '决定字段缺失、未知或超限')
        if body['action'] not in {'reject', 'confirm_issue', 'defer'}:
            _fail('semantic_decision', '关系确认必须另行显式更新知识；此处仅支持拒绝、确认问题和暂不决定')
        context, path = self._database(paper_id)
        self._require_owner(paper_id)
        row = self._row(path, body['request_id'])
        if row['state'] in {'queued', 'running', 'importing', 'cancelling'}:
            _fail('task_running', '任务运行中，请等待候选验证完成', 409)
        if not row['authority_json'] or not row['result_json']:
            _fail('candidate_not_found', '尚无已验证候选', 404)
        request = self._restore(row['authority_json'])
        result = json.loads(row['result_json'])
        candidate = next((c for c in result['candidates'] if c['candidate_id'] == body['candidate_id']), None)
        if not candidate:
            _fail('candidate_not_found', '候选不存在', 404)
        if body['input_id'] != request.input_id or identity_hash(self._capture(context)[1]) != request.input_id or candidate['lifecycle'] != 'current':
            _fail('stale_input', '证据已过期，请重新检查', 409)
        if body['action'] == 'confirm_issue' and candidate['kind'] not in {'potential_contradiction', 'missing_qualification'}:
            _fail('decision_type', '概念关系需单独的作者知识确认，不是问题确认')
        with self._connect(path) as db:
            previous = db.execute('SELECT * FROM semantic_decisions WHERE decision_id=?', (body['decision_id'],)).fetchone()
            values = (body['decision_id'], request.request_id, candidate['candidate_id'], request.input_id, candidate['fingerprint'], body['action'], body['reason'])
            if previous and tuple(previous) != values:
                _fail('decision_conflict', '幂等决定 ID 已用于不同内容', 409)
            if not previous:
                db.execute('INSERT INTO semantic_decisions VALUES(?,?,?,?,?,?,?)', values)
            candidate['review_state'] = {'reject': 'rejected', 'confirm_issue': 'confirmed', 'defer': 'unreviewed'}[body['action']]
            db.execute('UPDATE semantic_tasks SET result_json=? WHERE request_id=?', (_json(result), request.request_id))
        return self.get(paper_id, request.request_id)

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.cancels.values():
                event.set()
            for caller in self.callers.values():
                caller.close()
            self.executor.shutdown(wait=False, cancel_futures=True)
            # Retain the kernel lease through late timed-out provider futures.
            # They cannot publish results, but still occupy paid-call capacity.
            self._release_leases_if_idle()
