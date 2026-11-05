"""Workspace initialization is not a user tab switch or a new history URL."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).parents[1] / 'src' / 'latex_review'


@pytest.mark.parametrize('snapshot', [False, True])
def test_initial_selection_ignores_transient_scroll_and_preserves_published_url(snapshot):
    report = {'input': {'content_id': 'frozen'}, 'findings': [], 'coverage': []}
    meta = '<meta name="latex-review-readonly" content="snapshot">' if snapshot else ''
    html = (meta + '<main style="height:4000px">Original reading</main>'
            '<script type="application/json" id="checks-data">' + json.dumps(report) + '</script>')
    url = 'http://localhost/published-report'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route(url, lambda route: route.fulfill(body=html, content_type='text/html'))
        page.route('**/api/checks/status', lambda route: route.fulfill(
            body=json.dumps({'available': False, 'capabilities': {}}), content_type='application/json'))
        page.goto(url)
        page.add_style_tag(path=str(ASSETS / 'review_workspace.css'))
        # The legacy resize reader can adjust by a few pixels while parsing.
        # That is not a previously visited workspace reading-tab position.
        page.evaluate('scrollTo(0, 4)')
        assert page.evaluate('scrollY') == 4
        page.add_script_tag(path=str(ASSETS / 'review_workspace.js'))
        assert page.evaluate('scrollY') == 0
        assert page.url == url
        assert page.locator('[role=tablist]').evaluate('node => getComputedStyle(node).position') == 'fixed'
        # Real pointer clicks retain the actual reading position: navigation has
        # its own reserved viewport strip and never requires pre-scrolling.
        page.evaluate('scrollTo(0, 333)')
        page.locator('#workspace-tab-checks').click()
        assert page.evaluate('scrollY') == 0
        assert urlsplit(page.url).query == ('' if snapshot else 'panel=checks')
        page.locator('#workspace-tab-reading').click()
        assert page.evaluate('scrollY') == 333
        assert urlsplit(page.url).query == ('' if snapshot else 'panel=reading')
        assert not errors
        browser.close()
