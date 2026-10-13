"""Exercise the CLI watcher through a real Chrome reload, including its fallback."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize('polling', [False, True])
def test_live_cli_atomic_save_refreshes_chrome_and_excludes_output(tmp_path, polling):
    from playwright.sync_api import sync_playwright
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Initial live passage.\end{document}')
    output = tmp_path / '.latex-review/live'
    log = tmp_path / '.latex-review/process.log'
    log.parent.mkdir()
    command = [sys.executable, '-m', 'latex_review.live', str(entry), '--root', str(tmp_path)]
    if polling:
        command.append('--poll-files')
    with log.open('w') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=stream,
                                   env={**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / '.latex-review/settings')})
        try:
            deadline = time.monotonic() + 12
            url = None
            while time.monotonic() < deadline:
                match = re.search(r'http://127\.0\.0\.1:\d+/report.html', log.read_text())
                if match:
                    url = match[0]
                    break
                assert process.poll() is None, log.read_text()
                time.sleep(.1)
            assert url, log.read_text()
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
                try:
                    page = browser.new_page()
                    page.route('https://**', lambda route: route.abort())
                    page.goto(url)
                    page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Initial live passage.")')
                    before = json.loads((output / 'live-status.json').read_text())['revision']
                    temporary = tmp_path / 'main.tex.tmp'
                    temporary.write_text(entry.read_text().replace('Initial live passage.', 'Updated atomic passage.'))
                    temporary.replace(entry)
                    page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Updated atomic passage.")')
                    status = json.loads((output / 'live-status.json').read_text())
                    assert status['revision'] > before
                    if polling:
                        assert '降级' in status['message']
                        page.wait_for_function('!document.getElementById("live-status").hidden && document.getElementById("live-status").textContent.includes("降级")')
                    # Repeated output/status/cache writes must not create a feedback loop.
                    revision = status['revision']
                    time.sleep(2)
                    assert json.loads((output / 'live-status.json').read_text())['revision'] == revision
                    assert process.poll() is None
                    saved = entry.read_text()
                    entry.unlink()
                    page.wait_for_function('document.getElementById("live-status").textContent.includes("更新失败") && !document.getElementById("live-status").hidden')
                    assert json.loads((output / 'live-status.json').read_text())['revision'] == revision
                    entry.write_text(saved.replace('Updated atomic passage.', 'Recovered live passage.'))
                    page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Recovered live passage.")')
                finally:
                    browser.close()
        finally:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=8)


def test_live_cli_save_during_build_remains_pending(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Initial passage.\end{document}')
    output = tmp_path / '.latex-review/live'
    log = tmp_path / '.latex-review/process.log'
    log.parent.mkdir()
    # Hold the second build after writing HTML. Save again before it returns.
    bootstrap = '''
import sys,time
from latex_review import live
original = live.build
def held(source, output, revision=1):
    result = original(source, output, revision)
    if revision == 2:
        (output / 'building').write_text('ready')
        deadline = time.monotonic() + 12
        while not (output / 'release').exists():
            if time.monotonic() > deadline: raise RuntimeError('test build gate timed out')
            time.sleep(.05)
    return result
live.build = held
sys.exit(live.main())
'''
    def wait(predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if predicate():
                return
            assert process.poll() is None, log.read_text()
            time.sleep(.05)
        pytest.fail(log.read_text())
    with log.open('w') as stream:
        process = subprocess.Popen([sys.executable, '-c', bootstrap, str(entry), '--root', str(tmp_path)],
                                   stdout=stream, stderr=stream,
                                   env={**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / '.latex-review/settings')})
        try:
            wait(lambda: 'http://127.0.0.1:' in log.read_text())
            entry.write_text(entry.read_text().replace('Initial passage.', 'First save.'))
            wait(lambda: (output / 'building').exists())
            for text in ('Second save.', 'Third save.', 'Final save during build.'):
                entry.write_text(r'\documentclass{article}\begin{document}' + text + r'\end{document}')
                time.sleep(.05)
            # Give the native listener time to deliver its batch while building is blocked.
            time.sleep(.7)
            (output / 'release').write_text('continue')
            wait(lambda: json.loads((output / 'live-status.json').read_text())['revision'] >= 3)
            assert 'Final save during build.' in (output / 'report.html').read_text()
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
                try:
                    page = browser.new_page()
                    page.route('https://**', lambda route: route.abort())
                    page.goto(re.search(r'http://127\.0\.0\.1:\d+/report.html', log.read_text())[0])
                    assert 'Final save during build.' in page.locator('.preview-side').inner_text()
                finally:
                    browser.close()
        finally:
            (output / 'release').write_text('cleanup')
            process.send_signal(signal.SIGINT)
            process.wait(timeout=8)
