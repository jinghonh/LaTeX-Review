"""当前论文翻译范围、内容缓存与实际浏览器阅读流程。"""
import json
from dataclasses import replace
import os
from threading import Thread

import pytest

from latex_review.contract import ComparisonSource
from latex_review.live import build, fingerprint, refresh_translation
from latex_review.serve import create_server
from latex_review.sources import ProjectSource
from test_translation import provider, _wait


def paper(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text(r'''\documentclass{article}
\title{A paper title}
\begin{document}\maketitle
\begin{abstract}Abstract content.\end{abstract}
\section{Introduction}
Stable content with $x$ and \cite{key}.

Editable content.
\begin{figure}\caption{Figure caption.}\end{figure}
\begin{table}\caption{Table caption.}\begin{tabular}{l}Table cell\\\end{tabular}\end{table}
\begin{thebibliography}{1}\bibitem{key}Reference content.\end{thebibliography}
\end{document}''')
    output = root / '.latex-review' / 'live'
    output.mkdir(parents=True)
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    build(source, output)
    (output / 'live-status.json').write_text(json.dumps({'revision': 1, 'message': 'Ready'}))
    return source, output, entry


def test_live_scope_and_content_cache_after_edits(tmp_path, provider):
    source, output, entry = paper(tmp_path)
    units = json.loads((output / 'translation-units.json').read_text())
    texts = [s['text'] for unit in units for s in unit['sentences']]
    assert len(units) == 6
    for text in ('A paper title', 'Abstract content.', 'Introduction', 'Editable content.', 'Figure caption.'):
        assert text in texts
    assert not any('Table' in text or 'Reference content' in text for text in texts)
    assert not any(s['changed'] for unit in units for s in unit['sentences'])
    stable = next(unit for unit in units if unit['sentences'][0]['text'].startswith('Stable'))
    assert {p['latex'] for s in stable['sentences'] for p in s['protected']} == {'$x$', r'\cite{key}'}
    config, calls, _, _ = provider
    cache = source.root / '.latex-review' / 'translation-cache'
    server = create_server(output, translation_config=config, cache_directory=cache)
    try:
        server.translation.submit([unit['id'] for unit in units])
        _wait(lambda: len(server.translation.snapshot()['results']) == 6)
        assert len(calls) == 6
        entry.write_text(entry.read_text().replace('Editable content.', 'Updated content.'))
        build(source, output, 2)
        refresh_translation(server, output)
        snapshot = server.translation.snapshot()
        assert len(snapshot['results']) == 5
        assert stable['id'] in snapshot['results']
        assert len(calls) == 6
        changed = [unit['id'] for unit in server.translation.units.values() if unit['id'] not in snapshot['results']]
        server.translation.submit(changed)
        _wait(lambda: len(server.translation.snapshot()['results']) == 6)
        assert len(calls) == 7
    finally:
        server.server_close()


def test_custom_cache_does_not_refresh_paper(tmp_path):
    (tmp_path / 'main.tex').write_text('paper')
    output = tmp_path / 'preview'
    output.mkdir()
    cache = tmp_path / 'cache'
    cache.mkdir()
    initial = fingerprint(tmp_path, output, cache_directory=cache)
    (cache / 'result.json').write_text('cache')
    assert fingerprint(tmp_path, output, cache_directory=cache) == initial


def test_live_translation_browser_retry_full_export_and_reload(tmp_path, provider):
    api = pytest.importorskip('playwright.sync_api')
    source, output, entry = paper(tmp_path)
    config, calls, codes, gate = provider
    config = replace(config, concurrency=1)
    server = create_server(output, translation_config=config, cache_directory=tmp_path / 'cache')
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
            except api.Error as exc:
                pytest.skip(f'浏览器运行环境不可用：{exc}')
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('https://**', lambda route: route.abort())
                page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
                page.wait_for_function('document.querySelectorAll("[data-translate-change]").length === 6')
                assert not calls
                codes.append(500)
                paragraph = page.locator('.node-paragraph').filter(has_text='Editable content.')
                paragraph.locator('button[data-translate-change]').click()
                page.wait_for_function('document.querySelector("#translation-status").textContent.includes("失败")')
                paragraph.locator('button[data-translate-change]').click()
                paragraph.locator('.translation-block').wait_for(state='visible')
                assert len(calls) == 2
                # Stop keeps completed translations and permits a later full request.
                gate.clear()
                page.locator('#translate-all').click()
                page.locator('#translation-cancel').wait_for(state='visible')
                page.locator('#translation-cancel').click()
                gate.set()
                page.wait_for_function('document.querySelector("#translation-cancel").hidden')
                assert paragraph.locator('.translation-block').is_visible()
                page.locator('#translate-all').click()
                page.wait_for_function('document.querySelectorAll(".translation-block").length === 6')
                page.locator('#translation-mode').select_option('original')
                assert not paragraph.locator('.translation-block').is_visible()
                page.locator('#translation-mode').select_option('bilingual')
                for width in (1280, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                with page.expect_download() as download:
                    page.locator('#translation-export').click()
                exported = tmp_path / 'translated.html'
                download.value.save_as(exported)
                page.goto(exported.as_uri())
                page.wait_for_function('document.querySelectorAll(".translation-block").length === 6')
                assert page.locator('.translation-block').first.is_visible()
                assert '连接中断' not in page.locator('#live-status').inner_text()
                before = len(calls)
                entry.write_text(entry.read_text().replace('Editable content.', 'Updated content.'))
                build(source, output, 2)
                refresh_translation(server, output)
                (output / 'live-status.json').write_text(json.dumps({'revision': 2, 'message': 'Updated'}))
                page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
                page.wait_for_function('document.querySelectorAll(".translation-block").length === 5')
                assert len(calls) == before
                updated = page.locator('.node-paragraph').filter(has_text='Updated content.')
                assert updated.locator('.translation-block').count() == 0
                assert updated.locator('button[data-translate-change]').is_enabled()
                assert not errors
            finally:
                browser.close()
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_live_command_refreshes_translation_manifest(tmp_path, provider):
    import re
    import signal
    import subprocess
    import sys
    from urllib.request import Request, urlopen

    source, output, entry = paper(tmp_path)
    config, calls, _, _ = provider
    configuration = tmp_path / 'project.toml'
    configuration.write_text('[translation]\n' + f'base_url = "{config.base_url}"\n'
                             + 'model = "test-model"\napi_key_env = "TEST_TRANSLATION_KEY"\n')
    log = tmp_path / 'live.log'
    env = {**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / 'settings')}
    with log.open('w') as stream:
        process = subprocess.Popen([sys.executable, '-m', 'latex_review.live', str(entry),
                                    '--config', str(configuration)], stdout=stream, stderr=stream, env=env)
        try:
            _wait(lambda: 'http://127.0.0.1:' in log.read_text())
            base = re.search(r'http://127\.0\.0\.1:\d+', log.read_text())[0]
            def status():
                with urlopen(base + '/api/translation/status', timeout=3) as response:
                    return json.load(response)
            current = status()
            assert current['enabled']
            assert not calls
            units = json.loads((output / 'translation-units.json').read_text())
            body = json.dumps({'units': [unit['id'] for unit in units]}).encode()
            with urlopen(Request(base + '/api/translation/start', data=body, headers={
                    'Content-Type': 'application/json', 'X-LaTeX-Review-Token': current['token']}), timeout=3):
                pass
            _wait(lambda: len(status()['results']) == 6)
            entry.write_text(entry.read_text().replace('Editable content.', 'Updated content.'))
            _wait(lambda: json.loads((output / 'live-status.json').read_text())['revision'] == 2)
            assert len(status()['results']) == 5
            assert len(calls) == 6
            assert 'Updated content.' in (output / 'report.html').read_text()
        finally:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=8)
