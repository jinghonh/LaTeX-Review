"""Real-browser isolation and cleanup, including failed calls and failed setup."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scope', ['module', 'function'])
def test_browser_resource_isolation_and_failure_cleanup(tmp_path, scope):
    # Run a small independent pytest session: failures here are deliberate and
    # must still release contexts before the next case uses the shared process.
    suite = tmp_path / 'suite'
    suite.mkdir()
    (suite / 'conftest.py').write_text(Path(__file__).with_name('conftest.py').read_text())
    (suite / 'page.html').write_text('<!doctype html><title>Isolated reader</title>')
    (suite / 'test_probe.py').write_text(r'''
import os
from pathlib import Path
import pytest

first_browser = None


def test_failed_call_keeps_independent_readers(isolated_browser):
    global first_browser
    first_browser = isolated_browser
    url = Path(__file__).with_name('page.html').as_uri()
    first = isolated_browser.new_page()
    first.goto(url)
    first.evaluate("localStorage.setItem('reader', 'first')")
    first.evaluate("""async () => {
      const db = await new Promise((ok, no) => {
        const r = indexedDB.open('reader', 1);
        r.onupgradeneeded = () => r.result.createObjectStore('state');
        r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error);
      });
      db.close();
    }""")
    second = isolated_browser.new_page()
    second.goto(url)
    assert second.context is not first.context
    assert second.evaluate("localStorage.getItem('reader')") is None
    assert second.evaluate('indexedDB.databases()') == []
    shared = isolated_browser.new_context()
    writer, reader = shared.new_page(), shared.new_page()
    writer.goto(url)
    reader.goto(url)
    writer.evaluate("localStorage.setItem('reader', 'shared')")
    assert reader.evaluate("localStorage.getItem('reader')") == 'shared'
    # Cookie storage is attached to its context, even without an HTTP server.
    first.context.add_cookies([{'name': 'reader', 'value': 'first', 'url': 'http://127.0.0.1'}])
    assert second.context.cookies('http://127.0.0.1') == []
    raise AssertionError('controlled call failure')


@pytest.fixture
def broken_page(isolated_browser):
    assert isolated_browser.contexts == []
    isolated_browser.new_page()
    raise RuntimeError('controlled setup failure')


def test_failed_setup(broken_page):
    raise AssertionError('unreachable')


def test_clean_after_both_failures(isolated_browser):
    assert isolated_browser.contexts == []
    if os.environ['LATEX_REVIEW_BROWSER_SCOPE'] == 'module':
        assert isolated_browser is first_browser
    else:
        assert isolated_browser is not first_browser
    page = isolated_browser.new_page()
    page.goto(Path(__file__).with_name('page.html').as_uri())
    assert page.evaluate('Object.keys(localStorage)') == []
    assert page.evaluate('indexedDB.databases()') == []
    assert page.context.cookies('http://127.0.0.1') == []
''')
    env = dict(os.environ, LATEX_REVIEW_BROWSER_SCOPE=scope)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-ra', str(suite)],
                            cwd=suite, env=env, text=True, capture_output=True, timeout=90)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert 'controlled call failure' in text and 'controlled setup failure' in text, text
    assert '1 failed, 1 passed, 1 error' in text, text
