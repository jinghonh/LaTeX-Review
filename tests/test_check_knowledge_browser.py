"""Isolated browser contracts for the knowledge panel (not end-to-end service)."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from latex_review.checks.registry import empty_registry, validate_registry

SCRIPT = Path(__file__).parents[1] / 'src/latex_review/review_knowledge.js'


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page()
        yield page
        browser.close()


def _setup(page, registry=None, *, writable=True, conflict=False, source_conflict=False):
    registry = registry or empty_registry()
    page.route('http://localhost/knowledge-test', lambda route: route.fulfill(body='<section id="knowledge"></section>', content_type='text/html'))
    page.goto('http://localhost/knowledge-test')
    page.evaluate('''({registry,writable,conflict,sourceConflict}) => {
      window.calls=[]; window.knowledgeUpdates=0;
      window.addEventListener('review-knowledge-updated',()=>window.knowledgeUpdates++);
      window.envelope={registry,revision:registry.revision,content_hash:'hash-v0',status:'ok',source:'project',path:'/paper/.latex-review-knowledge.json',alternatives:[]};
      const evidence={entry:'main.tex',content_id:'content',file:'main.tex',file_sha256:'0'.repeat(64),start:0,end:10,quote:'Definition',include_instance:'main',start_line:1,start_column:1,end_line:1,end_column:11,reliability:'exact',reason:null};
      window.testEvidence=evidence;
      window.reviewWorkspace={
        panels:{knowledge:document.getElementById('knowledge')},
        capability:{available:writable,capabilities:{knowledge:writable}},
        report:{input_id:'input-current',input:{entry:'main.tex'},findings:[]},
        element(tag,text,attrs={}) { const x=document.createElement(tag);if(text!==undefined)x.textContent=text;Object.entries(attrs).forEach(([k,v])=>x.setAttribute(k,v));return x; },
        button(text,fn) { const x=document.createElement('button');x.type='button';x.textContent=text;x.onclick=fn;return x; },
        showEvidence() {}, refresh() {},
        async api(path,body) {
          window.calls.push({path,body});
          if(sourceConflict && !path.includes('source=external')) throw new Error('both project and external knowledge exist');
          if(path.includes('/evidence')) return {units:[{node_id:'node-definition',evidence:[evidence]}],next_cursor:null};
          if(body && conflict) throw new Error('knowledge changed: content hash conflict');
          if(path==='decisions') {
            const r=structuredClone(window.envelope.registry);r[body.collection].push(body.changes);r.revision++;
            window.envelope={...window.envelope,registry:r,revision:r.revision,content_hash:'hash-new'};return structuredClone(window.envelope);
          }
          if(body?.registry) { const r=structuredClone(body.registry);r.revision++;window.envelope={...window.envelope,registry:r,revision:r.revision,content_hash:'hash-new'}; }
          return structuredClone(window.envelope);
        }
      };
    }''', {'registry': registry, 'writable': writable, 'conflict': conflict, 'sourceConflict': source_conflict})
    page.add_script_tag(path=str(SCRIPT))
    page.wait_for_selector('#knowledge-manager')
    if writable and not source_conflict:
        page.get_by_text('登记概念 / 术语 / 缩写', exact=True).wait_for()


def test_manual_concept_happy_path_no_ai_or_source_writes(browser_page):
    page = browser_page
    _setup(page)
    assert not any(call.get('body') for call in page.evaluate('calls'))
    page.get_by_text('登记概念 / 术语 / 缩写', exact=True).click()
    page.get_by_label('正式名称', exact=True).fill('<img src=x onerror="window.pwned=1">')
    page.get_by_label('允许名称（每行一个）', exact=True).fill('Spectral mixing\nSM')
    page.get_by_label('禁用名称（每行一个）', exact=True).fill('spectral mix')
    page.get_by_label('代码标识（每行一个）', exact=True).fill('spectral_mixer')
    page.get_by_role('button', name='登记概念', exact=True).click()
    page.get_by_text('已保存知识；请复检论文。原文未改动。', exact=True).wait_for()
    calls = page.evaluate('calls')
    write = next(call['body'] for call in calls if call.get('body'))
    assert write['expected_revision'] == 0 and write['expected_hash'] == 'hash-v0'
    assert write['expected_source'] == 'project'
    validate_registry(write['registry'])
    record = write['registry']['concepts'][0]
    assert record['allowed_names'] == ['Spectral mixing', 'SM']
    assert record['code_identifiers'][0]['contexts'] == ['body', 'abstract', 'caption']
    assert page.evaluate('window.pwned') is None
    assert page.evaluate('window.knowledgeUpdates') == 1
    assert not any('semantic' in call['path'] for call in calls)


def test_static_historical_panel_does_not_contact_active_knowledge(browser_page):
    page = browser_page
    _setup(page, writable=False)
    assert page.evaluate('calls') == []
    assert page.get_by_text('静态 / 历史报告只读：不能读取或修改活动知识，也不会创建知识文件。', exact=True).is_visible()
    assert page.get_by_role('button', name='登记概念', exact=True).count() == 0


def test_source_conflict_requires_explicit_selection(browser_page):
    page = browser_page
    _setup(page, source_conflict=True)
    page.get_by_text('both project and external knowledge exist。请选择单一权威来源，不能静默合并。', exact=True).wait_for()
    page.get_by_label('权威知识来源', exact=True).select_option('external')
    page.get_by_text('登记概念 / 术语 / 缩写', exact=True).wait_for()
    assert any(call['path'] == 'knowledge?source=external' for call in page.evaluate('calls'))
    assert not any(call.get('body') for call in page.evaluate('calls'))


def test_completed_checks_refresh_evidence_for_manual_roles(browser_page):
    page = browser_page
    _setup(page)
    page.evaluate("window.calls=[]; window.dispatchEvent(new CustomEvent('review-checks-updated'))")
    page.wait_for_function("window.calls.some(x=>x.path.includes('/evidence'))")
    assert len([call for call in page.evaluate('calls') if '/evidence' in call['path']]) == 1
    assert not any(call.get('body') for call in page.evaluate('calls'))


def test_hash_conflict_preserves_unsaved_form_and_does_not_retry(browser_page):
    page = browser_page
    _setup(page, conflict=True)
    page.get_by_text('登记概念 / 术语 / 缩写', exact=True).click()
    page.get_by_label('正式名称', exact=True).fill('Unsaved author choice')
    page.get_by_role('button', name='登记概念', exact=True).click()
    page.get_by_text('knowledge changed: content hash conflict；发生冲突时请刷新，不会自动覆盖或合并。', exact=True).wait_for()
    assert page.get_by_label('正式名称', exact=True).input_value() == 'Unsaved author choice'
    assert len([x for x in page.evaluate('calls') if x.get('body')]) == 1


def test_role_selection_posts_audited_conditional_decision(browser_page):
    page = browser_page
    registry = empty_registry()
    registry['concepts'] = [{'id': 'sm', 'canonical_name': 'Spectral Mixing'}]
    _setup(page, registry)
    page.get_by_text('登记出现角色：预告 / 定义 / 正式使用', exact=True).click()
    form = page.locator('details').filter(has=page.locator('summary', has_text='登记出现角色：'))
    form.get_by_label('出现角色', exact=True).select_option('definition')
    form.get_by_label('当前原文证据', exact=True).select_option('0')
    form.get_by_role('button', name='确认出现角色', exact=True).click()
    page.get_by_text('已保存知识；请复检论文。原文未改动。', exact=True).wait_for()
    call = next(x for x in page.evaluate('calls') if x['path'] == 'decisions')
    body = call['body']
    assert body['collection'] == 'occurrences'
    assert body['input_id'] == 'input-current' and body['request_id']
    assert len(body['evidence_fingerprint']) == 64
    assert body['changes']['role'] == 'definition'
    assert body['changes']['evidence'][0]['quote'] == 'Definition'
    registry['occurrences'].append(body['changes'])
    validate_registry(registry)
    assert body['expected_revision'] == 0 and body['expected_hash'] == 'hash-v0'
    assert body['expected_source'] == 'project'


def test_semantic_relation_prefill_never_writes_automatically(browser_page):
    page = browser_page
    registry = empty_registry()
    registry['concepts'] = [{'id': 'a', 'canonical_name': 'A'}, {'id': 'b', 'canonical_name': 'B'}]
    _setup(page, registry)
    page.evaluate('''window.dispatchEvent(new CustomEvent('review-semantic-confirm-relation',{detail:{input_id:'input-current',request_id:'request',candidate:{kind:'same_concept',hypothesis:'Possible alias',object_ids:['a','b'],sources:[window.testEvidence]}}}))''')
    assert page.get_by_label('起点概念', exact=True).input_value() == 'a'
    assert page.get_by_label('终点概念', exact=True).input_value() == 'b'
    assert page.get_by_label('关系类型', exact=True).input_value() == 'same_concept'
    assert not [x for x in page.evaluate('calls') if x.get('body')]
    page.evaluate('''window.dispatchEvent(new CustomEvent('review-semantic-confirm-relation',{detail:{input_id:'old-input',candidate:{kind:'same_concept',object_ids:['a','unknown'],sources:[window.testEvidence]}}}))''')
    assert '不会猜测对象或自动导入' in page.locator('[data-knowledge-status]').inner_text()
    assert not [x for x in page.evaluate('calls') if x.get('body')]
