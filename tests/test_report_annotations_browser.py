"""Real HTTP/browser acceptance for report annotation components."""
import os
from pathlib import Path
from threading import Thread
import pytest
from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.serve import create_server

@pytest.fixture
def annotated_page(tmp_path):
    for side in ('old','new'):
        root=tmp_path/side; root.mkdir()
        (root/'main.tex').write_text(r'\documentclass{article}\begin{document}'+'\n'+
            ('Changed old sentence.' if side=='old' else 'Changed new sentence.')+'\n\n'+
            'Stable 😀 words with $x+1$ formula.\n\nSecond paragraph.\n'+r'\end{document}')
    with resolve_sources(entry='main.tex',old_dir=tmp_path/'old',new_dir=tmp_path/'new') as pair:
        old,new=parse_project(pair.old.expand()),parse_project(pair.new.expand())
        report=write_report(old,new,compare_projects(old,new),tmp_path/'report',pdf_converter='')
    from playwright.sync_api import sync_playwright
    with create_server(report.html.parent) as server, sync_playwright() as pw:
        worker=Thread(target=server.serve_forever);worker.start()
        browser=pw.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.route('https://**',lambda r:r.abort())
        page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
        page.wait_for_function('window.reviewAnnotations?.getContent() !== null')
        yield page
        browser.close();server.shutdown();worker.join()


def create(page,changed=False,start=0,end=7):
    return page.evaluate('''async ({changed,start,end}) => {
      const api=reviewAnnotations,u=api.getContent().units.find(u=>u.kind==='text'&&u.text.startsWith(changed?'Changed':'Stable'));
      return api.update({operation:'create',selection:{unit_id:u.id,start,end,quote:u.text.slice(start,end),side:'new'},body:'Question $x$ <img src=x onerror=alert(1)>',author:{name:'Tester',type:'user'},category:'question'});
    }''',{'changed':changed,'start':start,'end':end})


def test_coordinates_and_selection(annotated_page):
    p=annotated_page
    assert p.evaluate('''() => reviewAnnotations.getContent().units.filter(u=>u.kind==='text').every(u=>reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text)''')
    result=p.evaluate('''() => {
      const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));
      const map=reviewAnnotations.coordinates(document.querySelector(u.selector));
      const range=map.range(7,9);getSelection().removeAllRanges();getSelection().addRange(range);
      return reviewAnnotations.selection();
    }''')
    assert result['quote']=='😀' and result['end']-result['start']==2
    assert p.evaluate('''() => {const root=document.createElement('p');root.innerHTML='  A \\n <b> B</b>   C ';return reviewAnnotations.coordinates(root).text;}''')=='A B C'
    assert p.evaluate('''() => {const us=reviewAnnotations.getContent().units.filter(u=>u.kind==='text');const a=document.querySelector(us[0].selector),b=document.querySelector(us.at(-1).selector);const r=document.createRange();r.setStart(a,0);r.setEnd(b,b.childNodes.length);getSelection().removeAllRanges();getSelection().addRange(r);try{reviewAnnotations.selection();return false;}catch(e){return true;}}''')
    p.locator('#layout-mode').select_option('compare')
    assert p.evaluate('''() => {const root=[...document.querySelectorAll('.preview-side[data-side="old"] p')].find(e=>e.textContent.includes('Changed'));const r=document.createRange();r.selectNodeContents(root);getSelection().removeAllRanges();getSelection().addRange(r);try{reviewAnnotations.selection();return false;}catch(e){return true;}}''')
    assert p.evaluate('''() => {const u=reviewAnnotations.getContent().units.find(u=>u.kind==='formula'),root=document.querySelector(u.selector);root.innerHTML='<mjx-container><span>rendered math</span></mjx-container>';const t=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));return reviewAnnotations.coordinates(document.querySelector(t.selector)).text===t.text;}''')


def test_lifecycle_overlap_filters_and_cards(annotated_page):
    p=annotated_page; assert create(p); assert create(p,start=3,end=10); assert create(p,True)
    assert p.locator('.inline-change .annotation-card').count()==1
    assert p.locator('.annotation-card').count()==3
    assert p.locator('.annotation-body img').count()==0
    p.locator('#annotation-overview').evaluate('(e)=>e.open=true')
    p.locator('.annotation-mark').filter(has_text='批注').nth(1).click()
    assert p.locator('#annotation-list li').count()==2
    tid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].id')
    assert p.evaluate('''async id=>reviewAnnotations.update({operation:'reply',thread_id:id,body:'Reply',author:{name:'Agent',type:'agent'},category:'note'})''',tid)
    mid=p.evaluate('id=>reviewAnnotations.getSnapshot().threads.find(t=>t.id===id).messages.at(-1).id',tid)
    assert p.evaluate('''async x=>reviewAnnotations.update({operation:'edit',thread_id:x.t,message_id:x.m,body:'Edited',category:'suggestion'})''',{'t':tid,'m':mid})
    assert p.evaluate('async id=>reviewAnnotations.update({operation:"resolve",thread_id:id})',tid)
    p.locator('.annotation-filters select').nth(3).select_option('true');assert p.locator('#annotation-list li').count()==1
    p.locator('.annotation-filters select').nth(3).select_option('');p.locator('#annotation-overview').get_by_text('下一条',exact=True).click()
    assert p.evaluate('async id=>reviewAnnotations.update({operation:"reopen",thread_id:id})',tid)
    assert p.evaluate('async x=>reviewAnnotations.update({operation:"delete_message",thread_id:x.t,message_id:x.m})',{'t':tid,'m':mid})
    p.locator('.annotation-filters select').nth(1).select_option('agent')
    assert p.locator('#annotation-list li').count()==0
    p.locator('.annotation-filters select').nth(1).select_option('user')
    assert p.locator('#annotation-list li').count()==3
    p.locator('.annotation-filters select').nth(0).select_option('updated_at')
    p.locator('.annotation-filters select').nth(0).select_option('created_at')
    p.locator('.annotation-filters select').nth(2).select_option('note')
    assert p.locator('#annotation-list li').count()==0
    p.locator('.annotation-filters select').nth(2).select_option('question')
    p.locator('.annotation-filters select').nth(4).select_option('exact')
    p.locator('.annotation-filters input').fill('Tester')
    assert p.locator('#annotation-list li').count()==3
    p.locator('.annotation-filters input').fill('absent')
    assert p.locator('#annotation-list li').count()==0
    p.locator('.annotation-filters input').fill('')
    p.set_viewport_size({'width':390,'height':800});p.locator('#annotation-overview').get_by_text('下一条',exact=True).click()
    assert p.evaluate('async id=>reviewAnnotations.update({operation:"delete_thread",thread_id:id})',tid)
    assert p.locator('.annotation-card').count()==2
    p.locator('.annotation-card').last.evaluate('(e)=>e.open=true')
    p.locator('.annotation-card').last.scroll_into_view_if_needed()
    p.screenshot(path='/tmp/latex-review-annotations-narrow.png',full_page=False)


def test_conflict_and_mismatch_preserve_draft(annotated_page):
    p=annotated_page
    p.evaluate('''() => {const u=reviewAnnotations.getContent().units.find(u=>u.kind==='text');reviewAnnotations.openComposer('create',null,{unit_id:u.id,start:0,end:3,quote:u.text.slice(0,3)});}''')
    p.locator('.annotation-editor textarea').fill('Preserved draft')
    p.evaluate('reviewAnnotations.getSnapshot().revision=999')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('document.querySelector("#annotation-status").textContent.includes("输入已保留")')
    assert p.locator('.annotation-editor textarea').input_value()=='Preserved draft'
    p.evaluate('document.querySelector("meta[name=latex-review-annotation-version]").content="other"')
    p.evaluate('reviewAnnotations.refresh()')
    assert p.locator('.annotation-editor textarea').input_value()=='Preserved draft'


def test_formula_toolbar_render_success_failure_and_ui_save(annotated_page):
    p=annotated_page
    formula=p.evaluate('reviewAnnotations.getContent().units.find(u=>u.kind==="formula").selector')
    p.locator(formula).click()
    assert p.locator('.annotation-toolbar').is_visible()
    p.locator('.annotation-toolbar button').click()
    p.locator('.annotation-editor textarea').fill('Formula $x+1$')
    p.evaluate('window.MathJax={tex2chtmlPromise:async ()=>{const n=document.createElement("span");n.dataset.rendered="yes";return n;}}')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_selector('.annotation-body [data-rendered=yes]',state='attached')
    assert p.evaluate('reviewAnnotations.getSnapshot().threads[0].original_anchor.kind')=='formula'
    p.evaluate('() => {window.MathJax.tex2chtmlPromise=async ()=>{throw Error("failed")};}')
    assert p.evaluate('''async ()=>reviewAnnotations.update({operation:'reply',thread_id:reviewAnnotations.getSnapshot().threads[0].id,body:'Readable $bad$ <script>alert(1)</script>',author:{name:'A',type:'agent'},category:'note'})''')
    assert p.locator('.annotation-body').last.text_content()=='Readable $bad$ <script>alert(1)</script>'
    assert p.locator('.annotation-body script').count()==0
    assert p.evaluate('''() => {const u=reviewAnnotations.getContent().units.find(u=>u.kind==='text'&&u.text.startsWith('Stable'));return reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text;}''')
    p.evaluate('''() => {const u=reviewAnnotations.getContent().units.find(u=>u.kind==='text'&&u.text.startsWith('Changed'));const r=reviewAnnotations.coordinates(document.querySelector(u.selector)).range(0,7);getSelection().removeAllRanges();getSelection().addRange(r);document.querySelector(u.selector).dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}''')
    assert p.locator('.annotation-toolbar').is_visible()
    p.locator('.annotation-toolbar button').click();p.locator('.annotation-editor textarea').fill('Text via toolbar')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===2')
    p.locator('#next-change').click()
    assert p.locator('.inline-change[open]').count()==1


def test_real_mathjax_rendering(annotated_page):
    p=annotated_page
    p.unroute('https://**')
    p.reload()
    p.wait_for_function('window.MathJax?.tex2chtmlPromise !== undefined',timeout=45000)
    p.wait_for_function('reviewAnnotations.getContent() !== null')
    assert create(p)
    p.wait_for_selector('.annotation-body mjx-container',state='attached',timeout=45000)
    assert p.evaluate('''() => reviewAnnotations.getContent().units.filter(u=>u.kind==='text').every(u=>reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text)''')


@pytest.mark.parametrize('field,value', [('name','Changed author'),('author','agent'),('category','suggestion')])
def test_pending_save_preserves_complete_form(annotated_page,field,value):
    p=annotated_page
    p.evaluate('''() => {
      const api=reviewAnnotations,u=api.getContent().units.find(u=>u.kind==='text');
      window.delayedSave=null;
      api.setAdapter({read:async()=>[api.getContent(),api.getSnapshot()],update:envelope=>new Promise(resolve=>{window.delayedSave=async()=>{const response=await fetch('/api/annotations/update',{method:'POST',headers:{'Content-Type':'application/json','X-LaTeX-Review-Token':api.getSnapshot().token},body:JSON.stringify(envelope)});resolve(await response.json());};})});
      api.openComposer('create',null,{unit_id:u.id,start:0,end:3,quote:u.text.slice(0,3)});
    }''')
    p.wait_for_function('document.querySelector("#annotation-status").textContent.includes("条批注")')
    p.locator('.annotation-editor textarea').fill('Submitted body')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('window.delayedSave !== null')
    if field=='name':
        p.locator('.annotation-editor input').fill(value)
    else:
        p.locator('.annotation-editor select').nth(0 if field=='author' else 1).select_option(value)
    p.evaluate('() => window.delayedSave()')
    p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===1')
    assert p.locator('.annotation-editor').is_visible()
    draft=p.evaluate('Object.values(reviewAnnotations.getDrafts())[0]')
    assert draft[field]==value
    assert draft['body']=='Submitted body'
    saved=p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[0]')
    assert saved['body']=='Submitted body'
    assert saved['author']['name']!='Changed author'
    assert saved['author']['type']=='user'
    assert saved['category']=='question'


def test_successful_save_notifies_draft_removal(annotated_page):
    p=annotated_page
    p.evaluate('''() => {
      window.persistedDrafts=null;window.draftEvents=[];
      document.addEventListener('annotation-draft',()=>{window.persistedDrafts=reviewAnnotations.getDrafts();window.draftEvents.push(Object.keys(window.persistedDrafts).length);});
      const u=reviewAnnotations.getContent().units.find(u=>u.kind==='text');
      reviewAnnotations.openComposer('create',null,{unit_id:u.id,start:0,end:3,quote:u.text.slice(0,3)});
    }''')
    p.locator('.annotation-editor textarea').fill('Saved draft')
    assert p.evaluate('Object.keys(window.persistedDrafts).length')==1
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('document.querySelector(".annotation-editor").hidden')
    assert p.evaluate('window.persistedDrafts')=={}
    assert p.evaluate('window.draftEvents.at(-1)')==0


def test_existing_annotation_renders_when_mathjax_arrives_later(annotated_page):
    p=annotated_page
    assert create(p)
    p.route('**/tex-chtml.js',lambda r:r.abort())
    p.reload()
    p.wait_for_function('reviewAnnotations.getContent() !== null')
    p.evaluate('async () => {window.MathJax={};await reviewAnnotations.refresh();}')
    p.wait_for_selector('.annotation-formula',state='attached')
    assert p.locator('.annotation-formula').text_content()=='$x$'
    p.locator('.annotation-card').evaluate('(e)=>e.open=true')
    p.locator('.annotation-card').get_by_text('回复',exact=True).click()
    p.locator('.annotation-editor textarea').fill('Keep this draft')
    p.evaluate('''() => {
      window.cardBeforeMath=document.querySelector('.annotation-card');
      window.MathJax={tex2chtmlPromise:async ()=>{const n=document.createElement('span');n.dataset.lateMath='rendered';n.textContent='Rendered x';return n;}};
      window.dispatchEvent(new Event('review-math-ready'));
    }''')
    p.wait_for_selector('.annotation-formula [data-late-math=rendered]',state='attached')
    assert p.evaluate('window.cardBeforeMath===document.querySelector(".annotation-card")')
    assert p.locator('.annotation-card').get_attribute('open') is not None
    assert p.locator('.annotation-editor textarea').input_value()=='Keep this draft'
    assert p.locator('.annotation-body img').count()==0
    assert p.evaluate('Object.values(reviewAnnotations.getDrafts())[0].body')=='Keep this draft'
