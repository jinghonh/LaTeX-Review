"""AC-03: real published diagnostics merge by code, target AND source location."""
import json
import os
from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright

from latex_review.checks.diagnostics import preview_diagnostics_data
from latex_review.contract import Diagnostic, SourceLocation


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        yield browser
        browser.close()


def open_workspace(browser, report, diagnostics, *, content_id=None, primary=3):
    page = browser.new_page()
    errors, writes = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    data = preview_diagnostics_data(diagnostics, content_id or report['input']['content_id'])
    embed = lambda value: json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    html = (f'<meta charset="utf-8"><main id="paper-original">Original reading α😀</main>'
            f'<div id="report-summary" data-changes="{primary}">{primary} 项主变更</div>'
            f'<script type="application/json" id="checks-data">{embed(report)}</script>'
            f'<script type="application/json" id="preview-diagnostics-data">{embed(data)}</script>')
    page.route('http://localhost/diagnostics?panel=checks', lambda route: route.fulfill(body=html, content_type='text/html'))
    def api(route):
        if route.request.method != 'GET':
            writes.append(route.request.url)
        route.fulfill(json={'available': True, 'token': 'local', 'paper_id': 'paper', 'stale': False,
                            'current_input_id': report['input_id'], 'current_run': None,
                            'capabilities': {'decisions': True, 'run': False}})
    page.route('http://localhost/api/checks/**', api)
    page.goto('http://localhost/diagnostics?panel=checks')
    page.add_script_tag(path=str(Path(__file__).parents[1] / 'src/latex_review/review_workspace.js'))
    page.wait_for_function('window.reviewWorkspace?.capability.available')
    return page, errors, writes


def published(tmp_path):
    from test_check_workspace import source
    from latex_review.structure import parse_project
    from latex_review.comparison import compare_projects
    from latex_review.report import write_report
    old = source(tmp_path / 'old', r'\documentclass{article}\begin{document}Old prose \ref{missing}.\end{document}', 'old')
    new = source(tmp_path / 'new', r'\documentclass{article}\begin{document}New prose \ref{missing}.\end{document}')
    before, after = parse_project(old.expand()), parse_project(new.expand())
    comparison = compare_projects(before, after)
    result = write_report(before, after, comparison, tmp_path / 'report')
    html = result.html.read_text()
    report = json.loads((result.directory / 'checks.json').read_text())
    # Use the actual raw new-side preview diagnostics, not re-created messages.
    from latex_review.preview import render_preview
    diagnostics = tuple(item for item in render_preview(before, after).diagnostics if item.source_new)
    assert len(report['findings']) == 1 and any(item.code == 'unresolved_reference' for item in diagnostics)
    assert int(re.search(r'id="report-summary" data-changes="(\d+)"', html).group(1)) == comparison.document.summary.changes
    return result, report, diagnostics, (new.root / 'main.tex').read_bytes()


def test_same_issue_counts_once_preserves_both_sources_and_primary_count(browser, tmp_path):
    result, report, diagnostics, raw = published(tmp_path)
    primary = result.document.summary.changes
    unique = Diagnostic('preview_fixture_only', 'warning', 'Unique preview α😀 <img src=x onerror="window.pwned=1">',
                        source_new=SourceLocation('main.tex', 1, 1, 1, 8))
    page, errors, writes = open_workspace(browser, report, (*diagnostics, unique), primary=primary)
    try:
        rows = page.locator('.workspace-findings > li')
        assert rows.count() == 2  # one shared rule/preview issue + one unique preview issue
        assert '问题条目数 2' in page.locator('#workspace-checks').inner_text()
        merged = rows.filter(has_text='unresolved_reference')
        assert merged.count() == 1 and '确定性规则 + 预览诊断' in merged.inner_text()
        merged.get_by_role('button').click()
        drawer = page.locator('#workspace-evidence')
        assert '预览诊断来源' in drawer.inner_text()
        assert diagnostics[0].message in drawer.inner_text()
        assert report['findings'][0]['evidence'][0]['quote'] in drawer.inner_text()
        assert drawer.get_by_role('button', name='作者确认问题', exact=True).count() == 1
        drawer.get_by_role('button', name='关闭证据', exact=True).click()
        rows.filter(has_text='Unique preview').get_by_role('button').click()
        assert drawer.get_by_role('button').all_text_contents() == ['关闭证据']
        assert '没有独立验证的源码证据' in drawer.inner_text()
        assert 'Unique preview α😀 <img' in drawer.inner_text()
        assert page.evaluate('window.pwned') is None
        assert page.locator('#workspace-evidence img').count() == 0
        page.get_by_label('来源', exact=True).select_option('preview_diagnostic')
        assert rows.count() == 2  # merged sources also discoverable by preview origin
        page.get_by_label('来源', exact=True).select_option('deterministic')
        assert rows.count() == 1
        assert page.locator('#report-summary').get_attribute('data-changes') == str(primary)
        assert page.locator('#paper-original').inner_text() == 'Original reading α😀'
        assert page.evaluate('window.reviewWorkspace.report') == report
        assert json.loads(page.locator('#checks-data').text_content()) == report
        assert json.loads((result.directory / 'checks.json').read_text()) == report
        assert (tmp_path / 'new/main.tex').read_bytes() == raw
        assert writes == [] and errors == []
    finally:
        page.close()


@pytest.mark.parametrize('mismatch', ['code', 'target', 'file', 'location', 'unknown-producer', 'uncertain', 'content'])
def test_not_deduplicated_from_message_or_unreliable_identity_alone(browser, tmp_path, mismatch):
    result, report, diagnostics, raw = published(tmp_path)
    evidence = report['findings'][0]['evidence'][0]
    location = SourceLocation(evidence['file'], evidence['start_line'], evidence['end_line'], evidence['start_column'], evidence['end_column'])
    diagnostic = Diagnostic('unresolved_reference', 'info', '交叉引用目标不存在：missing', source_new=location)
    from dataclasses import replace
    if mismatch == 'code':
        diagnostic = replace(diagnostic, code='different_rule')
    elif mismatch == 'target':
        diagnostic = replace(diagnostic, message='交叉引用目标不存在：other-key')
    elif mismatch == 'file':
        diagnostic = replace(diagnostic, source_new=replace(location, file='other.tex'))
    elif mismatch == 'location':
        diagnostic = replace(diagnostic, source_new=replace(location, start_line=100, end_line=100))
    elif mismatch == 'unknown-producer':
        diagnostic = replace(diagnostic, message='arbitrary text missing')
    elif mismatch == 'uncertain':
        diagnostic = replace(diagnostic, source_new=replace(location, confidence=.5, uncertainty_reason='inexact'))
    page, errors, writes = open_workspace(browser, report, (diagnostic,), content_id='other-publication' if mismatch == 'content' else None)
    try:
        assert page.locator('.workspace-findings > li').count() == 2
        assert page.evaluate('window.reviewWorkspace.report') == report
        preview = page.evaluate('window.reviewWorkspace.unifiedIssues.find(item => item.origin === "preview_diagnostic")')
        assert preview['evidence'] == [] and preview['lifecycle'] != 'current'
        assert errors == [] and writes == []
    finally:
        page.close()
