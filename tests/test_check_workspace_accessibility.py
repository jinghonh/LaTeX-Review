"""AC22 automatic acceptance on actual published report HTML, not a WCAG audit."""
from hashlib import sha256
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from latex_review.checks.model import validate_report
from latex_review.comparison import compare_projects
from latex_review.contract import ComparisonSource
from latex_review.report import write_report
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project


@pytest.fixture(scope='module')
def published_accessible_report(tmp_path_factory):
    root = tmp_path_factory.mktemp('published-keyboard-report')
    paragraphs = '\n\n'.join(f'Paragraph {index}: Actual source reading 中文 α😀 remains readable at this location.' for index in range(45))
    source_texts = {}
    parsed = []
    for side in ('old', 'new'):
        directory = root / side
        directory.mkdir()
        checks = (r'First label \label{shared:unicode} and missing reference \ref{missing:chapter}.'
                  '\n\n' + r'Second label \label{shared:unicode} and repeated reference \ref{missing:chapter}.') if side == 'new' else 'Earlier prose without these two issues.'
        text = ('\\documentclass{article}\r\n\\begin{document}\r\n\\section{Published keyboard fixture}\r\n'
                + checks + '\n\n' + paragraphs + '\r\n\\end{document}\r\n')
        (directory / 'main.tex').write_bytes(text.encode('utf-8'))
        source_texts[side] = (directory / 'main.tex').read_bytes()
        source = ProjectSource(side, directory, 'main.tex', ComparisonSource('directory', str(directory)))
        parsed.append(parse_project(source.expand()))
    result = write_report(*parsed, compare_projects(*parsed), root / 'published')
    report = validate_report(json.loads((result.directory / 'checks.json').read_text(encoding='utf-8')))
    findings = report['findings']
    assert [finding['rule_id'] for finding in findings] == ['duplicate_label', 'unresolved_reference']
    assert all(len(finding['evidence']) == 2 for finding in findings)
    assert all(evidence['reliability'] == 'exact' for finding in findings for evidence in finding['evidence'])
    return root, result.directory, report, source_texts


@pytest.mark.parametrize('viewport', [{'width': 390, 'height': 844}, {'width': 1440, 'height': 1000}], ids=['narrow-390', 'desktop-1440'])
def test_published_keyboard_focus_scroll_and_complete_evidence(published_accessible_report, viewport):
    root, directory, report, source_texts = published_accessible_report
    artifacts = {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
                 for path in directory.rglob('*') if path.is_file()}
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page(viewport=viewport, reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            requests.append({'method': request.method, 'url': request.url})
            if url.hostname != 'localhost':
                route.abort()  # No network/CDN/model backend is needed for keyboard acceptance.
                return
            target = (directory / unquote(url.path).removeprefix('/published/')).resolve()
            if target.is_relative_to(directory.resolve()) and target.is_file():
                route.fulfill(body=target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            else:
                route.fulfill(status=404, body=json.dumps({'error': 'published static fixture; no backend'}), content_type='application/json')

        page.route('**/*', serve)
        page.goto('http://localhost/published/report.html', wait_until='networkidle')
        page.wait_for_function('window.reviewWorkspace && !window.reviewWorkspace.capability.available')
        assert json.loads(page.locator('#checks-data').text_content()) == report
        assert 'Published keyboard fixture' in page.locator('#workspace-reading').inner_text()

        def active_tab(panel):
            assert page.evaluate('document.activeElement.id') == 'workspace-tab-' + panel
            assert page.locator('#workspace-tab-' + panel).get_attribute('aria-selected') == 'true'
            assert page.locator('#workspace-tab-' + panel).get_attribute('tabindex') == '0'
            assert page.locator('#workspace-' + panel).is_visible()
            assert page.locator('[role="tabpanel"]:visible').count() == 1

        # Reach the real navigation by keyboard; no locator.focus()/DOM click.
        for _ in range(100):
            page.keyboard.press('Tab')
            if page.evaluate('document.activeElement.id') == 'workspace-tab-reading':
                break
        active_tab('reading')
        for key, expected in [('End', 'coverage'), ('ArrowRight', 'reading'), ('ArrowLeft', 'coverage'),
                              ('Home', 'reading'), ('ArrowRight', 'checks'), ('ArrowRight', 'knowledge'),
                              ('ArrowRight', 'semantic'), ('ArrowLeft', 'knowledge'), ('Home', 'reading')]:
            page.keyboard.press(key)
            active_tab(expected)

        # Set an actual paper reading position, then perform real keyboard tab
        # switches (no Playwright click pre-scroll or replacement HTML fixture).
        page.wait_for_function('window.reviewReadingPosition && !window.reviewReadingPosition.isResizing()')
        page.evaluate('scrollTo(0, 333)')
        # Native scroll events and character-anchor tracking finish on animation
        # frames. Establish a settled reading position, not a transient resize.
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('scrollY') == 333
        page.keyboard.press('ArrowRight')
        active_tab('checks')
        assert page.evaluate('scrollY') == 0
        page.keyboard.press('ArrowLeft')
        active_tab('reading')
        page.wait_for_function('!window.reviewReadingPosition.isResizing()')
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('scrollY') == 333
        page.keyboard.press('ArrowRight')
        active_tab('checks')
        assert page.evaluate('scrollY') == 0

        first_key, second_key = [finding['finding_key'] for finding in report['findings']]
        for _ in range(30):
            page.keyboard.press('Tab')
            if page.evaluate('document.activeElement.dataset.findingKey') == first_key:
                break
        assert page.evaluate('document.activeElement.dataset.findingKey') == first_key
        first = page.locator('[data-finding-key="' + first_key + '"]').element_handle()
        assert 'current · unreviewed' in first.inner_text()
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement.dataset.findingKey') == second_key
        page.keyboard.press('Shift+Tab')
        assert first.evaluate('node => document.activeElement === node')
        page.keyboard.press('Enter')
        drawer = page.locator('#workspace-evidence')
        assert drawer.is_visible()
        assert page.evaluate('document.activeElement.id') == 'workspace-evidence'
        assert drawer.get_attribute('role') == 'dialog'
        assert drawer.get_attribute('aria-label') == '原文证据'

        finding = report['findings'][0]
        # Meaning is literal text, not only a colored badge or CSS class.
        for field in ('origin', 'assessment', 'severity', 'review_state', 'lifecycle', 'delta_status'):
            assert drawer.get_by_text(field + '：' + finding[field], exact=True).count() == 1
        quotes = drawer.locator('section pre')
        assert quotes.all_text_contents() == [evidence['quote'] for evidence in finding['evidence']]
        assert quotes.count() == 2
        geometry = drawer.locator('section').evaluate_all('(nodes) => nodes.map(node => {const r = node.getBoundingClientRect(); return {x:r.x, y:r.y, width:r.width, bottom:r.bottom};})')
        if viewport['width'] == 390:
            # Two exact evidence fragments are stacked, not squeezed side by side.
            assert abs(geometry[0]['x'] - geometry[1]['x']) <= 1
            assert abs(geometry[0]['width'] - geometry[1]['width']) <= 1
            assert geometry[1]['y'] >= geometry[0]['bottom']
            assert abs(drawer.evaluate('node => node.getBoundingClientRect().width') - viewport['width']) <= 1
            assert drawer.evaluate('node => node.scrollHeight > node.clientHeight')

        assert drawer.evaluate('node => node.scrollWidth <= node.clientWidth + 1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert quotes.evaluate_all('(nodes) => nodes.every(node => node.scrollWidth <= node.clientWidth + 1 && node.scrollHeight <= node.clientHeight + 1)')
        for index in range(2):
            page.keyboard.press('Home')
            for _ in range(12):
                fully_visible = quotes.nth(index).evaluate('''node => {
                    const r = node.getBoundingClientRect(), host = node.closest('aside').getBoundingClientRect();
                    return r.top >= Math.max(0, host.top) && r.bottom <= Math.min(innerHeight, host.bottom)
                        && r.left >= Math.max(0, host.left) && r.right <= Math.min(innerWidth, host.right);
                }''')
                if fully_visible:
                    break
                page.keyboard.press('PageDown')
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            assert fully_visible, f'evidence {index} must be completely reachable by keyboard scrolling'
            assert quotes.nth(index).text_content() == finding['evidence'][index]['quote']
        if viewport['width'] == 390:
            assert drawer.evaluate('node => node.scrollTop > 0')
        page.keyboard.press('Home')
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement.textContent') == '关闭证据'
        page.keyboard.press('Escape')
        assert drawer.is_hidden()
        assert first.evaluate('node => node.isConnected && document.activeElement === node')
        # The same real trigger opens again; close does not replace the row.
        page.keyboard.press('Enter')
        assert drawer.is_visible()
        page.keyboard.press('Escape')
        assert drawer.is_hidden()
        assert first.evaluate('node => node.isConnected && document.activeElement === node')
        assert not errors
        assert all(request['method'] == 'GET' for request in requests)
        assert not any('/semantic/' in request['url'] or '/invoke' in request['url'] for request in requests)
        browser.close()
    assert artifacts == {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
                         for path in directory.rglob('*') if path.is_file()}
    assert all((root / side / 'main.tex').read_bytes() == raw for side, raw in source_texts.items())
