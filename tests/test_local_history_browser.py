"""Exercise frozen readers through the real local Handler and its opaque CSP."""
import base64
from importlib.resources import files
import json
import logging
import re
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from latex_review.annotations import AnnotationStore, load_content
from latex_review.delivery import _embed_results
from latex_review.local_history import History
from latex_review.local_service import EntryServer, Handler
from latex_review.translation import TranslationConfig, RULE_VERSION, cache_key, rendered_result
from test_annotations import AUTHOR, mutate, select
from test_translation import _browser_api, _launch_browser, _content_report


@pytest.fixture
def frozen_report(tmp_path):
    report = _content_report(tmp_path, r'\section{Results} Stable old result with $x$.',
                             r'\section{Results} Stable new result with $x$.')
    directory = report.directory
    content = load_content(directory)
    session = AnnotationStore.open(content)
    mutate(session, 'create', selection=select(content, 'Stable'), body='Frozen annotation $x$',
           author=AUTHOR, category='question')
    # Add a local image without a network dependency.
    (directory / 'assets').mkdir(exist_ok=True)
    image = directory / 'assets/pixel.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9X8AAAAASUVORK5CYII='))
    html = (directory / 'report.html').read_text()
    html = html.replace('</body>', '<img id="history-image" src="assets/pixel.png"></body>')
    # Simulate an older generated report with no snapshot guards.
    html = html.replace("const readonly = document.querySelector('meta[name=\"latex-review-readonly\"]')?.content === 'snapshot';", 'const readonly = false;')
    (directory / 'report.html').write_text(html)
    config_path, cache = tmp_path / 'translation.toml', tmp_path / 'explicit-cache'
    config_path.write_text('[translation]\nbase_url="http://127.0.0.1:1/v1"\nmodel="frozen-test"\n')
    cache.mkdir()
    config = TranslationConfig('http://127.0.0.1:1/v1', 'frozen-test')
    units = json.loads((directory / 'translation-units.json').read_text())
    for unit in units:
        if unit.get('error'):
            continue
        key = cache_key(unit, config)
        (cache / (key + '.json')).write_text(json.dumps({'version': RULE_VERSION, 'key': key,
            'translation': {'sentences': [{'id': s['number'], 'text': '冻结译文：' + s['text']} for s in unit['sentences']]}}))
    history = History(tmp_path / 'state')
    request = {'config': str(config_path), 'config_explicit': True, 'cache': str(cache)}
    snapshot = history.capture('paper', directory, request, 'initial')
    original_html = history.report_file(snapshot['report_id'], 'report.html').read_bytes()
    # Annotation-only and translation-only updates must not alter the revision.
    mutate(session, 'create', selection=select(content, 'Stable'), body='Later annotation', author=AUTHOR, category='note')
    for file in cache.iterdir():
        file.unlink()
    assert history.capture('paper', directory, request, 'initial_import') == snapshot
    assert history.report_file(snapshot['report_id'], 'report.html').read_bytes() == original_html
    return history, snapshot, session


def test_snapshot_opaque_csp_freezes_annotations_translations_and_keeps_reading(frozen_report):
    history, snapshot, session = frozen_report
    original = session.export()
    with EntryServer(('127.0.0.1', 0), Handler) as server:
        server.manager = SimpleNamespace(history=history, logger=logging.getLogger('history-test'))
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        api = _browser_api()
        try:
            with api.sync_playwright() as pw:
                browser = _launch_browser(pw, api)
                page = browser.new_page()
                errors, requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append((request.method, request.url)))
                page.route('https://**', lambda route: route.abort())
                page.add_init_script('''
                    window.historyStorageAccess=[];
                    for(const key of ['localStorage','sessionStorage','indexedDB']) {
                      Object.defineProperty(window,key,{get(){historyStorageAccess.push(key+new Error().stack);throw Error('unexpected storage access');}});
                    }
                ''')
                response = page.goto(f'http://127.0.0.1:{server.server_port}' + snapshot['url'])
                assert 'sandbox allow-scripts;' in response.headers['content-security-policy']
                assert 'allow-same-origin' not in response.headers['content-security-policy']
                api.expect(page.locator('#annotation-status')).to_contain_text('历史快照只读')
                assert page.evaluate('window.origin') == 'null'
                assert page.locator('.history-readonly-banner').text_content() == '历史快照 · 只读 · 批注和译文固定为保存时内容'
                threads = page.evaluate('reviewAnnotations.getSnapshot().threads')
                assert len(threads) == 1 and threads[0]['messages'][0]['body'] == 'Frozen annotation $x$'
                assert not page.evaluate("reviewAnnotations.update({operation:'resolve',thread_id:reviewAnnotations.getSnapshot().threads[0].id})")
                page.evaluate("reviewAnnotations.openComposer('create',null,{})")
                assert page.locator('.annotation-editor').is_hidden()
                assert page.locator('.annotation-card-actions').count() == 0
                assert page.locator('.annotation-edit').count() == 0
                assert page.locator('.change-review-editor').count() == 0
                assert page.locator('#review-state').is_disabled()
                assert page.locator('#review-import').is_disabled()
                assert page.locator('#translate-all').is_hidden()
                assert page.locator('.translation-block').count() > 0
                assert '冻结译文' in page.locator('body').text_content()
                for selector in ('.inline-translation', '.group-translation'):
                    populated = page.locator(selector).filter(has=page.locator('.translated-sentence'))
                    assert populated.count() > 0
                    assert populated.evaluate_all('(panels)=>panels.every(panel=>panel.open)')
                    sides = populated.locator('[data-translation-node]').filter(has=page.locator('.translated-sentence'))
                    assert sides.evaluate_all('(sides)=>sides.every(side=>side.querySelector(".translation-placeholder").hidden)')
                assert page.locator('[data-translate-change]:visible').count() == 0
                assert page.locator('#translation-cancel').is_hidden()
                assert page.locator('#translation-export').is_hidden()
                # Saved panels remain collapsible; reading-mode changes do not reopen them.
                panel = page.locator('.inline-translation').first
                panel.evaluate('(node)=>node.open=false')
                page.locator('#translation-mode').select_option('original')
                page.locator('#translation-mode').select_option('bilingual')
                assert not panel.evaluate('(node)=>node.open')
                page.locator('#reader-tools').evaluate('(node)=>node.open=true')
                page.locator('#report-search').fill('Stable')
                assert page.locator('#search-results li').count() > 0, (errors, page.locator('#search-status').text_content(), page.evaluate('historyStorageAccess'))
                page.locator('#report-search').fill('冻结译文')
                assert page.locator('#search-results li').count() > 0
                page.locator('#toggle-sections').click()
                assert page.locator('#section-tree').text_content()
                assert page.locator('#history-image').evaluate('(image)=>image.naturalWidth') == 1, requests
                # Formula source is retained even when the CDN is unavailable.
                assert page.locator('[data-annotation-latex]').count() > 0
                accesses = page.evaluate('historyStorageAccess')
                assert all(access.startswith('localStorage') and
                           'https://cdn.jsdelivr.net/npm/mathjax@3/' in access and
                           'mergeUserSettings' in access for access in accesses), accesses
                assert '无法恢复浏览器记录' not in page.locator('#review-storage-status').text_content()
                assert not errors
                assert not any(method != 'GET' or '/api/' in url or 'live-status.json' in url for method, url in requests)
                browser.close()
        finally:
            server.shutdown()
            worker.join()
    assert session.export() == original


@pytest.mark.parametrize('manifest,unaligned,prior_reader', [
    ('{}', False, False), ('null', False, False), ('{', False, False),
    ('[null]', False, False), ('{}', True, False),
    pytest.param('{}', False, True, id='stored-legacy-reader'),
])
def test_snapshot_incompatible_manifest_displays_saved_translations(tmp_path, manifest, unaligned, prior_reader):
    report = _content_report(tmp_path, r'\section{Results} Stable old result.',
                             r'\section{Results} Stable new result.')
    directory = report.directory
    units = json.loads((directory / 'translation-units.json').read_text())
    results = {unit['id']: rendered_result(unit, ['保存译文' for _ in unit['sentences']])
               for unit in units if not unit.get('error')}
    assert results
    if unaligned:
        for result in results.values():
            for key in ('side', 'node_id', 'change_id'):
                del result[key]
    html = _embed_results((directory / 'report.html').read_text(), results)
    html, replaced = re.subn(r'(<script[^>]*id="translation-units"[^>]*>).*?(</script>)',
                             lambda match: match[1] + manifest + match[2], html, flags=re.S)
    assert replaced == 1
    (directory / 'report.html').write_text(html)
    (directory / 'translation-units.json').write_text(manifest)
    config = tmp_path / 'translation.toml'
    config.write_text('[translation]\nbase_url="http://127.0.0.1:1/v1"\nmodel="saved-test"\n')
    history = History(tmp_path / 'state')
    snapshot = history.capture('paper', directory, {'config': str(config), 'cache': str(tmp_path / 'cache')}, 'initial')
    snapshot_file = history.report_file(snapshot['report_id'], 'report.html')
    if prior_reader:
        # Test setup only: represent an already-saved reader with the original
        # non-array-manifest failure, after capture has finished upgrading scripts.
        saved_html = snapshot_file.read_text()
        translation_scripts = [match[0] for match in re.finditer(r'<script>(.*?)</script>', saved_html, re.S)
                               if "const manifest = document.getElementById('translation-units')" in match[1]]
        assert len(translation_scripts) == 1
        old_reader = """<script>(function () {
          'use strict';
          const manifest = document.getElementById('translation-units');
          const units = JSON.parse(manifest.textContent);
          const blockedStates = Object.fromEntries(units.filter(unit => unit.error)
            .map(unit => [unit.id, {state: 'blocked', message: unit.error}]));
        })();</script>"""
        snapshot_file.write_text(saved_html.replace(translation_scripts[0], old_reader, 1))
    saved_bytes = snapshot_file.read_bytes()
    saved_index = history.reports()
    with EntryServer(('127.0.0.1', 0), Handler) as server:
        server.manager = SimpleNamespace(history=history, logger=logging.getLogger('history-test'))
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        api = _browser_api()
        try:
            with api.sync_playwright() as pw:
                browser = _launch_browser(pw, api)
                try:
                    page = browser.new_page()
                    errors, requests = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: requests.append((request.method, request.url)))
                    page.route('https://**', lambda route: route.abort())
                    response = page.goto(f'http://127.0.0.1:{server.server_port}' + snapshot['url'])
                    if prior_reader:
                        served_bytes = response.body()
                        assert served_bytes != saved_bytes
                        current_reader = files('latex_review').joinpath('report_translation.js').read_bytes()
                        assert b'<script>' + current_reader + b'</script>' in served_bytes
                        # Only executable reader scripts can differ. Frozen JSON,
                        # body, banner, styles and all other markup remain byte-exact.
                        scripts = rb'<script>.*?</script>'
                        assert re.sub(scripts, b'<script></script>', served_bytes, flags=re.S) == re.sub(
                            scripts, b'<script></script>', saved_bytes, flags=re.S)
                        assert int(response.headers['content-length']) == len(served_bytes)
                    api.expect(page.locator('#translation-status')).to_contain_text(f'已保存 {len(results)} 段译文')
                    assert page.evaluate('window.origin') == 'null'
                    api.expect(page.locator('.delivery-warning')).to_contain_text('未载入缓存译文')
                    if unaligned:
                        saved = page.get_by_role('region', name='已保存译文（只读，未关联原文）')
                        api.expect(saved).to_be_visible()
                        api.expect(saved).to_contain_text('不提供原文跳转')
                        assert saved.locator('.translation-block').count() == len(results)
                        for sentence in saved.locator('.translated-sentence').all():
                            api.expect(sentence).to_be_visible()
                            api.expect(sentence).to_have_text('保存译文')
                            assert sentence.get_attribute('data-original-sentence') is None
                            assert 'sentence-changed' not in sentence.get_attribute('class')
                            sentence.click()
                    else:
                        assert page.locator('.history-saved-translations').count() == 0
                        page.locator('#layout-mode').select_option('compare')
                        for unit in units:
                            if unit['id'] not in results:
                                continue
                            block = page.locator(f'#{unit["id"]} > .translation-block')
                            api.expect(block).to_be_visible()
                            api.expect(block).to_contain_text('保存译文')
                        page.locator('#reader-tools').evaluate('(node)=>node.open=true')
                        page.locator('#report-search').fill('保存译文')
                        assert page.locator('#search-results li').count() > 0
                    assert page.locator('[data-translate-change]:visible').count() == 0
                    assert page.locator('#translate-all').is_hidden()
                    assert page.locator('#translation-cancel').is_hidden()
                    assert page.locator('#translation-export').is_hidden()
                    if prior_reader:
                        # Repeated reads neither accumulate banners nor rewrite the snapshot.
                        assert page.reload().body() == served_bytes
                        api.expect(page.locator('.history-readonly-banner')).to_have_count(1)
                        assert snapshot_file.read_bytes() == saved_bytes
                        assert history.reports() == saved_index
                    assert not errors
                    assert not any(method != 'GET' or '/api/' in url for method, url in requests)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            worker.join()
