"""CLI adapters for host-retained semantic authority; never trust request files.

No daemon is launched. An available matching coordinator is used before a new
operation, but after submission errors never fall back to a duplicate local job.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from ..identity import paper_identity, identity_hash
from ..local_paths import state_directory
from .semantic import SemanticError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, _load, _json
from .semantic_service import SemanticService
from .store import CheckStore
from .snapshot import canonical_check_configuration


def remember_context(identity, configuration, store_path, *, config_path=None):
    """Keep private root/config location outside exported runs, in the retained DB."""
    store = CheckStore(store_path)
    with store.paper_lock(identity['paper_id']), sqlite3.connect(store.path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS semantic_cli_schema (version INTEGER NOT NULL)')
        row = db.execute('SELECT version FROM semantic_cli_schema').fetchone()
        if row and row[0] != 1:
            raise SemanticError('semantic_store_version', 'unsupported semantic CLI context version')
        if not row:
            db.execute('INSERT INTO semantic_cli_schema VALUES(1)')
        db.execute('CREATE TABLE IF NOT EXISTS semantic_context (paper_id TEXT PRIMARY KEY, root TEXT NOT NULL, entry TEXT NOT NULL, configuration TEXT NOT NULL, config_path TEXT)')
        db.execute('INSERT OR REPLACE INTO semantic_context VALUES(?,?,?,?,?)',
                   (identity['paper_id'], str(identity['root']), identity['entry'], _json(configuration),
                    str(Path(config_path).resolve()) if config_path else None))


def _paths(store=None):
    if store:
        path = Path(store)
        return [path] if path.is_file() else []
    return sorted((state_directory() / 'checks').glob('*/checks.sqlite3'))


def _locate_run(run_id, store):
    for path in _paths(store):
        run = CheckStore(path).get_run(run_id)
        if run:
            if not run['report']:
                raise SemanticError('run_not_ready', 'run has no frozen report')
            return path, run
    raise SemanticError('run_not_found', 'run not found in retained checks database')


def _context(path, pid, *, project=None, config=None, entry=None):
    with sqlite3.connect(path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_context'").fetchone()
        row = db.execute('SELECT root,entry,configuration,config_path FROM semantic_context WHERE paper_id=?', (pid,)).fetchone() if exists else None
    if row:
        root, entry, saved, config_path = row
        if project and Path(project).resolve() != Path(root).resolve():
            raise SemanticError('paper_identity', 'project differs from locally retained authority')
        return {'root': root, 'entry': entry, 'paper_id': pid}, Path(config or config_path) if (config or config_path) else None
    if project and entry:
        identity = paper_identity(Path(project).resolve(), entry, require_entry=True)
        if identity['paper_id'] != pid:
            raise SemanticError('paper_identity', 'explicit project differs from retained input')
        return identity, Path(config) if config else None
    raise SemanticError('context_unavailable', 'no private local run context; provide --project or run current checks again before exporting')


class _LocalHost:
    def __init__(self, identity, path, config_path=None):
        self.context = {'paper': identity, 'store': CheckStore(path)}
        self.config_path = config_path

    def _paper(self, pid):
        if pid != self.context['paper']['paper_id']:
            raise SemanticError('paper_identity', 'paper differs from retained context')
        return self.context

    def _configuration(self, context):
        from ..cli import _config
        from ..project_profile import settings
        from ..sources import ProjectSource
        from ..contract import ComparisonSource
        identity = context['paper']
        root = Path(identity['root'])
        raw = _config(self.config_path or root / '.latex-review.toml', self.config_path is not None)
        return canonical_check_configuration(settings(ProjectSource('new', root, identity['entry'], ComparisonSource('directory', str(root)), configuration=raw)))

    def _knowledge(self, context, configuration=None):
        from .registry import KnowledgeStore
        configuration = configuration or self._configuration(context)
        policy = configuration.get('knowledge', {})
        return KnowledgeStore(context['paper']['root'], state_directory(), source=policy.get('storage', 'auto'),
                              path=policy.get('path'), read_only=configuration.get('project', {}).get('read_only', False))


@contextmanager
def _service(identity, path, config_path=None):
    service = SemanticService(_LocalHost(identity, path, config_path))
    try:
        yield service
    finally:
        service.close()
        service.executor.shutdown(wait=True)


def _wait(service, pid, task):
    try:
        service.futures[(pid, task['request_id'])].result(timeout=300)
    except KeyboardInterrupt:
        service.cancel(pid, task['request_id'])
        return service.get(pid, task['request_id'])
    return service.get(pid, task['request_id'])


def _remote(pid, configuration, *, explicit_store=False):
    if explicit_store:
        return None
    from .. import local_client
    from ..local_protocol import LocalError
    try:
        local_client.endpoint()
        status = local_client.checks_request(pid, 'status')
    except LocalError as exc:
        if exc.status in {404, 503}:
            return None
        raise
    if status.get('configuration_hash') != identity_hash(canonical_check_configuration(configuration)):
        raise SemanticError('configuration_conflict', 'active coordinator configuration differs; no duplicate local semantic task started')
    return local_client


def _wait_remote(client, pid, task):
    deadline = time.monotonic() + 300
    while task['state'] in {'queued', 'running', 'importing', 'cancelling'}:
        if time.monotonic() >= deadline:
            raise SemanticError('task_pending', f"task remains pending; query retained request_id {task['request_id']}")
        try:
            time.sleep(0.1)
            task = client.checks_request(pid, 'semantic/tasks/' + task['request_id'])
        except KeyboardInterrupt:
            return client.checks_request(pid, 'semantic/tasks/' + task['request_id'] + '/cancel', {})
    return task


def export_run(run_id, *, store=None, output=None, task='concept_definition', project=None, config=None):
    path, run = _locate_run(run_id, store)
    pid = run['report']['input']['paper_id']
    identity, config_path = _context(path, pid, project=project, config=config, entry=run['report']['input']['entry'])
    verified = paper_identity(identity['root'], identity['entry'], require_entry=True)
    if verified['paper_id'] != pid:
        raise SemanticError('paper_identity', 'local project no longer matches retained run')
    host = _LocalHost(identity, path, config_path)
    configuration = host._configuration(host.context)
    client = _remote(pid, configuration, explicit_store=store is not None)
    body = {'task': task, 'mode': 'export', 'input_id': run['input_id']}
    if client:
        state = _wait_remote(client, pid, client.checks_request(pid, 'semantic/tasks', body))
        payload = client.checks_request(pid, 'semantic/tasks/' + state['request_id'] + '/export') if state['state'] == 'ready' else None
    else:
        with _service(identity, path, config_path) as service:
            state = _wait(service, pid, service.start(pid, body))
            payload = service.export(pid, state['request_id']) if state['state'] == 'ready' else None
    if payload is None:
        raise SemanticError('semantic_export_failed', _json(state.get('error') or {'state': state['state']}))
    if output:
        Path(output).write_text(_json(payload) + '\n', encoding='utf-8')
    return payload


def import_file(request_path, response_path, *, store=None, project=None, config=None):
    # Imported JSON is only a selector and exact equality check against host DB.
    supplied = _load(Path(request_path).read_bytes(), MAX_REQUEST_BYTES)
    if not isinstance(supplied, dict) or not isinstance(supplied.get('request_id'), str):
        raise SemanticError('unknown_request', 'request file does not select retained authority')
    request_id = supplied['request_id']
    response = _load(Path(response_path).read_bytes(), MAX_RESPONSE_BYTES)
    found = None
    for path in _paths(store):
        with sqlite3.connect(path) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_tasks'").fetchone()
            row = db.execute('SELECT authority_json FROM semantic_tasks WHERE request_id=?', (request_id,)).fetchone() if exists else None
        if row and row[0]:
            authority = SemanticService._restore(row[0])
            if _json(supplied) != _json(authority.payload):
                raise SemanticError('request_tampered', 'request file differs from host-retained authority')
            found = path, authority
            break
    if not found:
        raise SemanticError('unknown_request', 'no locally retained authority for this request file')
    path, authority = found
    pid = authority.payload['versions']['paper_id']
    identity, config_path = _context(path, pid, project=project, config=config, entry=authority.payload['versions']['entry'])
    host = _LocalHost(identity, path, config_path)
    client = _remote(pid, host._configuration(host.context), explicit_store=store is not None)
    body = {'request_id': request_id, 'response': response}
    if client:
        return _wait_remote(client, pid, client.checks_request(pid, 'semantic/import', body))
    with _service(identity, path, config_path) as service:
        return _wait(service, pid, service.import_response(pid, body))


def run_semantic(identity, configuration, store_path, *, config_path=None, expected_input_id=None, task='concept_definition', use_coordinator=True):
    remember_context(identity, configuration, store_path, config_path=config_path)
    pid = identity['paper_id']
    body = {'task': task, 'mode': 'call'}
    if expected_input_id:
        body['input_id'] = expected_input_id
    client = _remote(pid, configuration, explicit_store=not use_coordinator)
    if client:
        return _wait_remote(client, pid, client.checks_request(pid, 'semantic/tasks', body))
    with _service(identity, store_path, Path(config_path) if config_path else None) as service:
        return _wait(service, pid, service.start(pid, body))


def semantic_exit_code(result):
    status = result.get('state', result.get('status'))
    return 0 if status in {'completed', 'ready'} else 1 if status in {'failed', 'budget_exceeded'} else 2
