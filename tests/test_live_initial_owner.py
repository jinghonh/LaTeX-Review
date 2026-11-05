"""Cold live startup builds once in its final owner; replacement rollback stays two-phase."""
from hashlib import sha256
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from latex_review import cli, local_service
from latex_review.local_protocol import LocalError
from test_local_service_acceptance import (local, paper, live_args, stable_link,
                                           papers, eventually, fetch_report)


def slot(tmp_path, monkeypatch, *, existing=False, fail_builder=False, fail_worker=False):
    manager = SimpleNamespace(state=tmp_path / 'state', closed=Event(), port=31000, save=lambda: None)
    (manager.state / 'jobs').mkdir(parents=True)
    value = object.__new__(local_service.Slot)
    value.manager, value.paper_id, value.kind = manager, 'a' * 64, 'live'
    value.cancel, value.closing = Event(), False
    value.saved, value.fingerprint = None, None
    value.worker = value.builder = None
    value.state, value.error = 'stopped', ''
    value._collect_reports = lambda: None
    spawned, waited = [], []
    def process(recipe):
        obj = SimpleNamespace(recipe={**recipe, 'nonce': 'internal', 'bound_port': 32000}, stopped=False)
        obj.process = SimpleNamespace(pid=100 + len(spawned), poll=lambda: 0 if obj.stopped else None)
        def stop():
            obj.stopped = True
        obj.stop = stop
        return obj
    def spawn(recipe):
        obj = process(recipe)
        spawned.append(obj)
        return obj
    def wait(owned, *, build=False):
        waited.append((owned, build))
        if (build and fail_builder) or (not build and fail_worker):
            raise LocalError('controlled readiness failure', 503)
        return {'source_fingerprint': 'captured'} if build else {'port': 32000}
    value._spawn, value._wait_ready = spawn, wait
    monkeypatch.setattr(local_service, 'lock_busy', lambda *a: False)
    monkeypatch.setattr(cli, '_config', lambda *a: {})
    old = process({'kind': 'live'}) if existing else None
    value.worker = old
    request = {'kind': 'live', 'config': str(tmp_path / 'config.toml'), 'config_explicit': True,
               'root': str(tmp_path), 'output': str(tmp_path / 'report')}
    operation = {'request': request, 'fingerprint': 'new-fingerprint'}
    return value, operation, spawned, waited, old


def test_cold_slot_spawns_only_final_live_owner_and_ignores_caller_candidate(tmp_path, monkeypatch):
    value, operation, spawned, waited, _ = slot(tmp_path, monkeypatch)
    operation['request'].update(candidate='/untrusted/candidate', source_fingerprint='untrusted')
    value._ensure(operation)
    assert len(spawned) == 1 and spawned[0].recipe['kind'] == 'live'
    assert 'candidate' not in spawned[0].recipe and 'source_fingerprint' not in spawned[0].recipe
    assert waited == [(spawned[0], False)]
    assert value.builder is None and value.worker is spawned[0]
    assert value.state == operation['state'] == 'ready'
    assert operation['pid'] == spawned[0].process.pid
    assert value.saved == {key: val for key, val in operation['request'].items()
                           if key not in ('candidate', 'source_fingerprint')}
    assert 'candidate' not in value.saved and 'source_fingerprint' not in value.saved


def test_existing_live_replacement_keeps_preparer_candidate_and_stops_old_only_after_success(tmp_path, monkeypatch):
    value, operation, spawned, waited, old = slot(tmp_path, monkeypatch, existing=True)
    value._ensure(operation)
    assert [obj.recipe['kind'] for obj in spawned] == ['build-live', 'live']
    assert [build for obj, build in waited] == [True, False]
    assert spawned[0].stopped and old.stopped
    assert spawned[1].recipe['candidate'] == spawned[0].recipe['candidate']
    assert spawned[1].recipe['source_fingerprint'] == 'captured'
    assert value.state == 'ready' and value.worker is spawned[1]
    assert not Path(spawned[0].recipe['candidate']).exists()


def test_bad_replacement_preparation_preserves_existing_owner(tmp_path, monkeypatch):
    value, operation, spawned, waited, old = slot(tmp_path, monkeypatch, existing=True, fail_builder=True)
    with pytest.raises(LocalError, match='controlled'):
        value._ensure(operation)
    assert len(spawned) == 1 and spawned[0].recipe['kind'] == 'build-live'
    assert spawned[0].stopped and not old.stopped
    assert value.worker is old and value.builder is None
    assert operation.get('state') != 'ready'


def test_cold_owner_readiness_failure_stops_owned_process_without_claiming_ready(tmp_path, monkeypatch):
    value, operation, spawned, waited, old = slot(tmp_path, monkeypatch, fail_worker=True)
    with pytest.raises(LocalError, match='controlled'):
        value._ensure(operation)
    assert len(spawned) == 1 and spawned[0].stopped
    assert value.worker is None and value.saved is None
    assert operation.get('state') != 'ready'


@pytest.mark.parametrize('built_at,ready_at,passes', [(550, 1100, True), (650, 700, False), (550, 1200, False)])
def test_cold_owner_preserves_separate_build_and_server_budgets(tmp_path, monkeypatch, built_at, ready_at, passes):
    import io
    clock = SimpleNamespace(now=0)
    value, operation, spawned, waited, _ = slot(tmp_path, monkeypatch)
    value.cancel = SimpleNamespace(is_set=lambda: False, wait=lambda interval: setattr(clock, 'now', clock.now + 50))
    owned = SimpleNamespace(recipe={'kind': 'live', '_initial_build_in_worker': True,
                                    'nonce': 'owned', 'ready': 'owned-ready'},
                            process=SimpleNamespace(pid=101, poll=lambda: None))
    def ready(path):
        if clock.now < built_at:
            return None
        result = {'nonce': 'owned', 'pid': 101, 'initial_build_complete': True}
        if clock.now >= ready_at:
            result['port'] = 12345
        return result
    monkeypatch.setattr(local_service, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(local_service, 'read_json', ready)
    monkeypatch.setattr(local_service, 'urlopen', lambda *a, **k: io.BytesIO(b'{"nonce":"owned","pid":101}'))
    if passes:
        result = local_service.Slot._wait_ready(value, owned)
        assert result['port'] == owned.recipe['bound_port'] == 12345
        assert clock.now == ready_at
    else:
        with pytest.raises(LocalError, match='超时'):
            local_service.Slot._wait_ready(value, owned)
        assert clock.now == (600 if built_at > 600 else built_at + 600)


@pytest.mark.parametrize('forged', [{'nonce': 'other', 'pid': 101}, {'nonce': 'owned', 'pid': 999}])
def test_unowned_progress_cannot_extend_initial_build_deadline(tmp_path, monkeypatch, forged):
    clock = SimpleNamespace(now=0)
    value, operation, spawned, waited, _ = slot(tmp_path, monkeypatch)
    value.cancel = SimpleNamespace(is_set=lambda: False, wait=lambda interval: setattr(clock, 'now', clock.now + 50))
    owned = SimpleNamespace(recipe={'kind': 'live', '_initial_build_in_worker': True,
                                    'nonce': 'owned', 'ready': 'owned-ready'},
                            process=SimpleNamespace(pid=101, poll=lambda: None))
    monkeypatch.setattr(local_service, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(local_service, 'read_json', lambda path: {**forged, 'initial_build_complete': True})
    with pytest.raises(LocalError, match='超时'):
        local_service.Slot._wait_ready(value, owned)
    assert clock.now == 600


def test_initial_build_owner_cancel_discards_before_promotion_and_readiness(tmp_path, monkeypatch):
    from latex_review import live
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache-home'))
    root = tmp_path / 'paper'
    paper(root)
    config = tmp_path / 'writing.toml'
    config.write_text('[compile]\nenabled=false\n')
    output = tmp_path / 'preview'
    output.mkdir()
    (output / 'sentinel.txt').write_text('last good publication')
    stop = Event()
    recipe = {'root': str(root), 'entry': 'main.tex', 'output': str(output),
              'cache': str(tmp_path / 'cache'), 'config': str(config), 'config_explicit': True,
              'poll_files': True, 'nonce': 'owned', 'ready': str(tmp_path / 'ready.json'),
              '_initial_build_in_worker': True}
    def cancelled_build(source, directory):
        (directory / 'cancelled.txt').write_text('must never publish')
        stop.set()
        return []
    monkeypatch.setattr(live, 'build', cancelled_build)
    monkeypatch.setattr(live, 'create_server', lambda *a, **k: pytest.fail('cancelled build became ready'))
    assert live.run_worker(recipe, stop) == 0
    assert (output / 'sentinel.txt').read_text() == 'last good publication'
    assert not (output / 'cancelled.txt').exists() and not Path(recipe['ready']).exists()


def test_real_private_initial_publication_and_first_save_share_one_resident_worker(local):
    root, output = local.root / 'paper', local.root / 'preview'
    entry = paper(root, 'Initial owner text.')
    config = local.root / 'writing.toml'
    config.write_text('[compile]\nenabled=false\n[checks]\non_save=false\n')
    link = stable_link(local.cli('live', *live_args(entry, root, output, '--config', config)))
    record = papers(local)[0]
    first_pid = record['slots']['live']['pid']
    logs = list((local.state / 'logs').glob('*.log'))
    started = [line for log in logs for line in log.read_text().splitlines() if 'worker_started' in line]
    assert len(started) == 1 and 'kind=live ' in started[0]
    assert all('kind=build-live' not in line for line in started)
    initial = json.loads((output / 'checks-page.json').read_text())
    assert initial['files']['main.tex'] == sha256(entry.read_bytes()).hexdigest()
    with entry.open('a') as stream:
        stream.write('\n% first real save\n')
    expected = sha256(entry.read_bytes()).hexdigest()
    def updated():
        page = json.loads((output / 'checks-page.json').read_text())
        return page if page['files']['main.tex'] == expected else None
    current = eventually(updated, timeout=20)
    assert current['content_id'] != initial['content_id']
    assert papers(local)[0]['slots']['live']['pid'] == first_pid
    assert fetch_report(link)[0] == 200
    result = local.cli('local_service', 'stop-paper', record['paper_id'], '--kind', 'live')
    assert result.returncode == 0, result.stderr
