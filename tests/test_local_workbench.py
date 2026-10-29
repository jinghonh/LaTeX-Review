"""Workbench contracts across service restarts and failed replacements."""
from pathlib import Path
import logging
from urllib.request import urlopen

import pytest

from latex_review import local_client
from latex_review.local_protocol import LocalError
from latex_review.local_state import lock_busy, read_json, write_json
from latex_review.local_paths import state_directory
from latex_review.local_service import Manager
from latex_review.report_rebuild import rebuild_lock
from test_local_core import diff_request, paper, wait_until


def stopped(paper_id):
    result = local_client.request(f'/api/v1/papers/{paper_id}/diff/stop', {})
    wait_until(lambda: local_client.request('/api/v1/operations/' + result['operation_id'])['state'] == 'ready')


def test_metadata_history_and_readonly_report_survive_restart(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    result = local_client.ensure(request)
    paper_id = request['paper_id']
    metadata = f'/api/v1/papers/{paper_id}/metadata'
    local_client.request(metadata, {'alias': '测试论文', 'pinned': True})
    with pytest.raises(LocalError, match='先停止'):
        local_client.request(metadata, {'archived': True})
    local_client.ensure(request)
    rows = local_client.request('/api/v1/papers')['papers']
    assert rows[0]['alias'] == '测试论文' and rows[0]['pinned']
    snapshots = local_client.request('/api/v1/history/reports')['reports']
    assert len(snapshots) == 1  # Restarting an unchanged report is not a revision.
    endpoint = local_client.endpoint()
    snapshot = snapshots[0]
    with urlopen(f"http://127.0.0.1:{endpoint['port']}{snapshot['url']}") as response:
        assert b'First report' in response.read()
        policy = response.headers['Content-Security-Policy']
        assert 'sandbox allow-scripts;' in policy
        assert "connect-src 'none'" in policy
        assert 'allow-same-origin' not in policy
    stopped(paper_id)
    local_client.request(metadata, {'archived': True})
    with pytest.raises(LocalError, match='归档'):
        local_client.ensure(request)
    local_client.stop()
    local_client.start()
    row = local_client.request('/api/v1/papers')['papers'][0]
    assert row['archived'] and row['pinned'] and row['alias'] == '测试论文'
    operations = local_client.request('/api/v1/history/operations')['operations']
    assert any(o['operation_id'] == result['operation_id'] for o in operations)
    assert all('request' not in o for o in operations)
    assert len(local_client.request('/api/v1/history/reports')['reports']) == 1
    local_client.request(metadata, {'archived': False})
    restored = local_client.ensure(request)
    assert restored['state'] == 'ready'


def test_retry_failed_parameters_is_distinct_from_restore(tmp_path):
    root, entry = paper(tmp_path)
    first = diff_request(root, entry, tmp_path / 'first', tmp_path / 'missing.toml')
    local_client.ensure(first)
    second = diff_request(root, entry, tmp_path / 'second', tmp_path / 'new-config.toml')
    (Path(second['directory']) / 'report.html').write_text('<h1>Replacement report</h1>')
    second['config_explicit'] = True
    with pytest.raises(LocalError):
        local_client.ensure(second)
    operations = local_client.request('/api/v1/history/operations')['operations']
    failed = next(o for o in operations if o['state'] == 'failed')
    Path(second['config']).write_text('')
    retried = local_client.request(f"/api/v1/operations/{failed['operation_id']}/retry", {})
    wait_until(lambda: local_client.request('/api/v1/operations/' + retried['operation_id'])['state'] == 'ready')
    op = local_client.request('/api/v1/operations/' + retried['operation_id'])
    assert op['retry_of'] == failed['operation_id']
    snapshots = local_client.request('/api/v1/history/reports')['reports']
    assert len(snapshots) == 2
    latest = snapshots[0]
    endpoint = local_client.endpoint()
    with urlopen(f"http://127.0.0.1:{endpoint['port']}{latest['url']}") as response:
        assert b'Replacement report' in response.read()


def test_history_capture_failure_does_not_break_current_service(tmp_path, monkeypatch):
    import logging
    from latex_review.local_service import Manager
    from latex_review.local_history import History
    from latex_review.local_history_events import capture_managed_report, marker_path

    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    state = tmp_path / 'state'
    state.mkdir()
    manager = Manager(state, 8765, logging.getLogger('test'))
    try:
        manager.register(request)
        original = History.capture
        def fail(*args, **kwargs):
            raise OSError('disk full')
        monkeypatch.setattr(History, 'capture', fail)
        result = manager.capture_snapshot(request['paper_id'], request=request)
        assert result['error'] == 'disk full'
        assert manager.report_history()['errors'][0]['message'] == 'disk full'
        monkeypatch.setattr(History, 'capture', original)
        result = manager.capture_snapshot(request['paper_id'], request=request)
        assert result['report_id']
        assert manager.report_history()['errors'] == []
        (Path(request['directory']) / 'report.html').write_text('<h1>Changed body</h1>')
        assert marker_path(request['directory']).is_file()
        capture_managed_report(request['directory'])
        assert len(manager.history.reports()) == 2
        assert manager.history.reports()[0]['reason'] == '正文重建'
    finally:
        manager.close()


def test_history_database_failure_keeps_slot_thread_alive_and_retries_write(tmp_path, monkeypatch):
    import logging
    import sqlite3
    from latex_review.local_service import Manager, Slot

    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    state = tmp_path / 'state'
    state.mkdir()
    manager = Manager(state, 8765, logging.getLogger('test'))
    try:
        manager.register(request)
        slot = Slot(manager, request['paper_id'], 'diff', None)
        manager.slots[(request['paper_id'], 'diff')] = slot
        save = manager.history.save_operation
        def fail(*args):
            raise sqlite3.OperationalError('disk full')
        monkeypatch.setattr(manager.history, 'save_operation', fail)
        operation = slot.stop_request('first')
        wait_until(lambda: operation['state'] == 'ready' and slot.current is None)
        assert slot.thread.is_alive()
        history = manager.operation_history()
        assert 'disk full' in history['warning']
        assert history['operations'][0]['state'] == 'ready'
        monkeypatch.setattr(manager.history, 'save_operation', save)
        assert manager.operation_history()['warning'] == ''
        assert manager.history.get_operation(operation['operation_id'])['state'] == 'ready'
    finally:
        manager.close()


def test_first_configuration_failure_is_visible_and_retryable(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    request['config_explicit'] = True
    with pytest.raises(LocalError):
        local_client.ensure(request)
    rows = local_client.request('/api/v1/papers')['papers']
    assert rows[0]['paper_id'] == request['paper_id']
    failed = local_client.request('/api/v1/history/operations')['operations'][0]
    assert failed['state'] == 'failed'
    Path(request['config']).write_text('')
    retried = local_client.request(f"/api/v1/operations/{failed['operation_id']}/retry", {})
    wait_until(lambda: local_client.request('/api/v1/operations/' + retried['operation_id'])['state'] == 'ready')


def test_deleted_snapshot_is_not_reimported_on_restart(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    local_client.ensure(request)
    snapshot = local_client.request('/api/v1/history/reports')['reports'][0]
    local_client.request('/api/v1/history/reports/' + snapshot['report_id'] + '/delete', {})
    stopped(request['paper_id'])
    local_client.ensure(request)
    assert local_client.request('/api/v1/history/reports')['reports'] == []
    local_client.stop()
    local_client.start()
    assert local_client.request('/api/v1/history/reports')['reports'] == []


@pytest.fixture
def legacy_report(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'legacy-report', tmp_path / 'missing.toml')
    identity = {key: request[key] for key in ('paper_id', 'root', 'entry')}
    state = state_directory()
    write_json(state / 'registry.json', {'papers': {
        request['paper_id']: {**identity, 'slots': {'diff': request}}}})
    return request, state


def test_invalid_request_does_not_poison_stop_or_store_unknown_fields(legacy_report):
    request, state = legacy_report
    manager = Manager(state, 8765, logging.getLogger('test'))
    paper_id = request['paper_id']
    try:
        with pytest.raises(LocalError, match='config必须是绝对路径'):
            manager.ensure(paper_id, 'diff', {**request, 'config': {}})
        invalid = manager.history.operations()[0]
        assert invalid['request'] is None
        assert 'config必须是绝对路径' in invalid['message']
        with pytest.raises(LocalError, match='未知字段'):
            manager.ensure(paper_id, 'diff', {**request, 'unexpected': {'private_value': 'DO-NOT-STORE'}})
        records = manager.history.operations()
        assert all('DO-NOT-STORE' not in str(record) for record in records)
        assert all('unexpected' not in str(record) for record in records)
        stopped_operation = manager.slots[(paper_id, 'diff')].stop_request('stop-after-invalid')
        wait_until(lambda: manager.history.get_operation(stopped_operation['operation_id'])['state'] == 'ready')
        assert not manager.history_pending
        assert manager.operation_history()['warning'] == ''
        assert manager.slots[(paper_id, 'diff')].thread.is_alive()
    finally:
        manager.close()


def test_invalid_pending_record_is_quarantined_without_blocking_valid_writes(legacy_report):
    request, state = legacy_report
    manager = Manager(state, 8765, logging.getLogger('test'))
    try:
        manager.history_pending['malformed'] = {
            'operation_id': 'malformed', 'state': 'failed', 'request': {'config': {}}}
        operation = manager.slots[(request['paper_id'], 'diff')].stop_request('valid-stop')
        wait_until(lambda: manager.history.get_operation(operation['operation_id'])['state'] == 'ready')
        history = manager.operation_history()
        assert '已隔离' in history['warning']
        assert not manager.history_pending
        assert manager.history.get_operation('malformed') is None
        assert history['operations'][0]['operation_id'] == operation['operation_id']
    finally:
        manager.close()


def test_initial_import_does_not_block_health_list_or_stop(legacy_report, tmp_path):
    request, state = legacy_report
    paper_id = request['paper_id']
    # A distinct replacement can queue while the import waits for old content.
    replacement = diff_request(Path(request['root']), Path(request['root']) / request['entry'],
                               tmp_path / 'replacement', tmp_path / 'missing.toml')
    with rebuild_lock(request['directory']):
        local_client.start()
        assert local_client.request('/health', timeout=1)['protocol']
        wait_until(lambda: local_client.request('/api/v1/papers')['papers'][0].get('history_import') == 'importing')
        operation = local_client.request(f'/api/v1/papers/{paper_id}/diff/ensure', replacement)
        wait_until(lambda: local_client.request('/api/v1/operations/' + operation['operation_id'])['state'] == 'starting')
        stopped(paper_id)
        assert local_client.request('/api/v1/operations/' + operation['operation_id'])['state'] == 'cancelled'
        assert local_client.request('/api/v1/history/reports')['reports'] == []
        assert not read_json(state / 'registry.json')['papers'][paper_id].get('history_initialized')
    wait_until(lambda: len(local_client.request('/api/v1/history/reports')['reports']) == 1)


def test_interrupted_import_retries_on_next_start(legacy_report):
    request, state = legacy_report
    paper_id = request['paper_id']
    with rebuild_lock(request['directory']):
        local_client.start()
        wait_until(lambda: read_json(state / 'registry.json')['papers'][paper_id].get('history_import') == 'importing')
        local_client.stop()
        wait_until(lambda: not lock_busy(state / 'local.lock'), timeout=3)
        saved = read_json(state / 'registry.json')['papers'][paper_id]
        assert saved['history_import'] == 'pending'
        assert not saved.get('history_initialized')
    local_client.start()
    wait_until(lambda: read_json(state / 'registry.json')['papers'][paper_id].get('history_initialized'))
    assert len(local_client.request('/api/v1/history/reports')['reports']) == 1
    assert read_json(state / 'registry.json')['papers'][paper_id]['history_import'] == 'complete'


def test_failed_background_import_is_visible_and_manually_retryable(legacy_report, monkeypatch):
    from latex_review.local_history import History
    request, state = legacy_report
    manager = Manager(state, 8765, logging.getLogger('test'))
    paper_id = request['paper_id']
    try:
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):
                raise OSError('capture disk full')
            patch.setattr(History, 'capture', fail)
            manager.start_history_imports()
            manager.history_thread.join(timeout=3)
            assert not manager.history_thread.is_alive()
        saved = read_json(state / 'registry.json')['papers'][paper_id]
        assert saved['history_import'] == 'importfailed'
        assert not saved.get('history_initialized')
        assert manager.report_history()['errors'][0]['message'] == 'capture disk full'
        assert manager.capture_snapshot(paper_id)['report_id']
        assert manager.report_history()['errors'] == []
        assert read_json(state / 'registry.json')['papers'][paper_id]['history_import'] == 'complete'
    finally:
        manager.close()


def test_initial_import_finishes_before_replacement_capture(legacy_report, tmp_path):
    request, state = legacy_report
    paper_id = request['paper_id']
    replacement = diff_request(Path(request['root']), Path(request['root']) / request['entry'],
                               tmp_path / 'replacement', tmp_path / 'missing.toml')
    (Path(replacement['directory']) / 'report.html').write_text('<h1>New baseline</h1>')
    with rebuild_lock(request['directory']):
        local_client.start()
        wait_until(lambda: read_json(state / 'registry.json')['papers'][paper_id].get('history_import') == 'importing')
        operation = local_client.request(f'/api/v1/papers/{paper_id}/diff/ensure', replacement)
        wait_until(lambda: local_client.request('/api/v1/operations/' + operation['operation_id'])['state'] == 'starting')
    wait_until(lambda: local_client.request('/api/v1/operations/' + operation['operation_id'])['state'] == 'ready')
    reports = local_client.request('/api/v1/history/reports')['reports']
    assert len(reports) == 2
    endpoint = local_client.endpoint()
    for report, text in zip(reports, (b'New baseline', b'First report')):
        with urlopen(f"http://127.0.0.1:{endpoint['port']}{report['url']}") as response:
            assert text in response.read()
