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
    assert p.locator('.inline-change .annotation-card').count()==0
    assert not p.locator('#annotation-overview').evaluate('(e)=>e.open')
    assert p.locator('.annotation-card').count()==3
    assert p.locator('.annotation-body img').count()==0
    p.locator('#annotation-overview').evaluate('(e)=>e.open=true')
    p.locator('.annotation-mark[data-unit]').filter(has_text='2').click()
    p.locator('.annotation-filter-options').evaluate('(e)=>e.open=true')
    assert p.locator('#annotation-list li').count()==3
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
    p.locator('.annotation-panel-header > input').fill('Tester')
    assert p.locator('#annotation-list li').count()==3
    p.locator('.annotation-panel-header > input').fill('absent')
    assert p.locator('#annotation-list li').count()==0
    p.locator('.annotation-panel-header > input').fill('')
    p.set_viewport_size({'width':390,'height':800});p.locator('#annotation-overview').get_by_text('下一条',exact=True).click()
    assert p.evaluate('async id=>reviewAnnotations.update({operation:"delete_thread",thread_id:id})',tid)
    assert p.locator('.annotation-card').count()==2
    p.locator('.annotation-card').last.evaluate('(e)=>e.open=true')
    p.locator('.annotation-card').last.scroll_into_view_if_needed()
    p.screenshot(path='/tmp/latex-review-annotations-narrow.png',full_page=False)


def test_conflict_and_mismatch_preserve_draft(annotated_page):
    p=annotated_page
    p.evaluate('''() => {const u=reviewAnnotations.getContent().units.find(u=>u.kind==='text');reviewAnnotations.openComposer('create',null,{unit_id:u.id,start:0,end:3,quote:u.text.slice(0,3)});}''')
    p.locator('.annotation-editor > textarea').fill('Preserved draft')
    # Set the stale revision and submit in one event, before periodic polling can refresh it.
    p.evaluate('''() => {reviewAnnotations.getSnapshot().revision=999;
        Array.from(document.querySelectorAll('.annotation-editor button')).find(b=>b.textContent==='保存').click();}''')
    p.wait_for_function('document.querySelector("#annotation-status").textContent.includes("输入已保留")')
    assert p.locator('.annotation-editor > textarea').input_value()=='Preserved draft'
    p.evaluate('document.querySelector("meta[name=latex-review-annotation-version]").content="other"')
    p.evaluate('reviewAnnotations.refresh()')
    assert p.locator('.annotation-editor > textarea').input_value()=='Preserved draft'


def test_formula_toolbar_render_success_failure_and_ui_save(annotated_page):
    p=annotated_page
    formula=p.evaluate('reviewAnnotations.getContent().units.find(u=>u.kind==="formula").selector')
    p.locator(formula).click()
    assert p.locator('.annotation-toolbar').is_visible()
    p.locator('.annotation-toolbar button').click()
    p.locator('.annotation-editor > textarea').fill('Formula $x+1$')
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
    p.locator('.annotation-toolbar button').click();p.locator('.annotation-editor > textarea').fill('Text via toolbar')
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
    p.locator('.annotation-editor > textarea').fill('Submitted body')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('window.delayedSave !== null')
    if field=='name':
        p.locator('.annotation-composer-settings').evaluate('(e)=>e.open=true');p.locator('.annotation-editor input').fill(value)
    else:
        p.locator('.annotation-composer-settings').evaluate('(e)=>e.open=true')
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
    p.locator('.annotation-editor > textarea').fill('Saved draft')
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
    p.locator('#annotation-overview').evaluate('(e)=>e.open=true');p.locator('.annotation-card').evaluate('(e)=>e.open=true')
    p.locator('.annotation-card').get_by_text('回复',exact=True).click()
    p.locator('.annotation-editor > textarea').fill('Keep this draft')
    p.evaluate('''() => {
      window.cardBeforeMath=document.querySelector('.annotation-card');
      window.MathJax={tex2chtmlPromise:async ()=>{const n=document.createElement('span');n.dataset.lateMath='rendered';n.textContent='Rendered x';return n;}};
      window.dispatchEvent(new Event('review-math-ready'));
    }''')
    p.wait_for_selector('.annotation-formula [data-late-math=rendered]',state='attached')
    assert p.evaluate('window.cardBeforeMath===document.querySelector(".annotation-card")')
    assert p.locator('.annotation-card').get_attribute('open') is not None
    assert p.locator('.annotation-editor > textarea').input_value()=='Keep this draft'
    assert p.locator('.annotation-body img').count()==0
    assert p.evaluate('Object.values(reviewAnnotations.getDrafts())[0].body')=='Keep this draft'


def test_annotation_panel_progressive_disclosure(annotated_page):
    p=annotated_page
    assert create(p)
    assert create(p,start=3,end=10)
    assert p.locator('.annotation-mark').count()==1
    assert p.locator('.annotation-mark').text_content()=='2'
    assert not p.locator('#annotation-overview').evaluate('(e)=>e.open')
    p.locator('.annotation-mark').click()
    assert p.locator('.annotation-card[open]').count()==1
    assert p.locator('.annotation-message > strong').count()==0
    assert p.locator('.annotation-card').nth(1).locator('.annotation-card-preview').is_visible()
    assert not p.locator('.annotation-filter-options').evaluate('(e)=>e.open')
    assert p.locator('.annotation-context').count()==0
    assert p.locator('.annotation-card[open]').first.get_by_text('回复',exact=True).is_visible()
    p.locator('.annotation-card[open]').first.get_by_text('回复',exact=True).click()
    assert p.locator('.annotation-editor > textarea').evaluate('(e)=>e===document.activeElement')
    assert not p.locator('.annotation-composer-settings').evaluate('(e)=>e.open')
    p.locator('.annotation-editor > textarea').fill('Draft survives closing')
    p.locator('.annotation-panel-heading').get_by_text('关闭',exact=True).click()
    assert p.locator('.annotation-editor > textarea').is_hidden()
    p.locator('.annotation-overview > summary').click()
    assert p.locator('.annotation-editor > textarea').input_value()=='Draft survives closing'
    p.wait_for_function('document.body.classList.contains("annotation-panel-open")')
    p.screenshot(path='/tmp/latex-review-annotations-desktop.png')
    p.set_viewport_size({'width':390,'height':800})
    assert p.locator('#annotation-overview').evaluate('(e)=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&e.scrollWidth<=e.clientWidth;}')
    p.screenshot(path='/tmp/latex-review-annotations-mobile.png')


def test_active_annotation_highlight_and_unified_delete(annotated_page):
    p=annotated_page
    assert create(p,start=0,end=6)
    assert create(p,start=7,end=9)
    assert p.evaluate("[...CSS.highlights.get('annotations')].map(r=>r.toString())")==['Stable','😀']
    p.locator('.annotation-overview > summary').click()
    p.locator('.annotation-card > summary').nth(0).click()
    assert p.locator('.annotation-card[open]').count()==1
    assert p.evaluate("[...CSS.highlights.get('annotation-active')].map(r=>r.toString())")==['Stable']
    p.locator('.annotation-card > summary').nth(1).click()
    assert p.locator('.annotation-card[open]').count()==1
    assert p.locator('.annotation-card.is-active').count()==1
    assert p.evaluate("[...CSS.highlights.get('annotation-active')].map(r=>r.toString())")==['😀']
    tid=p.evaluate('reviewAnnotations.getSnapshot().threads[1].id')
    assert p.evaluate("async id=>reviewAnnotations.update({operation:'reply',thread_id:id,body:'Reply',author:{name:'Tester',type:'user'},category:'note'})",tid)
    card=p.locator('.annotation-card[open]')
    assert card.get_by_text('删除内容',exact=True).count()==0
    assert card.get_by_text('删除线程',exact=True).count()==0
    dialogs=[]
    def cancel(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()
    p.on('dialog',cancel)
    card.get_by_text('删除批注',exact=True).click()
    assert '1 条回复' in dialogs[0]
    assert p.locator('.annotation-card').count()==2
    p.remove_listener('dialog',cancel)
    p.once('dialog',lambda dialog:dialog.accept())
    card.get_by_text('删除批注',exact=True).click()
    p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===1')
    assert p.locator('.annotation-card').count()==1
    assert p.evaluate("CSS.highlights.get('annotation-active').size")==0
    p.locator('.annotation-card > summary').click()
    p.locator('.annotation-panel-heading').get_by_text('关闭',exact=True).click()
    p.wait_for_function("CSS.highlights.get('annotation-active').size===0")
    assert p.evaluate("CSS.highlights.get('annotations').size")==1


def test_active_formula_and_unlocated_annotation(annotated_page):
    p=annotated_page
    assert p.evaluate("async()=>{const u=reviewAnnotations.getContent().units.find(u=>u.kind==='formula');return reviewAnnotations.update({operation:'create',selection:{unit_id:u.id,side:'new'},body:'Formula comment',author:{name:'Tester',type:'user'},category:'note'});}")
    p.locator('.annotation-overview > summary').click()
    p.locator('.annotation-card > summary').click()
    assert p.locator('.annotation-active-formula').count()==1
    assert p.locator('.annotation-located-formula').count()==1
    p.evaluate("()=>{const t=reviewAnnotations.getSnapshot().threads[0];t.association.status='needs_relocation';reviewAnnotations.focus(t);}")
    assert p.locator('.annotation-active-formula').count()==0
    assert p.locator('.annotation-located-formula').count()==0
    assert p.locator('.annotation-card[open]').get_by_text('手动重新关联',exact=True).is_visible()
    assert '待重新定位' in p.locator('#annotation-status').inner_text()


def test_annotation_metadata_and_reply_hierarchy(annotated_page):
    p=annotated_page
    assert create(p)
    tid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].id')
    mid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[0].id')
    assert p.evaluate("async x=>reviewAnnotations.update({operation:'edit',thread_id:x.t,message_id:x.m,body:'请补充这个模型的适用条件。',category:'question'})",{'t':tid,'m':mid})
    for name,identity,category,body in [('作者甲','user','note','已补充适用条件，请检查。'),('作者乙','agent','suggestion','建议进一步说明边界条件。')]:
        assert p.evaluate("async x=>reviewAnnotations.update({operation:'reply',thread_id:x.id,author:{name:x.name,type:x.identity},category:x.category,body:x.body})",{'id':tid,'name':name,'identity':identity,'category':category,'body':body})
    p.locator('.annotation-overview > summary').click()
    card=p.locator('.annotation-card')
    title=card.locator(':scope > summary')
    assert title.locator('.annotation-author-name').inner_text()=='Tester'
    assert title.locator('.annotation-identity-tag').inner_text()=='用户'
    assert title.locator('.annotation-status-tag').inner_text()=='未解决'
    assert title.locator('.annotation-category-tag').inner_text()=='问题'
    assert title.locator('.annotation-card-reply-count').inner_text()=='2 条回复'
    assert title.locator('time').is_hidden()
    title.click()
    assert title.locator('time').is_visible()
    assert title.locator('time').get_attribute('datetime')==p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[0].created_at')
    assert card.locator('.annotation-replies-heading').inner_text()=='回复 · 2'
    assert card.locator('.annotation-reply').count()==2
    assert card.locator('.annotation-reply .annotation-reply').count()==0
    assert card.locator('.annotation-reply .annotation-author-name').all_text_contents()==['作者甲','作者乙']
    assert card.locator('.annotation-reply .annotation-identity-tag').all_text_contents()==['用户','智能体']
    assert card.locator('.annotation-reply .annotation-category-tag').all_text_contents()==['说明','建议']
    assert card.locator('.annotation-status-tag').count()==1
    p.screenshot(path='/tmp/latex-review-annotation-tags-desktop.png')
    p.set_viewport_size({'width':390,'height':800})
    assert card.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
    p.screenshot(path='/tmp/latex-review-annotation-tags-mobile.png')
    reply=card.locator('.annotation-reply').last
    assert card.get_by_text('消息操作',exact=True).count()==0
    assert reply.get_by_text('编辑',exact=True).is_visible()
    reply.get_by_text('编辑',exact=True).click()
    assert p.locator('.annotation-editor > textarea').input_value()=='建议进一步说明边界条件。'


def test_navigation_from_single_annotation_marker(annotated_page):
    p = annotated_page
    create(p)
    create(p, True)
    p.locator('.annotation-mark[data-unit]').filter(has_text='1').last.click()
    assert p.locator('.annotation-panel-header h2').inner_text() == '批注'
    assert p.locator('.annotation-card').count() == 2
    assert p.locator('.annotation-card[open]').count() == 1
    before = p.locator('.annotation-card.is-active').get_attribute('data-annotation-thread')
    p.locator('.annotation-navigation').get_by_text('下一条', exact=True).click()
    after = p.locator('.annotation-card.is-active').get_attribute('data-annotation-thread')
    assert after != before
    p.locator('.annotation-navigation').get_by_text('上一条', exact=True).click()
    assert p.locator('.annotation-card.is-active').get_attribute('data-annotation-thread') == before


def test_reply_to_reply(annotated_page):
    p=annotated_page
    create(p)
    tid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].id')
    assert p.evaluate("async id=>reviewAnnotations.update({operation:'reply',thread_id:id,body:'First reply',author:{name:'作者甲',type:'user'},category:'note'})",tid)
    p.locator('.annotation-overview > summary').click()
    p.locator('.annotation-card > summary').click()
    p.locator('.annotation-reply').get_by_text('回复',exact=True).click()
    assert p.locator('.annotation-editor h3').inner_text()=='回复 作者甲'
    assert p.locator('.annotation-editor > textarea').input_value()==''
    p.locator('.annotation-editor > textarea').fill('@作者甲：\n\n补充回复')
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('reviewAnnotations.getSnapshot().threads[0].messages.length===3')
    assert p.locator('.annotation-reply').count()==2
    assert p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[2].reply_to===reviewAnnotations.getSnapshot().threads[0].messages[1].id')
    assert p.locator('.annotation-reply > .annotation-reply-children > .annotation-reply').count()==1
    assert '补充回复' in p.locator('.annotation-reply').last.inner_text()
    assert p.locator('.annotation-reply').last.get_by_text('回复',exact=True).is_visible()
    first=p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[1].id')
    child=p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[2].id')
    assert p.evaluate("async x=>reviewAnnotations.update({operation:'reply',thread_id:x.t,body:'Same name sibling',author:{name:'作者甲',type:'user'},category:'note'})",{'t':tid})
    assert p.evaluate("async x=>reviewAnnotations.update({operation:'edit',thread_id:x.t,message_id:x.m,body:'Edited without mention',category:'note'})",{'t':tid,'m':child})
    p.reload()
    p.wait_for_function('reviewAnnotations.getSnapshot()?.threads[0]?.messages.length===4')
    p.locator('.annotation-overview > summary').click()
    p.locator('.annotation-card > summary').click()
    parent=p.locator(f'[data-annotation-message="{first}"]')
    assert parent.locator(f':scope > .annotation-reply-children > [data-annotation-message="{child}"]').count()==1
    assert p.locator('.annotation-replies > .annotation-reply').count()==2


def test_external_annotation_updates_without_reload_preserve_draft(annotated_page):
    p=annotated_page
    create(p)
    p.locator('.annotation-overview > summary').click()
    p.locator('.annotation-card > summary').click()
    active=p.locator('.annotation-card.is-active').get_attribute('data-annotation-thread')
    p.locator('.annotation-card-actions').get_by_text('回复',exact=True).click()
    p.locator('.annotation-editor > textarea').fill('Unsaved draft')
    p.locator('.annotation-card.is-active').evaluate('(e)=>e.dataset.pollSentinel="kept"')
    p.wait_for_timeout(2200)
    assert p.locator('.annotation-card.is-active').get_attribute('data-poll-sentinel')=='kept'
    revision=p.evaluate('reviewAnnotations.getSnapshot().revision')
    status=p.request.get(p.url.replace('report.html','api/annotations/status')).json()
    content=p.evaluate('reviewAnnotations.getContent()')
    u=next(u for u in content['units'] if u['kind']=='text' and u['text'].startswith('Changed'))
    response=p.request.post(p.url.replace('report.html','api/annotations/update'),data={
        'project_id':content['project_id'],'version_id':content['version_id'],
        'request':{'operation':'create','expected_revision':status['revision'],
                   'selection':{'unit_id':u['id'],'start':0,'end':7,'quote':'Changed','side':'new'},
                   'body':'External agent comment','author':{'name':'Agent','type':'agent'},'category':'note'}},
        headers={'X-LaTeX-Review-Token':status['token']})
    assert response.ok
    p.wait_for_function('(r)=>reviewAnnotations.getSnapshot().revision>r',arg=revision,timeout=6000)
    assert p.locator('.annotation-card').count()==2
    assert p.locator('.annotation-card.is-active').get_attribute('data-annotation-thread')==active
    assert p.locator('.annotation-editor > textarea').input_value()=='Unsaved draft'
    assert p.locator('.annotation-editor').is_visible()
    assert p.locator('.annotation-editor > textarea').evaluate('(e)=>document.activeElement===e')
    assert p.evaluate('CSS.highlights.get("annotation-active").size')==1
    p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
    p.wait_for_function('(id)=>reviewAnnotations.getSnapshot().threads.find(t=>t.id===id).messages.length===2',arg=active)
    assert p.evaluate('id=>reviewAnnotations.getSnapshot().threads.find(t=>t.id===id).messages.at(-1).body',active)=='Unsaved draft'
