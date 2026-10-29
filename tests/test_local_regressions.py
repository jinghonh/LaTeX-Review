"""Regressions across the resident proxy, operation history and frozen reports."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import re
from threading import Event

import pytest
from playwright.sync_api import sync_playwright

from latex_review import local_client, local_service
from latex_review.delivery import _embed_results
from latex_review.live import prepare_request
from latex_review.local_history import History
from latex_review.local_protocol import LocalError
from latex_review.local_service import Manager, Slot
from latex_review.translation import TranslationConfig, RULE_VERSION, cache_key, rendered_result
from test_local_core import diff_request, paper, wait_until
from test_translation import _content_report


@pytest.mark.parametrize('kind', ['live', 'diff'])
def test_annotation_async_jobs_follow_resident_proxy(tmp_path, kind):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root, poll_files=True)
    if kind == 'diff':
        (tmp_path / 'comparison').mkdir()
        report = _content_report(tmp_path / 'comparison', 'Old result.', 'Current result.')
        from latex_review.local_reports import report_identity
        request.update(report_identity(report.directory), kind='diff', directory=str(report.directory), port=0)
        request.pop('output')
        request.pop('poll_files')
    result = local_client.ensure(request)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'), headless=True)
        try:
            page = browser.new_page()
            responses, errors = [], []
            page.on('response', lambda r: responses.append((r.url, r.status)) if '/api/annotations/' in r.url else None)
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('https://**', lambda route: route.abort())
            page.goto(result['url'])
            frame = page.frame_locator('iframe')
            frame.locator('#annotation-status').wait_for(state='attached')
            reader = page.frames[1]
            reader.wait_for_function('window.reviewAnnotationDeliveryReady === true')
            # Both explicit migrations and imports use the asynchronous adapter.
            assert reader.evaluate("reviewAnnotations.update({operation:'reconcile'})")
            assert reader.evaluate('''async () => reviewAnnotationDelivery.importBundle(
                await reviewAnnotationDelivery.backup())''')
            jobs = [(url, status) for url, status in responses if '/jobs/' in url]
            assert jobs and all('/worker/api/annotations/jobs/' in url and status == 200 for url, status in jobs)
            assert all(status < 400 for _, status in responses)
            assert not errors
        finally:
            browser.close()


def test_history_listing_survives_concurrent_registration(tmp_path, monkeypatch):
    root, entry = paper(tmp_path)
    first = prepare_request(entry, root=root)
    second_entry = root / 'second.tex'
    second_entry.write_text(entry.read_text())
    second = prepare_request(second_entry, root=root)
    state = tmp_path / 'state'
    state.mkdir()
    manager = Manager(state, 8765, logging.getLogger('test'))
    manager.register(first)
    read_json = local_service.read_json
    target = state / 'history-errors' / (first['paper_id'] + '.json')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            def register_during_read(path, *args, **kwargs):
                if path == target:
                    # A real registration thread runs while the history iterator
                    # is between items. A lock held over IO would deadlock here.
                    pool.submit(manager.register, second).result(timeout=2)
                    return {'message': 'Saved history warning'}
                return read_json(path, *args, **kwargs)
            monkeypatch.setattr(local_service, 'read_json', register_during_read)
            result = manager.report_history()
        assert result['errors'] == [{'paper_id': first['paper_id'], 'message': 'Saved history warning'}]
        assert second['paper_id'] in manager.papers
    finally:
        manager.close()


@pytest.mark.parametrize('busy_state', ['running', 'queued'])
@pytest.mark.parametrize('outcome', ['ready', 'failed', 'cancelled'])
def test_retries_keep_independent_records(tmp_path, monkeypatch, busy_state, outcome):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root)
    state = tmp_path / 'state'
    state.mkdir()
    manager = Manager(state, 8765, logging.getLogger('test'))
    manager.register(request)
    slot = Slot(manager, request['paper_id'], 'live', None)
    manager.slots[(request['paper_id'], 'live')] = slot
    entered, release = Event(), Event()

    def controlled_service(operation):
        entered.set()
        assert release.wait(5)
        if outcome != 'ready':
            raise LocalError('controlled service failure')
        operation['state'] = 'ready'

    monkeypatch.setattr(slot, '_ensure', controlled_service)
    originals = ['failed-first', 'failed-second']
    for identifier in originals:
        manager.history.save_operation({'operation_id': identifier, 'paper_id': request['paper_id'],
                                        'kind': 'live', 'action': 'ensure', 'state': 'failed', 'request': request})
    try:
        if busy_state == 'queued':
            slot.condition.acquire()
        try:
            initial = manager.ensure(request['paper_id'], 'live', request)
            if busy_state == 'running':
                assert entered.wait(2)
            retries = [manager.retry(identifier) for identifier in [*originals, originals[0]]]
            ids = [initial['operation_id'], *(r['operation_id'] for r in retries)]
            assert len(set(ids)) == 4
            # Ordinary identical requests still share work; only explicit retries
            # require distinct operation records.
            same = manager.ensure(request['paper_id'], 'live', request)
            assert same['operation_id'] == initial['operation_id']
            assert 'retry_of' not in manager.operations[initial['operation_id']]
            if outcome == 'cancelled':
                slot.stop_request('cancel-retries')
        finally:
            if busy_state == 'queued':
                slot.condition.release()
        release.set()
        wait_until(lambda: all(manager.history.get_operation(r['operation_id'])['state'] == outcome for r in retries))
        for retry, original in zip(retries, [*originals, originals[0]]):
            saved = manager.history.get_operation(retry['operation_id'])
            assert saved['retry_of'] == original
            assert saved['finished_at']
        assert all(manager.history.get_operation(identifier)['state'] == 'failed' for identifier in originals)
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize('kind', ['live', 'diff'])
def test_queued_retries_reuse_successful_worker(tmp_path, monkeypatch, kind):
    root, entry = paper(tmp_path)
    request = (prepare_request(entry, root=root, poll_files=True) if kind == 'live' else
               diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml'))
    state = tmp_path / 'state'
    state.mkdir()
    manager = Manager(state, 8765, logging.getLogger('test'))
    manager.register(request)
    slot = Slot(manager, request['paper_id'], kind, None)
    manager.slots[(request['paper_id'], kind)] = slot
    entered, release = Event(), Event()
    ensure = slot._ensure

    def delayed_ensure(operation):
        entered.set()
        assert release.wait(5)
        ensure(operation)

    monkeypatch.setattr(slot, '_ensure', delayed_ensure)
    manager.history.save_operation({'operation_id': 'failed', 'paper_id': request['paper_id'],
                                    'kind': kind, 'action': 'ensure', 'state': 'failed', 'request': request})
    try:
        first = manager.ensure(request['paper_id'], kind, request)
        assert entered.wait(2)
        retries = [manager.retry('failed'), manager.retry('failed')]
        release.set()
        ids = [first['operation_id'], *(r['operation_id'] for r in retries)]
        assert len(set(ids)) == 3
        wait_until(lambda: all(manager.history.get_operation(i)['state'] == 'ready' for i in ids), timeout=20)
        wait_until(lambda: slot.current is None)
        records = [manager.history.get_operation(i) for i in ids]
        assert len({(r['pid'], r['generation']) for r in records}) == 1
        assert all(r['retry_of'] == 'failed' for r in records[1:])
        assert not list((state / 'jobs').glob('diff-*'))
        if kind == 'diff':
            assert len(manager.history.reports()) == 1
    finally:
        release.set()
        manager.close()


@pytest.mark.parametrize('cache_mode', ['empty', 'partial'])
def test_snapshot_preserves_embedded_translations_with_incomplete_cache(tmp_path, cache_mode):
    report = _content_report(tmp_path, 'First old.\n\nSecond old.', 'First new.\n\nSecond new.')
    directory = report.directory
    units = [u for u in json.loads((directory / 'translation-units.json').read_text()) if not u.get('error')]
    assert len(units) >= 3
    embedded = {u['id']: rendered_result(u, ['已保存译文'] * len(u['sentences'])) for u in units[:-1]}
    html = directory / 'report.html'
    html.write_text(_embed_results(html.read_text(), embedded))
    original = html.read_bytes()
    config_file, cache = tmp_path / 'translation.toml', tmp_path / 'cache'
    config_file.write_text('[translation]\nbase_url="http://127.0.0.1:1/v1"\nmodel="snapshot-test"\n')
    cache.mkdir()
    config = TranslationConfig('http://127.0.0.1:1/v1', 'snapshot-test')
    expected = dict(embedded)
    if cache_mode == 'partial':
        # One cached translation replaces an embedded value; another is new.
        for unit in (units[0], units[-1]):
            key = cache_key(unit, config)
            (cache / (key + '.json')).write_text(json.dumps({'version': RULE_VERSION, 'key': key,
                'translation': {'sentences': [{'id': s['number'], 'text': '缓存译文'} for s in unit['sentences']]}}))
            expected[unit['id']] = rendered_result(unit, ['缓存译文'] * len(unit['sentences']))
    history = History(tmp_path / 'state')
    snapshot = history.capture('paper', directory, {'config': str(config_file), 'config_explicit': True,
                                                  'cache': str(cache)}, 'initial')
    captured = history.report_file(snapshot['report_id'], 'report.html').read_text()
    results = json.loads(re.search(r'<script[^>]*id="translation-results"[^>]*>(.*?)</script>', captured, re.S)[1])
    assert results == expected
    assert html.read_bytes() == original
