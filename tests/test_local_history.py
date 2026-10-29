"""Durability and immutable snapshot contracts, independent of daemon startup."""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import pytest

from latex_review.local_history import History
from latex_review.local_protocol import LocalError


def report(directory, body='first'):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'report.html').write_text('<html><head></head><body>' + body + '</body></html>')
    return directory


def concurrent_save(args):
    state, directory, index = args
    history = History(Path(state))
    history.save_operation({'operation_id': str(index), 'state': 'ready'})
    return history.capture('paper', Path(directory), {}, 'initial')['report_id']


def test_operations_persist_prune_and_recover_only_explicitly(tmp_path):
    history = History(tmp_path)
    now = datetime.now(timezone.utc)
    for identifier, age in [('expired', 91), ('kept', 89)]:
        at = (now - timedelta(days=age)).isoformat()
        history.save_operation({'operation_id': identifier, 'state': 'ready', 'created_at': at, 'finished_at': at})
    request = {'config': '/paper/config.toml', 'cache': '/cache', 'api_key': 'do-not-store'}
    history.save_operation({'operation_id': 'pending', 'state': 'starting', 'request': request, 'token': 'secret'})
    restarted = History(tmp_path)
    assert restarted.get_operation('pending')['state'] == 'starting'
    assert restarted.get_operation('expired') is None
    assert [o['operation_id'] for o in restarted.operations()] == ['pending', 'kept']
    restarted.recover_operations()
    pending = restarted.get_operation('pending')
    assert pending['state'] == 'failed' and pending['interrupted']
    assert datetime.fromisoformat(pending['finished_at']).utcoffset() == timedelta(0)
    assert pending['request'] == {'config': '/paper/config.toml', 'cache': '/cache'}
    assert 'token' not in pending
    restarted.save_operation({'operation_id': 'pending', 'state': 'ready'})
    assert restarted.get_operation('pending')['request'] == pending['request']
    assert history.root.stat().st_mode & 0o777 == 0o700
    assert history.database.stat().st_mode & 0o777 == 0o600


def test_minimal_report_baselines_and_private_resource_allowlist(tmp_path):
    source = report(tmp_path / 'source')
    history = History(tmp_path / 'state')
    first = history.capture('paper', source, {}, 'initial_import')
    assert first['reason'] == 'initial_import' and first['baselines'] == {}
    assert first['url'] == f"/history/{first['report_id']}/report.html"
    baselines = {'old': {'identifier': 'abc', 'content_fingerprint': 'hash'}, 'new': {'kind': 'worktree'}}
    (source / 'run.json').write_text(json.dumps({'baselines': baselines}))
    (source / 'diff.json').write_text(json.dumps({'old': {'identifier': 'wrong'}}))
    second = history.capture('paper', source, {}, 'replacement')
    assert second['baselines'] == baselines
    for path in ['../report.html', '/report.html', '%2e%2e/report.html', 'assets/../../report.html',
                 'run.json', 'diff.json', 'request.json', '.rebuild/recipe.json', 'report.html?x', 'report.html#x',
                 'assets\\image.png', 'report.html\x00', 'https://example.com/report.html']:
        with pytest.raises(LocalError) as error:
            history.report_file(second['report_id'], path)
        assert error.value.status == 404
    with pytest.raises(LocalError):
        history.report_file('../source', 'report.html')


def test_legacy_translation_manifest_preserves_existing_report_translation(tmp_path):
    source = report(tmp_path / 'source', '<script type="application/json" id="translation-results">{"saved":"旧译文"}</script>')
    (source / 'translation-units.json').write_text('[{"rule_version":"obsolete"}]')
    config = tmp_path / 'config.toml'
    config.write_text('[translation]\nbase_url="http://127.0.0.1:1/v1"\nmodel="test"\n')
    history = History(tmp_path / 'state')
    snapshot = history.capture('paper', source, {'config': str(config), 'config_explicit': True,
                                               'cache': str(tmp_path / 'cache')}, 'initial')
    html = history.report_file(snapshot['report_id'], 'report.html').read_text()
    assert '旧译文' in html
    assert '翻译清单不兼容' in html and '未载入缓存译文' in html


def test_latest_dedupe_body_revisit_and_translation_only_changes(tmp_path, monkeypatch):
    source = report(tmp_path / 'source', 'A<script type="application/json" id="translation-results">{}</script>')
    history = History(tmp_path / 'state')
    first = history.capture('paper', source, {}, 'initial')
    original = history.report_file(first['report_id'], 'report.html').read_bytes()
    report(source, 'A<script type="application/json" id="translation-results">{"saved":"translation"}</script>')
    # Deduplication precedes reading live annotations and translations.
    with monkeypatch.context() as patch:
        patch.setattr('latex_review.local_history.publication_html', lambda *a, **k: pytest.fail('duplicate embedded again'))
        assert history.capture('paper', source, {}, 'initial_import') == first
    report(source, 'B')
    second = history.capture('paper', source, {}, 'body_rebuild')
    report(source, 'A<script type="application/json" id="translation-results">{}</script>')
    third = history.capture('paper', source, {}, 'body_rebuild')
    assert len({first['report_id'], second['report_id'], third['report_id']}) == 3
    assert history.report_file(first['report_id'], 'report.html').read_bytes() == original
    assert [r['report_id'] for r in history.reports()] == [third['report_id'], second['report_id'], first['report_id']]


def test_resource_copy_is_independent_streamed_and_detects_same_path_changes(tmp_path, monkeypatch):
    source = report(tmp_path / 'source', '<img src="assets/image.png">')
    (source / 'assets').mkdir()
    asset = source / 'assets/image.png'
    asset.write_bytes(b'original-image' * 100000)
    history = History(tmp_path / 'state')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', lambda self: pytest.fail('capture must stream resources'))
        first = history.capture('paper', source, {}, 'initial')
    asset.write_bytes(b'changed-image')
    second = history.capture('paper', source, {}, 'replacement')
    assert second['report_id'] != first['report_id']
    shutil.rmtree(source)
    assert history.report_file(first['report_id'], 'assets/image.png').read_bytes() == b'original-image' * 100000
    assert history.report_file(second['report_id'], 'assets/image.png').read_bytes() == b'changed-image'
    assert first['bytes'] == sum(p.stat().st_size for p in (history.snapshots / first['report_id']).rglob('*') if p.is_file())
    history.delete_report(first['report_id'])
    with pytest.raises(LocalError):
        history.report_file(first['report_id'], 'report.html')
    assert history.report_file(second['report_id'], 'report.html').is_file()


def test_prunes_only_after_durable_capture_and_keeps_other_papers(tmp_path, monkeypatch):
    source = report(tmp_path / 'source')
    history = History(tmp_path / 'state')
    other = history.capture('other', source, {}, 'initial')
    snapshots = []
    for index in range(20):
        report(source, str(index))
        snapshots.append(history.capture('paper', source, {}, 'replacement'))
    before = history.reports()
    report(source, 'next')
    with monkeypatch.context() as patch:
        patch.setattr('latex_review.local_history.os.fsync', lambda fd: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError, match='disk full'):
            history.capture('paper', source, {}, 'replacement')
    assert history.reports() == before
    assert history.report_file(snapshots[0]['report_id'], 'report.html').exists()
    assert not list(history.snapshots.glob('.capture-*'))
    newest = history.capture('paper', source, {}, 'replacement')
    assert len(history.reports()) == 21
    assert history.reports()[0] == newest
    with pytest.raises(LocalError):
        history.report_file(snapshots[0]['report_id'], 'report.html')
    assert history.report_file(other['report_id'], 'report.html').exists()


def test_cross_process_concurrent_capture_and_operations(tmp_path):
    source = report(tmp_path / 'source')
    state = tmp_path / 'state'
    with ProcessPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(concurrent_save, [(state, source, i) for i in range(8)]))
    history = History(state)
    assert len(set(ids)) == len(history.reports()) == 1
    assert len(history.operations()) == 8


def test_symlinks_cannot_escape_snapshot_allowlist(tmp_path):
    source = report(tmp_path / 'source', '<img src="assets/image.png">')
    (source / 'assets').mkdir()
    asset = source / 'assets/image.png'
    asset.symlink_to(source / 'report.html')
    history = History(tmp_path / 'state')
    with pytest.raises(ValueError):
        history.capture('paper', source, {}, 'initial')
    asset.unlink()
    asset.write_bytes(b'image')
    saved = history.capture('paper', source, {}, 'initial')
    target = history.report_file(saved['report_id'], 'assets/image.png')
    target.unlink()
    target.symlink_to(source / 'report.html')
    with pytest.raises(LocalError) as error:
        history.report_file(saved['report_id'], 'assets/image.png')
    assert error.value.status == 404


def test_deleted_latest_body_stays_deleted_across_restarts(tmp_path):
    source = report(tmp_path / 'source', 'A')
    history = History(tmp_path / 'state')
    first = history.capture('paper', source, {}, 'initial')
    report(source, 'B')
    second = history.capture('paper', source, {}, 'replacement')
    history.delete_report(second['report_id'])
    restarted = History(tmp_path / 'state')
    assert restarted.capture('paper', source, {}, 'initial_import') == {
        'paper_id': 'paper', 'state': 'ready', 'skipped': True}
    assert restarted.reports() == [first]
    report(source, 'A')
    revisited = restarted.capture('paper', source, {}, 'body_rebuild')
    assert revisited['state'] == 'ready' and revisited['report_id'] != first['report_id']
    assert len(restarted.reports()) == 2
    restarted.delete_report(revisited['report_id'])
    restarted.delete_report(first['report_id'])
    assert History(tmp_path / 'state').capture('paper', source, {}, 'initial_import')['skipped']
    assert restarted.reports() == []


def test_capture_retry_finishes_failed_retention_cleanup(tmp_path, monkeypatch):
    source = report(tmp_path / 'source')
    history = History(tmp_path / 'state')
    for index in range(20):
        report(source, str(index))
        history.capture('paper', source, {}, 'replacement')
    oldest = history.reports()[-1]
    original_remove = shutil.rmtree
    def fail_oldest(path, *args, **kwargs):
        if Path(path).name == oldest['report_id']:
            raise PermissionError('cleanup denied')
        return original_remove(path, *args, **kwargs)
    report(source, '21st')
    with monkeypatch.context() as patch:
        patch.setattr(shutil, 'rmtree', fail_oldest)
        with pytest.raises(PermissionError, match='cleanup denied'):
            history.capture('paper', source, {}, 'replacement')
    assert len(history.reports()) == 21
    current = history.reports()[0]
    assert history.capture('paper', source, {}, 'retry') == current
    assert len(history.reports()) == 20
    assert not (history.snapshots / oldest['report_id']).exists()


def test_failed_manual_delete_stays_visible_and_can_be_retried(tmp_path, monkeypatch):
    source = report(tmp_path / 'source')
    history = History(tmp_path / 'state')
    snapshot = history.capture('paper', source, {}, 'initial')
    with monkeypatch.context() as patch:
        patch.setattr(shutil, 'rmtree', lambda *a, **k: (_ for _ in ()).throw(PermissionError('denied')))
        with pytest.raises(PermissionError):
            history.delete_report(snapshot['report_id'])
    assert history.reports() == [snapshot]
    history.delete_report(snapshot['report_id'])
    assert history.reports() == []
    assert not (history.snapshots / snapshot['report_id']).exists()
