"""Isolated UI contracts with a fixed API double, not provider/GUI acceptance."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

SCRIPT = Path(__file__).parents[1] / 'src/latex_review/review_semantic.js'


@pytest.fixture
def browser_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page()
        yield page
        browser.close()


def setup(page, *, writable=True, configured=False):
    page.route('http://localhost/semantic-test', lambda route: route.fulfill(body='<section id="semantic"></section><button id="workspace-tab-knowledge">Knowledge</button>', content_type='text/html'))
    page.goto('http://localhost/semantic-test')
    page.evaluate('''({writable,configured}) => {
      window.calls=[]; window.relationship=null; window.exposed=null;
      const inputId='a'.repeat(64);
      const evidence={file:'main.tex',quote:'Original <img onerror="window.pwned=1">',start:0,end:8,reliability:'exact'};
      window.state={request_id:'known-host-id',state:'ready',task:'concept_definition',input_id:inputId,called:false,model_configured:configured,model:'fixed-fake',budget:{max_requests:3,max_input_tokens:60000,max_output_tokens:12000,timeout:60},preview:{entry:'main.tex',unit_count:1,sections:['A']},result:null,error:null};
      window.reviewWorkspace={
        panels:{semantic:document.getElementById('semantic')},capability:{paper_id:'p',available:writable,capabilities:{semantic_import:writable,knowledge:writable}},report:{input_id:inputId},
        element(tag,text,attrs={}) { const x=document.createElement(tag);if(text!==undefined)x.textContent=text;Object.entries(attrs).forEach(([k,v])=>x.setAttribute(k,v));return x; },
        button(text,fn) { const x=document.createElement('button');x.type='button';x.textContent=text;x.onclick=fn;return x; },
        showEvidence(x) {window.exposed=x;},
        async api(path,body) {
          window.calls.push({path,body});
          if(path.startsWith('knowledge/evidence')) return {input_id:inputId,units:[{section_path:['A'],evidence:[evidence]}],total:1,next_cursor:null};
          if(path==='semantic/import') window.state.result={status:'partial',valid_count:1,invalid_count:1,stale_count:0,errors:[{code:'bad_quote',message:'quote not in allowed evidence'}],candidates:[{candidate_id:'candidate',kind:'same_concept',hypothesis:'Maybe <img src=x onerror="window.pwned=1">',rationale:'wrong inference remains candidate',missing_context:['author judgment'],assessment:'candidate',origin:'semantic',lifecycle:'current',review_state:'unreviewed',sources:[evidence]}]};
          if(path.endsWith('/invoke')) {window.state.called=true;window.state.state='completed';}
          if(path==='semantic/decisions') window.state.result.candidates[0].review_state='rejected';
          return structuredClone(window.state);
        }
      };
      window.addEventListener('review-semantic-confirm-relation',e=>window.relationship=e.detail);
    }''', {'writable': writable, 'configured': configured})
    page.add_script_tag(path=str(SCRIPT))
    page.wait_for_selector('#semantic-manager')


def test_static_has_no_writes_or_credential_discovery(browser_page):
    page = browser_page
    setup(page, writable=False)
    assert page.evaluate('calls') == []
    assert page.get_by_role('button', name='准备本地请求（不联网）').count() == 0
    assert '只读' in page.locator('#semantic-manager').inner_text()


def test_selectable_scope_preview_import_partial_and_separate_relation(browser_page):
    page = browser_page
    setup(page)
    assert page.evaluate('calls') == []
    page.get_by_label('语义任务', exact=True).select_option('name_mapping')
    page.get_by_role('button', name='加载当前原文片段以缩小范围').click()
    page.get_by_label('选择片段 0', exact=True).check()
    page.get_by_role('button', name='准备本地请求（不联网）').click()
    page.get_by_text('发送预览：', exact=False).wait_for()
    bodies = [c['body'] for c in page.evaluate('calls') if c.get('body')]
    assert bodies == [{'task': 'name_mapping', 'mode': 'export', 'unit_indexes': [0], 'input_id': 'a' * 64}]
    assert page.get_by_role('button', name='手动调用已配置模型').is_disabled()
    page.get_by_label('模型响应 JSON', exact=True).fill('{"fixed":"response"}')
    page.get_by_role('button', name='验证并导入响应（仅匹配此本地请求）').click()
    page.get_by_text('quote not in allowed evidence', exact=False).wait_for()
    assert '有效 1；无效 1；陈旧 0' in page.locator('#semantic-manager').inner_text()
    page.get_by_role('button', name='查看精确原文证据').click()
    assert page.evaluate('window.exposed.evidence[0].file') == 'main.tex'
    page.on('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='到知识面板显式确认关系').click()
    assert page.evaluate('window.relationship.candidate.assessment') == 'candidate'
    assert not any(c['path'] == 'semantic/decisions' for c in page.evaluate('calls'))
    assert page.evaluate('window.pwned') is None
    assert page.get_by_role('button', name='作者确认问题（非知识关系）').count() == 0


def test_explicit_call_requires_preview_and_confirmation(browser_page):
    page = browser_page
    setup(page, configured=True)
    assert not any(c['path'].endswith('/invoke') for c in page.evaluate('calls'))
    page.get_by_role('button', name='准备本地请求（不联网）').click()
    page.get_by_role('button', name='手动调用已配置模型').wait_for()
    page.once('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('button', name='手动调用已配置模型').click()
    assert not any(c['path'].endswith('/invoke') for c in page.evaluate('calls'))
    page.once('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='手动调用已配置模型').click()
    page.wait_for_function('window.state.called')
    assert len([c for c in page.evaluate('calls') if c['path'].endswith('/invoke')]) == 1
    assert page.get_by_role('button', name='手动调用已配置模型').is_disabled()
