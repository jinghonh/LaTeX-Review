"""Translation refresh failures and requests crossing a report generation."""
import json
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from latex_review.live import refresh_translation
from latex_review.serve import create_server
from latex_review.translation import TranslationError
from test_live_translation import paper
from test_translation import provider, _wait


@pytest.fixture
def session_server(tmp_path, provider):
    source, output, _ = paper(tmp_path)
    server = create_server(output, translation_config=provider[0], cache_directory=tmp_path / 'cache')
    try:
        yield server, output
    finally:
        provider[3].set()
        server.server_close()


def test_failed_refresh_pauses_then_next_refresh_recovers(session_server):
    server, output = session_server
    session = server.translation
    previous = session.manager
    manifest = output / 'translation-units.json'
    original = manifest.read_text()
    manifest.write_text('{broken')
    refresh_translation(server, output)
    assert previous.closed.is_set()
    status = session.snapshot()
    assert not status['available'] and not status['results']
    assert '刷新失败' in status['message']
    with pytest.raises(TranslationError, match='刷新失败'):
        session.submit([])
    # A status query does not retry. Only the next report refresh does.
    manifest.write_text(original)
    assert not session.snapshot()['available']
    refresh_translation(server, output)
    assert session.snapshot()['available']
    assert session.manager is not previous


def test_refresh_checks_manifest_rules_and_symlinks(session_server):
    server, output = session_server
    session = server.translation
    manifest = output / 'translation-units.json'
    units = json.loads(manifest.read_text())
    units[0]['rule_version'] = 'obsolete'
    manifest.write_text(json.dumps(units))
    assert '保护规则' in session.refresh()
    manifest.rename(output / 'other.json')
    manifest.symlink_to(output / 'other.json')
    assert '符号链接' in session.refresh()
    assert not session.snapshot()['available']


def test_http_request_during_refresh_is_rejected_without_waiting(session_server, monkeypatch):
    server, _ = session_server
    session = server.translation
    entered, release = Event(), Event()
    load = session._load
    def paused_load():
        entered.set()
        assert release.wait(5)
        return load()
    monkeypatch.setattr(session, '_load', paused_load)
    serving = Thread(target=server.serve_forever)
    refreshing = Thread(target=session.refresh)
    serving.start()
    refreshing.start()
    try:
        assert entered.wait(5)
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + '/api/translation/status', timeout=2) as response:
            state = json.load(response)
        assert not state['available'] and '刷新' in state['message']
        request = Request(base + '/api/translation/start', data=b'{"units": []}',
            headers={'Content-Type': 'application/json', 'X-LaTeX-Review-Token': server.translation_token})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=2)
        assert error.value.code == 400
        assert '稍后重试' in json.load(error.value)['message']
    finally:
        release.set()
        refreshing.join(5)
        server.shutdown()
        serving.join(5)
    assert session.snapshot()['available']


def test_close_during_refresh_cannot_resurrect_session(session_server, monkeypatch):
    server, _ = session_server
    session = server.translation
    entered, release = Event(), Event()
    load = session._load
    replacements = []
    def paused_load():
        entered.set()
        assert release.wait(5)
        result = load()
        replacements.append(result)
        return result
    monkeypatch.setattr(session, '_load', paused_load)
    thread = Thread(target=session.refresh)
    thread.start()
    try:
        assert entered.wait(5)
        session.close()
    finally:
        release.set()
        thread.join(5)
    assert replacements[0].closed.is_set()
    assert not session.snapshot()['available']
    with pytest.raises(TranslationError, match='关闭'):
        session.submit([])


def test_refresh_cancels_running_task_without_saving_late_result(session_server, provider, monkeypatch):
    server, _ = session_server
    session = server.translation
    _, calls, _, gate = provider
    from latex_review import translation
    finished = Event()
    request_translation = translation.request_translation
    def tracked_request(*args, **kwargs):
        try:
            return request_translation(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(translation, 'request_translation', tracked_request)
    previous = session.manager
    unit_id = next(iter(session.units))
    key = session.keys[unit_id]
    gate.clear()
    session.submit([unit_id])
    _wait(lambda: bool(calls))
    assert not session.refresh()
    assert previous.closed.is_set()
    gate.set()
    assert finished.wait(5)
    _wait(lambda: previous.jobs[key].done())
    assert not session.snapshot()['results']
    assert not (session.directory / (key + '.json')).exists()


def test_annotation_rebuild_refreshes_same_session_and_reports_failure(session_server, monkeypatch):
    server, output = session_server
    session = server.translation
    old_manager = session.manager
    monkeypatch.setattr('latex_review.annotations.execute_request',
        lambda *args: {'result': {'source_changed': True}})
    monkeypatch.setattr('latex_review.report_rebuild.rebuild_report',
        lambda *args: {'status': 'updated'})
    manifest = output / 'translation-units.json'
    original = manifest.read_text()
    manifest.write_text('{broken')
    serving = Thread(target=server.serve_forever)
    serving.start()
    def update():
        request = Request(f'http://127.0.0.1:{server.server_port}/api/annotations/update', data=b'{}',
            headers={'Content-Type': 'application/json', 'X-LaTeX-Review-Token': server.annotation_token})
        with urlopen(request, timeout=2) as response:
            return json.load(response)['result']['rebuild']
    try:
        assert '刷新失败' in update()['translation_warning']
        assert old_manager.closed.is_set()
        assert not session.snapshot()['available']
        manifest.write_text(original)
        assert 'translation_warning' not in update()
        assert server.translation is session
        assert session.snapshot()['available']
    finally:
        server.shutdown()
        serving.join(5)
