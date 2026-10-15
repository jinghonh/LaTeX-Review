"""Stage 3 uses installed Chrome, including file and static HTTP delivery."""
import json
import os
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest
from playwright.sync_api import sync_playwright
from latex_review.live import build
from latex_review.sources import ProjectSource
from latex_review.contract import ComparisonSource
from latex_review.serve import create_server
from latex_review.annotations import AnnotationStore, load_content
from latex_review.delivery import publication_html

@pytest.fixture
def live_report(tmp_path):
    root=tmp_path/'paper';root.mkdir();entry=root/'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}'+'\nStable original paragraph.\n\nOther unique words.\n'+r'\end{document}')
    output=tmp_path/'report';output.mkdir()
    source=ProjectSource('new',root,'main.tex',ComparisonSource('directory',str(root)))
    build(source,output)
    (output/'live-status.json').write_text(json.dumps({'revision':1,'message':'ready'}))
    return root,entry,output,source

@pytest.fixture
def chrome():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        yield browser
        browser.close()

def ready(p,url):
    p.route('https://**',lambda r:r.abort());p.goto(url);p.wait_for_function('window.reviewAnnotationDeliveryReady===true')

def create(p,body='Offline question'):
    assert p.evaluate('''async body=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));return reviewAnnotations.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body,author:{name:'Tester',type:'user'},category:'question'});}''',body)

@pytest.mark.parametrize('scheme',['file','http'])
def test_offline_lifecycle_reopen_export_import(live_report,chrome,scheme,tmp_path):
    root,entry,output,source=live_report
    from latex_review.delivery import publication_html
    (output/'report.html').write_text(publication_html(output))
    server=None;worker=None
    if scheme=='http':
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(output)));worker=Thread(target=server.serve_forever);worker.start();url=f'http://127.0.0.1:{server.server_port}/report.html'
    else:url=(output/'report.html').as_uri()
    try:
        context=chrome.new_context(viewport={'width':1280,'height':900});p=context.new_page();ready(p,url);create(p)
        assert p.locator('#annotation-overview .annotation-card').count()==1
        tid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].id')
        assert p.evaluate('async id=>reviewAnnotations.update({operation:"reply",thread_id:id,body:"Reply $x$",author:{name:"Agent",type:"agent"},category:"note"})',tid)
        mid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages.at(-1).id')
        assert p.evaluate('async x=>reviewAnnotations.update({operation:"edit",thread_id:x.t,message_id:x.m,body:"Edited reply",category:"suggestion"})',{'t':tid,'m':mid})
        assert p.evaluate('async x=>reviewAnnotations.update({operation:"reply",thread_id:x.t,reply_to:x.m,body:"Nested reply",author:{name:"Agent",type:"agent"},category:"note"})',{'t':tid,'m':mid})
        assert p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages.at(-1).reply_to')==mid
        assert p.evaluate('async id=>reviewAnnotations.update({operation:"resolve",thread_id:id})',tid)
        assert p.evaluate('async id=>reviewAnnotations.update({operation:"reopen",thread_id:id})',tid)
        assert p.evaluate('async id=>reviewAnnotations.update({operation:"update_metadata",thread_id:id,title:"Topic title",tags:["non-experiment","review"]})',tid)
        bundle=p.evaluate('reviewAnnotationDelivery.backup()')
        assert bundle['threads'][tid]['title']=='Topic title'
        assert bundle['threads'][tid]['tags']==['non-experiment','review']
        assert bundle['threads'][tid]['messages'][1]['edits'][0]['body']=='Reply $x$'
        # The portable metadata contract is also accepted by the Python store.
        content=load_content(output);store=AnnotationStore.for_content(content)
        imported=store.dispatch(content,{'operation':'import','expected_revision':0,'bundle':bundle})
        assert imported['threads'][0]['title']=='Topic title'
        assert p.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)',bundle)
        conflict=json.loads(json.dumps(bundle));conflict['threads'][tid]['messages'][0]['body']='conflict'
        assert not p.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)',conflict)
        assert '冲突' in p.locator('#annotation-status').text_content()
        p.reload();p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===1')
        html=p.evaluate('reviewAnnotationDelivery.exportHTML()');export=tmp_path/'export.html';export.write_text(html)
        fresh=chrome.new_context();q=fresh.new_page();errors=[];q.on('pageerror',lambda e:errors.append(str(e)));ready(q,export.as_uri())
        q.wait_for_function('reviewAnnotations.getSnapshot().threads.length===1');assert q.locator('.annotation-card').count()==1;assert not errors
        assert q.evaluate('reviewAnnotations.getSnapshot().threads[0].messages.at(-1).reply_to')==mid
        assert q.evaluate('async id=>reviewAnnotations.update({operation:"delete_thread",thread_id:id})',tid)
        deleted=q.evaluate('reviewAnnotationDelivery.backup()');assert deleted['threads'][tid]['deleted_at']
        assert q.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)',deleted)
        assert not q.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)',bundle)
        fresh.close();context.close()
    finally:
        if server:server.shutdown();worker.join();server.server_close()

def test_live_draft_auto_refresh_and_history(live_report,chrome):
    root,entry,output,source=live_report
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            p=chrome.new_page(viewport={'width':1280,'height':900});ready(p,f'http://127.0.0.1:{server.server_port}/report.html');create(p)
            p.evaluate('''()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));reviewAnnotations.openComposer('create',null,{unit_id:u.id,start:7,end:15,quote:'original'});}''')
            p.locator('.annotation-editor > textarea').fill('Draft before saving source');p.locator('.annotation-composer-settings').evaluate('(e)=>e.open=true');p.locator('.annotation-editor input').fill('Draft Author');p.locator('.annotation-editor select').nth(0).select_option('agent');p.locator('.annotation-editor select').nth(1).select_option('suggestion')
            entry.write_text(entry.read_text().replace('Stable original paragraph.','Other moved text.\n\nStable original paragraph.'))
            old=p.evaluate('reviewAnnotations.getContent().version_id');build(source,output,2);(output/'live-status.json').write_text(json.dumps({'revision':2,'message':'updated'}))
            p.wait_for_function('old=>window.reviewAnnotations?.getContent()?.version_id && reviewAnnotations.getContent().version_id!==old',arg=old)
            p.wait_for_function('document.querySelector(".annotation-editor > textarea").value==="Draft before saving source"')
            d=p.evaluate('Object.values(reviewAnnotations.getDrafts())[0]');assert d['name']=='Draft Author' and d['author']=='agent' and d['category']=='suggestion' and d['selection']['quote']=='original'
            p.locator('.annotation-editor').get_by_text('保存',exact=True).click();p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===2')
            assert p.evaluate('reviewAnnotations.getSnapshot().threads.every(t=>t.association.persisted!==false)')
            entry.write_text(entry.read_text().replace('Stable original paragraph.','Deleted replacement.'));build(source,output,3);(output/'live-status.json').write_text(json.dumps({'revision':3,'message':'updated'}))
            p.wait_for_function('reviewAnnotations.getSnapshot().threads.some(t=>t.association.status==="needs_relocation")')
            assert p.locator('#annotation-overview .annotation-card').count()==2
            p.screenshot(path='/tmp/latex-review-annotations-stage3-desktop.png',full_page=True)
            p.set_viewport_size({'width':390,'height':800});p.wait_for_function('!document.querySelector(".annotation-margin").classList.contains("annotation-margin-desktop")')
            p.screenshot(path='/tmp/latex-review-annotations-stage3-narrow.png',full_page=True)
        finally:server.shutdown();worker.join()

def test_restored_http_edit_draft_can_be_saved(live_report,chrome):
    root,entry,output,source=live_report
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            p=chrome.new_page();ready(p,f'http://127.0.0.1:{server.server_port}/report.html');create(p)
            p.evaluate('''()=>{const t=reviewAnnotations.getSnapshot().threads[0];reviewAnnotations.openComposer('edit',t,null,t.messages[0]);}''')
            p.locator('.annotation-editor > textarea').fill('Restored edit can be saved')
            p.reload();p.wait_for_function('window.reviewAnnotationDeliveryReady===true')
            assert p.locator('.annotation-editor > textarea').input_value()=='Restored edit can be saved'
            # Field order does not affect restored draft validation.
            assert p.evaluate('''()=>{const d=Object.values(reviewAnnotations.getDrafts())[0];return !d.warning;}''')
            p.locator('.annotation-editor').get_by_text('保存',exact=True).click()
            p.wait_for_function('reviewAnnotations.getSnapshot().threads[0].messages[0].body==="Restored edit can be saved"')
            assert p.locator('.annotation-editor').is_hidden()
            assert p.evaluate('Object.keys(reviewAnnotations.getDrafts()).length')==0
            assert AnnotationStore.for_content(load_content(output)).read(load_content(output))['threads'][0]['messages'][0]['body']=='Restored edit can be saved'
            p.close()
        finally:server.shutdown();worker.join()

def test_same_project_windows_and_contract(live_report,chrome):
    root,entry,output,source=live_report
    (output/'report.html').write_text(publication_html(output))
    context=chrome.new_context();p=context.new_page();q=context.new_page();url=(output/'report.html').as_uri();ready(p,url);ready(q,url)
    create(p,'First window');q.wait_for_function('reviewAnnotations.getSnapshot().threads.length===1');create(q,'Second window');p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===2')
    at=p.evaluate('Date.now()+300')
    schedule='''({at,body})=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));window.concurrentRequest={operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body,author:{name:'Concurrent',type:'user'},category:'note'};window.concurrentResult=null;setTimeout(async()=>{window.concurrentResult=await reviewAnnotations.update(window.concurrentRequest);},Math.max(0,at-Date.now()));}'''
    p.evaluate(schedule,{'at':at,'body':'Concurrent first'});q.evaluate(schedule,{'at':at,'body':'Concurrent second'})
    p.wait_for_function('window.concurrentResult!==null');q.wait_for_function('window.concurrentResult!==null')
    for page in (p,q):
        if not page.evaluate('window.concurrentResult'):
            page.evaluate('reviewAnnotations.refresh()');assert page.evaluate('reviewAnnotations.update(window.concurrentRequest)')
    p.evaluate('reviewAnnotations.refresh()');q.evaluate('reviewAnnotations.refresh()')
    assert p.evaluate('reviewAnnotations.getSnapshot().threads.length')==4
    bundle=p.evaluate('reviewAnnotationDelivery.backup()')
    # Python accepts browser-generated lifecycle records unchanged.
    c=load_content(output);store=AnnotationStore.for_content(c)
    result=store.dispatch(c,{'operation':'import','expected_revision':0,'bundle':bundle});assert len(result['threads'])==4
    assert p.evaluate('reviewAnnotations.getContent().units.filter(u=>u.kind==="text").every(u=>reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text)')
    before=p.evaluate('reviewAnnotationDelivery.backup()');bad=json.loads(json.dumps(before));tid=next(iter(bad['threads']));bad['threads'][tid]['original_anchor']['quote']='invalid'
    error=p.evaluate('async b=>{try{await reviewAnnotationDelivery.importBundle(b);return null;}catch(e){return e.message;}}',bad);assert '原文与版本不一致' in error
    assert p.evaluate('reviewAnnotationDelivery.backup()')==before
    context.close()

def test_live_ambiguity_manual_history_and_old_report(live_report,chrome,tmp_path):
    root,entry,output,source=live_report
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            p=chrome.new_page();url=f'http://127.0.0.1:{server.server_port}/report.html';ready(p,url);create(p);tid=p.evaluate('reviewAnnotations.getSnapshot().threads[0].id')
            old_html=(output/'report.html').read_text();old_content=load_content(output)
            entry.write_text(entry.read_text().replace('Stable original paragraph.','Stable original paragraph.\n\nStable original paragraph.'))
            build(source,output,2);(output/'live-status.json').write_text(json.dumps({'revision':2,'message':'updated'}));p.reload();p.wait_for_function('window.reviewAnnotationDeliveryReady===true')
            t=p.evaluate('reviewAnnotations.getSnapshot().threads[0]');assert t['association']['status']=='needs_relocation' and len(t['association']['candidates'])>=2
            p.locator('#annotation-overview').evaluate('(e)=>e.open=true');p.locator('.annotation-card').evaluate('(e)=>e.open=true');p.locator('.annotation-card').get_by_text('手动重新关联',exact=True).click()
            p.evaluate('''()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Other'));const r=reviewAnnotations.coordinates(document.querySelector(u.selector)).range(0,5);getSelection().removeAllRanges();getSelection().addRange(r);document.querySelector(u.selector).dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}''')
            p.wait_for_function('reviewAnnotations.getSnapshot().threads[0].association.status==="manual"')
            t=p.evaluate('reviewAnnotations.getSnapshot().threads[0]');assert t['original_anchor']['quote']=='Stable' and t['association']['anchor']['quote']=='Other' and len(t['history'])==2
            store=AnnotationStore.for_content(load_content(output));latest=store.export()['threads'][tid]['current_anchor']
            historical=tmp_path/'historical';historical.mkdir();(historical/'report.html').write_text(old_html.replace('reading-view live-view','reading-view'));(historical/'annotation-content.json').write_text(json.dumps(old_content))
            with create_server(historical,annotation_project_root=root) as oldserver:
                oldworker=Thread(target=oldserver.serve_forever);oldworker.start()
                try:
                    q=chrome.new_page();ready(q,f'http://127.0.0.1:{oldserver.server_port}/report.html');assert q.evaluate('reviewAnnotations.getSnapshot().threads[0].association.anchor.quote')=='Stable'
                    assert store.export()['threads'][tid]['current_anchor']==latest
                finally:oldserver.shutdown();oldworker.join()
        finally:server.shutdown();worker.join()

def test_images_and_formula_export(live_report,chrome,tmp_path):
    root,entry,output,source=live_report
    import base64
    (root/'figure.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    entry.write_text(entry.read_text().replace(r'\end{document}',r'\begin{figure}\includegraphics{figure.png}\caption{Picture}\end{figure}'+'\n'+r'\[x+1\]'+'\n'+r'\end{document}'))
    build(source,output);(output/'report.html').write_text(publication_html(output));p=chrome.new_page();ready(p,(output/'report.html').as_uri());create(p)
    html=p.evaluate('reviewAnnotationDelivery.exportHTML()');path=tmp_path/'image-export.html';path.write_text(html)
    fresh=chrome.new_context();q=fresh.new_page();ready(q,path.as_uri());assert q.locator('img[src^="data:"]').count()>=1
    assert q.evaluate('reviewAnnotations.getContent().units.some(u=>u.kind==="formula")')
    assert q.evaluate('reviewAnnotations.getContent().units.filter(u=>u.kind==="text").every(u=>reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text)')
    fresh.close()

def test_live_fuzzy_migration_and_unbound_draft(live_report,chrome):
    root,entry,output,source=live_report
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            p=chrome.new_page();ready(p,f'http://127.0.0.1:{server.server_port}/report.html')
            assert p.evaluate('''async()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));return reviewAnnotations.update({operation:'create',selection:{unit_id:u.id,start:0,end:u.text.length,quote:u.text},body:'Whole paragraph',author:{name:'A',type:'user'},category:'note'});}''')
            p.evaluate('''()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));reviewAnnotations.openComposer('create',null,{unit_id:u.id,start:0,end:6,quote:'Stable'});}''');p.locator('.annotation-editor > textarea').fill('Unbound input')
            entry.write_text(entry.read_text().replace('Stable original paragraph.','Stable original paragraph!'));build(source,output,2);(output/'live-status.json').write_text(json.dumps({'revision':2,'message':'updated'}))
            p.wait_for_function('window.reviewAnnotationDeliveryReady===true && reviewAnnotations.getSnapshot().threads[0]?.association.status==="auto_migrated"')
            assert p.locator('.annotation-card').get_by_text('自动迁移',exact=True).count()==1
            p.wait_for_function('document.querySelector(".annotation-editor > textarea").value==="Unbound input"')
            draft=p.evaluate('Object.values(reviewAnnotations.getDrafts())[0]');assert draft['selection'] is None and draft['anchor']['quote']=='Stable'
            p.evaluate('''()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));const r=reviewAnnotations.coordinates(document.querySelector(u.selector)).range(0,6);getSelection().removeAllRanges();getSelection().addRange(r);document.querySelector(u.selector).dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}''')
            p.locator('.annotation-editor').get_by_text('保存',exact=True).click();p.wait_for_function('reviewAnnotations.getSnapshot().threads.length===2')
            # Ordinary reopen restores edit input while refusing a changed target.
            p.evaluate('''()=>{const t=reviewAnnotations.getSnapshot().threads[0];reviewAnnotations.openComposer('edit',t,null,t.messages[0]);}''');p.locator('.annotation-editor > textarea').fill('Stale edit input')
            c=load_content(output);store=AnnotationStore.for_content(c);snapshot=store.read(c);t=snapshot['threads'][0];store.dispatch(c,{'operation':'edit','expected_revision':snapshot['revision'],'thread_id':t['id'],'message_id':t['messages'][0]['id'],'body':'External change','category':'note'})
            p.reload();p.wait_for_function('window.reviewAnnotationDeliveryReady===true');assert p.locator('.annotation-editor > textarea').input_value()=='Stale edit input'
            assert '操作对象' in p.locator('#annotation-status').text_content()
            p.locator('.annotation-editor > textarea').fill('Edited restored input');p.reload();p.wait_for_function('window.reviewAnnotationDeliveryReady===true')
            assert p.locator('.annotation-editor > textarea').input_value()=='Edited restored input'
        finally:server.shutdown();worker.join()

from test_report_annotations_browser import annotated_page

def test_report_export_preserves_translation_and_navigation(annotated_page,tmp_path):
    p=annotated_page
    u=p.evaluate('JSON.parse(document.querySelector("#translation-units").textContent).find(u=>u.side==="new"&&u.kind==="paragraph")')
    translated={'id':u['id'],'side':'new','node_id':u['node_id'],'kind':'paragraph','sentences':[{'number':1,'html':'保留的中文译文','changed':True}]}
    p.route('**/api/translation/status',lambda route:route.fulfill(json={'enabled':True,'available':True,'states':{},'results':{u['id']:translated}}))
    p.reload();p.wait_for_function('window.reviewAnnotationDeliveryReady===true');p.wait_for_selector('.translation-block',state='attached')
    assert p.evaluate('''async()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));return reviewAnnotations.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body:'Shared report',author:{name:'A',type:'user'},category:'note'});}''')
    html=p.evaluate('reviewAnnotationDelivery.exportHTML()');path=tmp_path/'report-export.html';path.write_text(html)
    context=p.context.browser.new_context();q=context.new_page();errors=[];q.on('pageerror',lambda e:errors.append(str(e)));ready(q,path.as_uri())
    assert q.locator('.annotation-card').count()==1
    assert q.locator('.translation-block').filter(has_text='保留的中文译文').count()>=1
    assert q.evaluate('reviewAnnotations.getContent().units.filter(u=>u.kind==="text").every(u=>reviewAnnotations.coordinates(document.querySelector(u.selector)).text===u.text)')
    q.locator('#next-change').click();assert q.locator('.inline-change[open]').count()==1;assert not errors
    context.close()

def test_report_and_live_share_project(annotated_page,tmp_path):
    p=annotated_page
    c=p.evaluate('reviewAnnotations.getContent()');root=Path(c['project_root']);output=tmp_path/'live';output.mkdir()
    source=ProjectSource('new',root,c['entry'],ComparisonSource('directory',str(root)));build(source,output);(output/'live-status.json').write_text(json.dumps({'revision':1,'message':'ready'}))
    assert p.evaluate('''async()=>{const u=reviewAnnotations.getContent().units.find(u=>u.text.startsWith('Stable'));return reviewAnnotations.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body:'Report thread',author:{name:'A',type:'user'},category:'note'});}''')
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            q=p.context.browser.new_page();ready(q,f'http://127.0.0.1:{server.server_port}/report.html');assert q.evaluate('reviewAnnotations.getSnapshot().threads[0].messages[0].body')=='Report thread'
            tid=q.evaluate('reviewAnnotations.getSnapshot().threads[0].id');assert q.evaluate('async id=>reviewAnnotations.update({operation:"reply",thread_id:id,body:"Live reply",author:{name:"B",type:"agent"},category:"note"})',tid)
            p.evaluate('reviewAnnotations.refresh()');assert p.evaluate('reviewAnnotations.getSnapshot().threads[0].messages.at(-1).body')=='Live reply'
        finally:server.shutdown();worker.join()


def test_lightweight_http_export_embeds_pdf_and_preserves_annotations(live_report,chrome,tmp_path):
    root,entry,output,source=live_report
    assets=output/'assets';assets.mkdir()
    pdf=b'%PDF-1.4\nportable resource\n'
    (assets/'figure.pdf').write_bytes(pdf)
    html=(output/'report.html').read_text().replace('</main>','<a href="assets/figure.pdf">PDF</a></main>')
    (output/'report.html').write_text(html)
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            p=chrome.new_page();ready(p,f'http://127.0.0.1:{server.server_port}/report.html');create(p)
            exported=p.evaluate('reviewAnnotationDelivery.exportHTML()')
            assert 'href="assets/figure.pdf"' not in exported
            assert 'data:application/pdf;base64,' in exported
            target=tmp_path/'portable.html';target.write_text(exported)
            context=chrome.new_context();q=context.new_page();ready(q,target.as_uri())
            assert q.evaluate('reviewAnnotations.getSnapshot().threads.length')==1
            create(q,'Second offline question')
            assert q.evaluate('reviewAnnotations.getSnapshot().threads.length')==2
            context.close();p.close()
        finally:server.shutdown();worker.join()
