"""Real workspace JS with fixed HTTP responses; not full local-service E2E."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest
from playwright.sync_api import sync_playwright

SCRIPT = Path(__file__).parents[1] / 'src/latex_review/review_workspace.js'


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page()
        yield page
        browser.close()


def setup(page, *, writable=True, stale=False, input_mismatch=False, origin='deterministic', lifecycle='current'):
    evidence = [{'entry': 'main.tex', 'file': 'main.tex', 'file_sha256': '0' * 64,
                 'content_id': 'content', 'start': 0, 'end': 24,
                 'quote': '定义 α😀\r\n"<img src=x onerror="window.pwned=1">',
                 'include_instance': 'main', 'start_line': 1, 'start_column': 1,
                 'end_line': 2, 'end_column': 40, 'reliability': 'exact', 'reason': None}]
    finding = {'finding_key': 'finding:定义/one', 'rule_id': 'unresolved_reference',
               'message': 'A fixed finding', 'origin': origin, 'assessment': 'deterministic',
               'severity': 'warning', 'lifecycle': lifecycle, 'review_state': 'unreviewed',
               'delta_status': 'new', 'evidence': evidence}
    report = {'input_id': 'a' * 64, 'input': {'content_id': 'c' * 64}, 'findings': [finding], 'coverage': []}
    capability = {'available': writable, 'paper_id': 'paper', 'token': 'fixed-local-token',
                  'page_version': 'frozen-page', 'stale': stale,
                  'current_input_id': 'b' * 64 if input_mismatch else report['input_id'],
                  'capabilities': {'decisions': writable, 'run': False}, 'current_run': None}
    calls = []
    snapshot = deepcopy(report)
    html = '<meta charset="utf-8"><meta name="latex-review-annotation-version" content="frozen-page"><main id="paper-original">Untouched source reading</main><script id="checks-data" type="application/json">' + json.dumps(report, ensure_ascii=False).replace('<', '\\u003c') + '</script>'
    page.route('http://localhost/workspace-test?panel=checks', lambda route: route.fulfill(body=html, content_type='text/html'))

    def api(route):
        request = route.request
        path = urlsplit(request.url).path.split('/api/checks/', 1)[1]
        body = request.post_data_json if request.method == 'POST' else None
        calls.append({'path': path, 'method': request.method, 'body': body, 'headers': request.headers})
        if path == 'status':
            payload = capability
        elif path == 'knowledge':
            payload = {'source': 'external', 'revision': 4, 'content_hash': 'knowledge-hash'}
        elif path.endswith('/decisions'):
            finding['review_state'] = {'confirm': 'confirmed', 'reject': 'rejected', 'waive': 'waived', 'defer': 'unreviewed'}[body['action']]
            payload = {'finding': finding}
        else:
            raise AssertionError('unexpected workspace request: ' + path)
        route.fulfill(body=json.dumps(payload, ensure_ascii=False), content_type='application/json')

    page.route('**/api/checks/**', api)
    page.goto('http://localhost/workspace-test?panel=checks')
    page.evaluate("window.testCapabilityReady=false;window.addEventListener('review-workspace-capabilities',()=>window.testCapabilityReady=true)")
    page.add_script_tag(path=str(SCRIPT))
    page.wait_for_function('window.testCapabilityReady')
    page.locator('[data-finding-key]').click()
    return calls, snapshot


def test_confirm_utf8_evidence_fingerprint_overlay_and_escape_after_async_render(browser_page):
    page = browser_page
    calls, frozen = setup(page)
    old_row = page.locator('[data-finding-key]').element_handle()
    page.once('dialog', lambda dialog: dialog.accept('  Verified against original evidence  '))
    page.get_by_role('button', name='作者确认问题', exact=True).click()
    page.get_by_role('button', name='A fixed finding · current · confirmed', exact=True).wait_for()
    page.get_by_text('review_state：confirmed', exact=True).wait_for()
    posted = [call for call in calls if call['method'] == 'POST']
    assert len(posted) == 1
    call, finding = posted[0], frozen['findings'][0]
    assert call['path'] == 'findings/' + quote(finding['finding_key'], safe='') + '/decisions'
    body = call['body']
    expected_fingerprint = sha256(json.dumps(finding['evidence'], ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    assert body == {'input_id': frozen['input_id'], 'request_id': body['request_id'], 'action': 'confirm',
                    'reason': 'Verified against original evidence', 'evidence_fingerprint': expected_fingerprint,
                    'scope': {'id': 'project', 'domain': 'project'}, 'author': 'author'}
    from uuid import UUID
    assert UUID(body['request_id']).version == 4
    assert call['headers']['x-latex-review-token'] == 'fixed-local-token'
    assert call['headers']['x-latex-review-paper-id'] == 'paper'
    assert call['headers']['x-latex-review-page-version'] == 'frozen-page'
    assert call['headers']['x-latex-review-checks-content-id'] == frozen['input']['content_id']
    assert page.locator('#paper-original').inner_text() == 'Untouched source reading'
    assert json.loads(page.locator('#checks-data').text_content()) == frozen
    assert page.evaluate('window.pwned') is None
    assert old_row.evaluate('(node)=>node.isConnected') is False
    page.locator('#workspace-evidence').press('Escape')
    assert page.locator('#workspace-evidence').is_hidden()
    assert page.locator('[data-finding-key]').evaluate('(node)=>document.activeElement===node')
    assert not any(call['path'] == 'knowledge' for call in calls)


def test_waive_reads_explicit_knowledge_source_before_decision(browser_page):
    page = browser_page
    calls, _ = setup(page)
    page.once('dialog', lambda dialog: dialog.accept('Only applicable under the stated assumptions'))
    page.get_by_role('button', name='添加有条件例外', exact=True).click()
    page.get_by_text('review_state：waived', exact=True).wait_for()
    paths = [call['path'] for call in calls]
    decision = next(call for call in calls if call['method'] == 'POST')
    assert decision['body']['action'] == 'waive'
    assert decision['body']['expected_source'] == 'external'
    assert paths.index('knowledge') < paths.index(decision['path'])
    assert [call['method'] for call in calls if call['path'] == 'knowledge'] == ['GET']
    assert len([call for call in calls if call['method'] == 'POST']) == 1


@pytest.mark.parametrize('settings', [{'writable': False}, {'stale': True}, {'input_mismatch': True}, {'origin': 'semantic'}, {'lifecycle': 'stale'}, {'lifecycle': 'resolved'}, {'lifecycle': 'unknown'}])
def test_static_stale_mismatched_or_semantic_findings_have_no_deterministic_decisions(browser_page, settings):
    page = browser_page
    calls, _ = setup(page, **settings)
    assert page.locator('#workspace-evidence').is_visible()
    for label in ('作者确认问题', '拒绝此建议', '添加有条件例外', '暂不决定'):
        assert page.get_by_role('button', name=label, exact=True).count() == 0
    assert all(call['method'] == 'GET' for call in calls)


@pytest.mark.parametrize('label,action,state', [('拒绝此建议', 'reject', 'rejected'), ('暂不决定', 'defer', 'unreviewed')])
def test_reject_and_defer_use_dedicated_finding_decision_route(browser_page, label, action, state):
    page = browser_page
    calls, _ = setup(page)
    page.evaluate("window.updated=0;window.addEventListener('review-checks-updated',()=>window.updated++)")
    page.once('dialog', lambda dialog: dialog.accept('Author decision only'))
    page.get_by_role('button', name=label, exact=True).click()
    page.wait_for_function('window.updated > 0')
    posted = [call for call in calls if call['method'] == 'POST']
    assert len(posted) == 1 and posted[0]['body']['action'] == action
    assert 'expected_source' not in posted[0]['body']
    page.get_by_text('review_state：' + state, exact=True).wait_for()


@pytest.mark.parametrize('reply', [None, '', '   '])
def test_cancelled_or_empty_reason_cannot_write_decisions(browser_page, reply):
    page = browser_page
    calls, _ = setup(page)
    page.once('dialog', lambda dialog: dialog.dismiss() if reply is None else dialog.accept(reply))
    page.get_by_role('button', name='拒绝此建议', exact=True).click()
    assert all(call['method'] == 'GET' for call in calls)
    assert page.get_by_role('button', name='拒绝此建议', exact=True).is_enabled()
