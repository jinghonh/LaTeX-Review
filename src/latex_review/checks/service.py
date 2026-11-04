"""Entry-owned check coordinator. Reading workers never own task execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock

from ..identity import identity_hash, paper_identity
from ..local_protocol import LocalError
from ..sources import SourceError
from .document import build_check_document
from .engine import evaluate_current
from .registry import KnowledgeStore
from .snapshot import capture_snapshot
from .store import CheckStore
from .service_state import ServiceState


class CheckError(LocalError):
    def __init__(self, code, message, status=422, **details):
        super().__init__(message, status)
        self.code, self.details = code, details

    def to_dict(self):
        return {'code': self.code, 'message': str(self), **self.details}


class CheckCoordinator:
    """One task queue per paper; CPU work and process locks stay off HTTP threads."""
    def __init__(self, state, paper_lookup):
        self.state, self.paper_lookup = Path(state), paper_lookup
        self.lock = RLock()
        self.papers = {}
        self.knowledge_sources = {}
        self.closed = False
        from uuid import uuid4
        self.session_id = uuid4().hex
        from .semantic_service import SemanticService
        self.semantic = SemanticService(self)

    def _paper(self, paper_id):
        with self.lock:
            if paper_id in self.papers:
                return self.papers[paper_id]
            paper = self.paper_lookup(paper_id)
            if not paper:
                raise CheckError('paper_not_found', '论文未注册', 404)
            identity = paper_identity(paper['root'], Path(paper['root']) / paper['entry'], require_entry=True)
            if identity['paper_id'] != paper_id:
                raise CheckError('paper_identity', '论文身份不符', 403)
            store = CheckStore(self.state / 'checks' / paper_id / 'checks.sqlite3')
            context = {'paper': dict(paper), 'store': store, 'service_state': ServiceState(store), 'tasks': {}, 'generation': 0,
                       'executor': ThreadPoolExecutor(max_workers=1, thread_name_prefix='checks'),
                       'recovered': False, 'current': None}
            # Queue recovery before any execution; the cross-process lock also
            # prevents marking a live CLI run interrupted.
            context['executor'].submit(self._recover, paper_id, context)
            self.papers[paper_id] = context
            return context

    def _recover(self, paper_id, context):
        with context['store'].paper_lock(paper_id):
            context['store'].recover_interrupted(paper_id)
            context['service_state'].recover(self.session_id)
        context['recovered'] = True

    def _configuration(self, context):
        paper = self.paper_lookup(context['paper']['paper_id']) or context['paper']
        return self._configuration_for(paper)

    def _configuration_for(self, paper):
        from ..cli import _config
        # The registered paper may have independent live/diff recipes. Neither
        # browser input nor a submitted body may choose filesystem paths.
        recipe = paper.get('checks_recipe', {})
        from ..project_profile import directory, load, settings
        from ..sources import ProjectSource
        root = Path(paper['root'])
        if recipe.get('config'):
            raw = _config(Path(recipe['config']), recipe.get('config_explicit', False))
        else:
            profile = load(root, paper['entry'])
            path = directory(root) / 'project.toml' if profile else root / '.latex-review.toml'
            raw = _config(path, False)
        from ..contract import ComparisonSource
        from .snapshot import canonical_check_configuration
        return canonical_check_configuration(settings(ProjectSource(
            'new', root, paper['entry'], ComparisonSource('directory', str(root)), configuration=raw)))

    def _knowledge(self, context, configuration=None):
        configuration = self._configuration(context) if configuration is None else configuration
        settings = configuration.get('knowledge', {})
        root = context['paper']['root']
        selected = self.knowledge_sources.get(root)
        return KnowledgeStore(root, self.state,
                              source=selected or settings.get('storage', 'auto'),
                              path=None if selected else settings.get('path'),
                              read_only=configuration.get('project', {}).get('read_only', False))

    def validate_page(self, paper_id, metadata):
        """Bind worker-authenticated page bytes to the current trusted source graph."""
        context = self._paper(paper_id)
        if not isinstance(metadata, dict) or not isinstance(metadata.get('identity', metadata), dict):
            raise CheckError('page_snapshot_unavailable', '页面检查快照无效，请重建预览', 409)
        identity = metadata.get('identity', metadata)
        configuration = self._configuration(context)
        expected_hash = metadata.get('configuration_hash', identity.get('configuration_hash'))
        if (identity.get('paper_id') != paper_id or identity.get('entry') != context['paper']['entry']
                or expected_hash != identity_hash(configuration)):
            raise CheckError('page_input_conflict', '页面论文或配置已过期，请刷新', 409)
        from ..contract import ComparisonSource
        from ..sources import ProjectSource
        from .snapshot import _raw_files
        root = Path(context['paper']['root'])
        source = ProjectSource('new', root, context['paper']['entry'],
                               ComparisonSource('directory', str(root)), configuration=configuration)
        try:
            files = {name: sha256(raw).hexdigest() for name, raw in _raw_files(source.expand()).items()}
        except (OSError, SourceError) as exc:
            raise CheckError('page_input_conflict', '当前源码无法验证，请重建预览', 409) from exc
        if files != identity.get('files'):
            raise CheckError('page_input_conflict', '源码已变更但页面尚未重建，请刷新预览', 409)

    def start(self, paper_id, body, *, page_snapshot=None):
        if set(body) - {'scope', 'page_version', 'paper_id', 'configuration_hash'}:
            raise CheckError('unknown_field', '不支持的任务字段', field='/')
        scope = body.get('scope', 'all')
        if scope not in ('all', 'changed'):
            raise CheckError('invalid_scope', 'scope 必须为 all 或 changed', field='/scope')
        context = self._paper(paper_id)
        if body.get('configuration_hash') is not None and body['configuration_hash'] != identity_hash(self._configuration(context)):
            raise CheckError('configuration_conflict', '检查配置不符，请刷新', 409)
        import uuid
        with self.lock:
            if self.closed:
                raise CheckError('unavailable', '检查协调器已关闭', 503)
            context['generation'] += 1
            task_id = uuid.uuid4().hex
            task = {'run_id': task_id, 'paper_id': paper_id, 'state': 'queued', 'scope': scope,
                    'page_version': body.get('page_version'), 'generation': context['generation'],
                    'cancel': Event(), 'store_run_id': None, 'input_id': None, 'error': None}
            context['tasks'][task_id] = task
            context.setdefault('page_inputs', {})[task_id] = page_snapshot
            context['service_state'].task(self.session_id, self._public_task(task))
            context['executor'].submit(self._execute, paper_id, context, task)
            return self._public_task(task)

    def _execute(self, paper_id, context, task):
        store = context['store']
        with store.paper_lock(paper_id):
            if task['cancel'].is_set():
                task['state'] = 'cancelled'
                context['service_state'].task(self.session_id, self._public_task(task))
                return
            # IDs returned to clients also survive entry restart.
            run_id = store.begin_run(paper_id, run_id=task['run_id'])
            task.update(state='running', store_run_id=run_id)
            context['service_state'].task(self.session_id, self._public_task(task))
            try:
                configuration = self._configuration(context)
                knowledge = self._knowledge(context, configuration).read()
                root, entry = context['paper']['root'], context['paper']['entry']
                snapshot = capture_snapshot(entry, root=root, configuration=configuration, include_title=True)
                page = context.get('page_inputs', {}).get(run_id)
                if page:
                    frozen = page.get('identity', page)
                    if (snapshot.identity['files'] != frozen.get('files')
                            or identity_hash(configuration) != page.get('configuration_hash', frozen.get('configuration_hash'))):
                        raise CheckError('page_input_conflict', '排队期间页面输入发生变化，请重建预览', 409)
                if snapshot.paper_id != paper_id:
                    raise CheckError('paper_identity', '快照身份不符', 403)
                previous = store.latest(paper_id)
                document = build_check_document(snapshot)
                report = evaluate_current(document, knowledge=knowledge['registry'], knowledge_status=knowledge,
                                          configuration=configuration,
                                          previous=previous['report'] if previous else None, scope=task['scope'])
                from .finding_review import apply_exceptions
                apply_exceptions(report, knowledge['registry'], document)
                task['input_id'] = report['input_id']
                # Re-expand after evaluation: catches new/missing dependencies,
                # not only modifications to previously known files.
                current_snapshot = capture_snapshot(entry, root=root, configuration=self._configuration(context), include_title=True)
                current_knowledge = self._knowledge(context).read()
                stale = (snapshot.content_id != current_snapshot.content_id or
                         knowledge['content_hash'] != current_knowledge['content_hash'])
                with self.lock:
                    cancelled = task['cancel'].is_set() or self.closed
                    stale = stale or task['generation'] != context['generation']
                    if cancelled:
                        store.finish_failed(run_id, {'code': 'cancelled'}, status='cancelled')
                        task['state'] = 'cancelled'
                    else:
                        store.save_report(report, run_id=run_id, make_current=not stale)
                        task['state'] = 'stale' if stale else report['status']
                        task['report'] = report
                        if not stale:
                            context['current'] = run_id
                            context['document'] = document
            except Exception as exc:
                cancelled = task['cancel'].is_set() or self.closed
                task.update(state='cancelled' if cancelled else 'failed',
                            error={'code': getattr(exc, 'code', 'check_failed'), 'message': str(exc)})
                store.finish_failed(run_id, task['error'], status='cancelled' if cancelled else 'failed')
            finally:
                context['service_state'].task(self.session_id, self._public_task(task))

    @staticmethod
    def _public_task(task):
        return {key: deepcopy(task.get(key)) for key in
                ('run_id', 'paper_id', 'state', 'scope', 'page_version', 'input_id', 'error')}

    def run(self, paper_id, run_id):
        context = self._paper(paper_id)
        task = context['tasks'].get(run_id)
        if task:
            return self._public_task(task)
        run = context['store'].get_run(run_id)
        if not run or run['paper_id'] != paper_id:
            saved = context['service_state'].get_task(run_id)
            if saved and saved['paper_id'] == paper_id:
                return saved
            raise CheckError('run_not_found', '检查任务不存在', 404)
        return {'run_id': run_id, 'paper_id': paper_id, 'state': run['status'],
                'input_id': run['input_id'], 'error': run['error']}

    def cancel(self, paper_id, run_id):
        context = self._paper(paper_id)
        with self.lock:
            task = context['tasks'].get(run_id)
            if task and task['state'] in ('queued', 'running'):
                task['cancel'].set()
                task['state'] = 'cancelling'
                context['service_state'].task(self.session_id, self._public_task(task))
        return self.run(paper_id, run_id)

    def report(self, paper_id, run_id=None):
        context = self._paper(paper_id)
        run = context['store'].get_run(run_id) if run_id else context['store'].current(paper_id)
        if not run or run['paper_id'] != paper_id or not run['report']:
            raise CheckError('not_checked', '尚无已完成检查数据', 404)
        return context['service_state'].overlay(deepcopy(run['report']))

    def _fresh(self, context, report):
        configuration = self._configuration(context)
        if report['input'].get('configuration_hash') != identity_hash(configuration):
            return False
        knowledge = self._knowledge(context, configuration).read()
        if report['input'].get('knowledge_hash') != identity_hash(knowledge['registry']):
            return False
        root = Path(context['paper']['root'])
        try:
            from ..contract import ComparisonSource
            from ..sources import ProjectSource
            from .snapshot import _raw_files
            source = ProjectSource('new', root, context['paper']['entry'],
                                   ComparisonSource('directory', str(root)), configuration=configuration)
            # Re-expansion is cheap compared with parsing, and discovers formerly
            # missing includes/bibliographies which appeared since the last run.
            raw = _raw_files(source.expand())
            return {name: sha256(value).hexdigest() for name, value in raw.items()} == report['input']['files']
        except (OSError, ValueError, SourceError):
            return False

    def status(self, paper_id):
        context = self._paper(paper_id)
        try:
            report = self.report(paper_id)
        except CheckError:
            report = None
        tasks = list(context['tasks'].values())
        latest = tasks[-1] if tasks else None
        persisted = context['store'].current(paper_id) if report else None
        current_run = self._public_task(latest) if latest else (
            {'run_id': persisted['run_id'], 'paper_id': paper_id, 'state': persisted['status'],
             'input_id': persisted['input_id'], 'error': persisted['error']} if persisted else None)
        stale = bool(report and not self._fresh(context, report))
        return {'available': True, 'read_only': False, 'paper_id': paper_id,
                'configuration_hash': identity_hash(self._configuration(context)),
                'capabilities': {'run': True, 'cancel': True, 'knowledge': True,
                                 'decisions': True, 'annotation_conversion': True, 'semantic_import': True, 'export': True},
                'state': latest['state'] if latest else ('stale' if stale else report['status'] if report else 'not_run'),
                'current_input_id': report['input_id'] if report and not stale else None,
                'current_run': current_run,
                'stale': stale, 'coverage': report['coverage'] if report else [],
                'summary': report.get('summary') if report else None,
                'message': '规则检查不会自动调用 AI。'}

    def evidence(self, paper_id, *, cursor=0, limit=50):
        context = self._paper(paper_id)
        report = self.report(paper_id)
        document = context.get('document')
        if not document or not self._fresh(context, report) or document.snapshot.content_id != report['input']['content_id']:
            raise CheckError('evidence_unavailable', '请先运行当前论文检查以加载可靠证据', 409, action='run_checks')
        if not 1 <= limit <= 100 or cursor < 0:
            raise CheckError('invalid_pagination', '证据 limit 为 1–100，cursor 为非负整数')
        units = document.units
        end = cursor + limit
        return {'input_id': report['input_id'], 'content_id': report['input']['content_id'],
                'units': units[cursor:end], 'total': len(units),
                'next_cursor': str(end) if end < len(units) else None, 'truncated': end < len(units)}

    def _validate_registry_evidence(self, context, paper_id, registry, *, source_only=False):
        from .registry import validate_registry
        validate_registry(registry)
        selected = []
        for collection in ('concepts', 'symbol_bindings', 'occurrences', 'relations', 'exceptions'):
            for record in registry[collection]:
                if record.get('status', 'active' if collection == 'exceptions' else 'confirmed') not in ('active', 'confirmed'):
                    continue
                evidence = record.get('evidence', record.get('definitions', []))
                if not evidence and collection != 'concepts':
                    raise CheckError('evidence_required', '启用知识事实需要当前精确源码证据', 409)
                selected.extend(evidence)
        self._validate_evidence_by_entry(context, paper_id, selected, source_only=source_only)

    def _validate_evidence_by_entry(self, context, paper_id, evidence, *, source_only=False):
        from .finding_review import validate_evidence
        from ..identity import paper_identity
        grouped = {}
        def collect(items):
            for item in items:
                if not isinstance(item, dict):
                    raise CheckError('invalid_evidence', '证据片段无效', 409)
                if 'evidence' in item and 'file' not in item:
                    collect(item['evidence'])
                else:
                    entry = item.get('entry')
                    if not isinstance(entry, str):
                        raise CheckError('invalid_evidence', '证据入口无效', 409)
                    grouped.setdefault(entry, []).append(item)
        collect(evidence)
        root = Path(context['paper']['root'])
        current_entry = context['paper']['entry']
        for entry, fragments in grouped.items():
            if entry == current_entry:
                report = self.report(paper_id)
                if source_only:
                    # Recovery/undo intentionally changes knowledge (possibly
                    # corrupt), but source/config must still match the capture.
                    self.validate_page(paper_id, report['input'])
                elif not self._fresh(context, report):
                    raise CheckError('stale_input', '请先检查当前论文，再启用知识证据', 409)
                validate_evidence(context, report, fragments)
                continue
            try:
                identity = paper_identity(root, root / entry, require_entry=True)
            except (OSError, ValueError) as exc:
                raise CheckError('invalid_evidence', '证据入口必须位于同一论文根目录', 409) from exc
            if identity['entry'] != entry:
                raise CheckError('invalid_evidence', '证据入口必须使用规范相对路径', 409)
            other = self.paper_lookup(identity['paper_id'])
            if not other or any(other.get(key) != identity[key] for key in ('paper_id', 'root', 'entry')):
                # No registered recipe exists for this entry. Its verifiable
                # defaults/profile are authoritative, never another entry's
                # explicit overrides or already-merged effective settings.
                other = dict(identity)
            configuration = self._configuration_for(other)
            snapshot = capture_snapshot(entry, root=root, configuration=configuration, include_title=True)
            document = build_check_document(snapshot)
            proof_context = {'paper': other, 'document': document}
            validate_evidence(proof_context, {'input': snapshot.identity}, fragments)

    @staticmethod
    def _expected_source(store, body):
        if body.get('expected_source') != store.source:
            raise CheckError('knowledge_source_conflict', '知识来源已改变，请刷新并确认目标来源', 409, current_source=store.source)

    def knowledge(self, paper_id, body=None, *, source=None):
        with self.lock:
            return self._knowledge_operation(paper_id, body, source=source)

    def _knowledge_operation(self, paper_id, body=None, *, source=None):
        context = self._paper(paper_id)
        if source is not None:
            if source not in ('auto', 'project', 'external'):
                raise CheckError('invalid_knowledge_source', '知识来源必须为 auto/project/external')
            with self.lock:
                self.knowledge_sources[context['paper']['root']] = source
        store = self._knowledge(context)
        if body is None:
            return store.read()
        operation = body.get('operation', 'write')
        conditions = {'expected_revision': body.get('expected_revision'),
                      'expected_hash': body.get('expected_hash', body.get('expected_content_hash'))}
        if operation in ('import_preview', 'preview_import'):
            return store.preview_import(body['registry'])
        self._expected_source(store, body)
        if operation == 'write':
            self._validate_registry_evidence(context, paper_id, body['registry'])
        if operation == 'import':
            result = store.import_registry(body['registry'], resolutions=body.get('resolutions'), **conditions)
        elif operation == 'recover':
            result = store.recover(preflight=lambda registry: self._validate_registry_evidence(
                context, paper_id, registry, source_only=True), **conditions)
        elif operation == 'undo':
            result = store.undo_decision(body['decision_id'], reason=body.get('reason', 'Author undid decision'),
                                         author=body.get('author', 'author'), preflight=lambda registry: self._validate_registry_evidence(
                                             context, paper_id, registry, source_only=True), **conditions)
        elif operation == 'write':
            result = store.write(body['registry'], **conditions)
        else:
            raise CheckError('invalid_operation', '不支持的知识操作')
        with self.lock:
            for value in self.papers.values():
                if value['paper']['root'] == context['paper']['root']:
                    value['generation'] += 1
        return result

    def review_finding(self, paper_id, finding_key, body):
        from .finding_review import decide
        return decide(self, paper_id, finding_key, body)

    def annotate_finding(self, paper_id, finding_key, body, *, page_snapshot):
        from .annotation_link import convert
        return convert(self, paper_id, finding_key, body, page_snapshot=page_snapshot)

    def decide(self, paper_id, body):
        with self.lock:
            return self._decide_operation(paper_id, body)

    def _decide_operation(self, paper_id, body):
        required = {'collection', 'id', 'changes', 'action', 'reason', 'author', 'evidence_fingerprint',
                    'scope', 'expected_revision', 'expected_hash', 'request_id', 'input_id', 'expected_source'}
        if not required <= set(body) or body.get('collection') not in ('concepts', 'symbol_bindings', 'occurrences', 'relations', 'exceptions'):
            raise CheckError('invalid_decision', '知识决定缺少字段或集合无效')
        if body['changes'] is not None and not isinstance(body['changes'], dict):
            raise CheckError('invalid_decision', '知识变更必须为对象或 null')
        context = self._paper(paper_id)
        store = self._knowledge(context)
        self._expected_source(store, body)
        existing = store.read()
        replay = next((d for d in existing['registry']['decisions']
                       if body.get('request_id') and d.get('request_id') == body['request_id']), None)
        if replay:
            fields = ('action', 'reason', 'author', 'evidence_fingerprint', 'scope', 'input_id', 'collection')
            same_change = (replay.get('after') is None if body.get('changes') is None else
                           replay.get('after') == {**(replay.get('before') or {}),
                                                   **body['changes'], 'id': body.get('id')})
            if (all(replay.get(field) == body.get(field) for field in fields)
                    and replay.get('object_id') == body.get('id') and same_change):
                return existing
            raise CheckError('idempotency_conflict', '请求 ID 已用于不同决定', 409)
        report = self.report(paper_id)
        if body.get('input_id') != report['input_id'] or not self._fresh(context, report):
            raise CheckError('stale_input', '证据已过期，请重新检查', 409,
                             current_input_id=report['input_id'], action='refresh')
        if not isinstance(body.get('request_id'), str) or not body['request_id']:
            raise CheckError('request_id_required', '决定需要幂等请求 ID', field='/request_id')
        store = self._knowledge(context)
        allowed = {'collection', 'id', 'changes', 'action', 'reason', 'author', 'evidence_fingerprint',
                   'scope', 'expected_revision', 'expected_hash', 'request_id', 'input_id', 'expected_source'}
        if set(body) - allowed:
            raise CheckError('unknown_field', '决定包含未知字段')
        changes = body.get('changes')
        if changes is not None:
            before = next((r for r in existing['registry'].get(body.get('collection'), []) if r['id'] == body.get('id')), {})
            effective = {**before, **changes}
            evidence = effective.get('evidence', effective.get('definitions', []))
            if not evidence and body.get('collection') != 'concepts' and body.get('action') in ('confirm', 'waive'):
                raise CheckError('evidence_required', '确认此对象需要当前精确源码证据', 409)
            from .finding_review import fingerprint
            self._validate_evidence_by_entry(context, paper_id, evidence)
            if body.get('evidence_fingerprint') not in (fingerprint(evidence), identity_hash(evidence)):
                raise CheckError('evidence_conflict', '决定的证据指纹不符', 409)
        values = dict(body)
        values.pop('expected_source')
        collection, object_id, changes = values.pop('collection'), values.pop('id'), values.pop('changes')
        result = store.decide(collection, object_id, changes, **values)
        with self.lock:
            context['generation'] += 1
        return result

    def close(self):
        self.semantic.close()
        with self.lock:
            self.closed = True
            for context in self.papers.values():
                for task in context['tasks'].values():
                    task['cancel'].set()
                context['executor'].shutdown(wait=False, cancel_futures=True)
