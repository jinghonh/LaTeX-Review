"""Optional real-browser synthetic knowledge/semantic probe; never invokes a model."""
from copy import deepcopy
from hashlib import sha256
import json

from playwright.sync_api import expect


def _exercise(page, frame, evidence, manuscript):
    source_files = sorted(p for p in manuscript.rglob('*') if p.suffix in {'.tex', '.bib', '.bst'})
    before = {str(p.relative_to(manuscript)): sha256(p.read_bytes()).hexdigest() for p in source_files}
    page.set_viewport_size({'width': 1440, 'height': 1000})
    frame.get_by_role('tab', name='概念与符号', exact=True).click()
    knowledge = frame.locator('#knowledge-manager')
    knowledge.get_by_text('登记概念 / 术语 / 缩写', exact=True).click()
    knowledge.get_by_label('正式名称', exact=True).first.fill('adaptive estimator')
    knowledge.get_by_label('禁用名称（每行一个）', exact=True).first.fill('adaptive estimation method')
    knowledge.get_by_role('button', name='登记概念', exact=True).click()
    expect(knowledge.get_by_role('heading', name='概念 1 · 符号 0 · 角色 0 · 关系 0', exact=True)).to_be_visible(timeout=30000)
    page.wait_for_function("() => document.querySelector('iframe').contentWindow.reviewWorkspace.capability.stale === true")
    page.screenshot(path=str(evidence / 'diff-knowledge.png'), full_page=True)
    frame.get_by_role('tab', name='问题检查', exact=True).click()
    launch = frame.get_by_role('button', name='检查当前论文', exact=True)
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/api/checks/runs')) as started:
        launch.click()
    task = started.value.json()
    assert 'run_id' in task, task
    page.wait_for_function("""runId => {
        const workspace = document.querySelector('iframe').contentWindow.reviewWorkspace;
        const run = workspace.capability.current_run;
        return run?.run_id === runId && ['complete','completed','partial','succeeded'].includes(run.state || run.status)
            && workspace.report?.input_id === workspace.capability.current_input_id;
    }""", arg=task['run_id'], timeout=60000)
    expect(launch).to_be_enabled(timeout=30000)
    report = frame.locator('body').evaluate('() => window.reviewWorkspace.report')
    terms = [f for f in report['findings'] if f['rule_id'] == 'term.forbidden_name']
    assert len(terms) == 1, [f['rule_id'] for f in report['findings']]
    assert terms[0]['evidence'][0]['quote'] == 'adaptive estimation method', terms[0]
    frame.locator('.workspace-findings').get_by_role('button', name='term.forbidden_name:', exact=False).click()
    drawer = frame.get_by_role('dialog', name='原文证据', exact=True)
    expect(drawer).to_be_visible()
    page.screenshot(path=str(evidence / 'diff-knowledge-evidence.png'), full_page=True)
    drawer.press('Escape')
    frame.get_by_role('tab', name='AI 审查', exact=True).click()
    semantic = frame.locator('#semantic-manager')
    semantic.get_by_role('button', name='准备本地请求（不联网）', exact=True).click()
    expect(semantic.get_by_role('button', name='导出请求 JSON', exact=True)).to_be_enabled(timeout=30000)
    expect(semantic.get_by_role('button', name='手动调用已配置模型', exact=True)).to_be_disabled()
    with page.expect_download() as exported:
        semantic.get_by_role('button', name='导出请求 JSON', exact=True).click()
    request_path = evidence / 'diff-semantic-request.json'
    exported.value.save_as(request_path)
    request = json.loads(request_path.read_text())
    assert str(manuscript) not in json.dumps(request), 'Absolute manuscript path leaked'
    unit = request['evidence_units'][0]
    valid = {'kind': 'concept', 'hypothesis': 'Synthetic candidate only: inspect this passage.',
             'evidence': [{'unit_id': unit['id'], 'quote': unit['text']}],
             'rationale': 'Fixed-response provenance exercise, not semantic quality evidence.',
             'missing_context': ['Author judgment is still required.']}
    invalid = deepcopy(valid)
    invalid['evidence'][0]['unit_id'] = 'invented-unit-do-not-trust'
    response = {'schema_version': '1.0', 'request_id': request['request_id'],
                'task': request['task'], 'candidates': [valid, invalid]}
    (evidence / 'diff-semantic-fixed-response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2) + '\n')
    semantic.get_by_label('模型响应 JSON', exact=True).fill(json.dumps(response, ensure_ascii=False))
    semantic.get_by_role('button', name='验证并导入响应（仅匹配此本地请求）', exact=True).click()
    expect(semantic).to_contain_text('有效 1；无效 1；陈旧 0', timeout=30000)
    expect(semantic.locator('.semantic-candidate')).to_have_count(1)
    page.screenshot(path=str(evidence / 'diff-semantic.png'), full_page=True)
    semantic.get_by_role('button', name='查看精确原文证据', exact=True).click()
    expect(drawer).to_contain_text('candidate')
    expect(drawer.locator('pre').first).not_to_be_empty()
    page.screenshot(path=str(evidence / 'diff-semantic-evidence.png'), full_page=True)
    drawer.press('Escape')
    page.wait_for_function('() => document.querySelector("iframe").contentWindow.reviewAnnotations?.isReady()')
    thread_count_before = frame.locator('body').evaluate('() => window.reviewAnnotations.getSnapshot().threads.length')
    conversions = []
    for attempt in range(2):
        frame.get_by_role('tab', name='问题检查', exact=True).click()
        frame.locator('.workspace-findings').get_by_role('button', name='term.forbidden_name:', exact=False).click()
        expect(drawer).to_be_visible()
        page.once('dialog', lambda dialog: dialog.accept())
        with page.expect_response(lambda result: result.request.method == 'POST' and '/api/checks/findings/' in result.url and result.url.endswith('/annotation')) as converted:
            drawer.get_by_role('button', name='转为批注（不解决问题）', exact=True).click()
        linked = converted.value.json()
        assert 'annotation_thread_id' in linked, linked
        conversions.append(linked)
        expect(frame.locator('#workspace-reading')).to_be_visible(timeout=30000)
        page.wait_for_function('threadId => document.querySelector("iframe").contentWindow.reviewAnnotations.getSnapshot().threads.some(thread => thread.id === threadId)', arg=linked['annotation_thread_id'])
        page.screenshot(path=str(evidence / ('diff-annotation-conversion.png' if attempt == 0 else 'diff-annotation-reused.png')), full_page=True)
    assert conversions[0]['annotation_thread_id'] == conversions[1]['annotation_thread_id'], conversions
    assert conversions[0]['reused'] is False and conversions[1]['reused'] is True, conversions
    thread_count_after = frame.locator('body').evaluate('() => window.reviewAnnotations.getSnapshot().threads.length')
    assert thread_count_after == thread_count_before + 1, (thread_count_before, thread_count_after)
    persisted = frame.locator('body').evaluate('async (_node, key) => (await window.reviewWorkspace.api("findings?limit=100")).findings.find(item => item.finding_key === key)', terms[0]['finding_key'])
    assert persisted['lifecycle'] == 'current' and persisted['review_state'] == 'unreviewed', persisted
    after = {str(p.relative_to(manuscript)): sha256(p.read_bytes()).hexdigest() for p in sorted(manuscript.rglob('*')) if p.suffix in {'.tex', '.bib', '.bst'}}
    unchanged = before == after
    assert unchanged, 'Knowledge/semantic/annotation action changed manuscript bytes'
    return {'knowledge_registration': True, 'knowledge_stales_previous_checks': True,
            'forbidden_name_finding': terms[0], 'semantic_request_id': request['request_id'],
            'semantic_valid_count': 1, 'semantic_invalid_count': 1, 'semantic_candidate_only': True,
            'model_invoked': False, 'annotation_conversions': conversions,
            'annotation_thread_count_before': thread_count_before, 'annotation_thread_count_after': thread_count_after,
            'conversion_keeps_finding_current_unreviewed': True,
            'manuscript_sha256_before': before, 'manuscript_sha256_after': after,
            'manuscript_bytes_unchanged': unchanged}


def exercise(page, frame, evidence, manuscript):
    try:
        return _exercise(page, frame, evidence, manuscript)
    except Exception as error:
        page.screenshot(path=str(evidence / 'diff-offline-panel-failure.png'), full_page=True)
        diagnostic = {'error': str(error), 'visible_text': frame.locator('body').inner_text(),
                      'checks_status': frame.locator('body').evaluate('() => { const d = {...window.reviewWorkspace.capability}; delete d.token; return d; }')}
        (evidence / 'diff-offline-panel-failure.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + '\n')
        raise
