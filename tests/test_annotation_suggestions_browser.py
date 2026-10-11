"""Actual browser checks for explicit source suggestion controls."""
from test_report_annotations_browser import annotated_page


def test_suggestion_preview_and_explicit_apply(annotated_page):
    page = annotated_page
    assert page.evaluate('''async () => {const api=reviewAnnotations,u=api.getContent().units.find(u=>u.text.startsWith('Stable'));
      return api.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body:'Improve source',author:{name:'Tester',type:'user'},category:'suggestion',suggested_replacement:{original_chunk:'Stable',replacement_chunk:'Updated'}});}''')
    page.locator('#annotation-overview').evaluate('(e)=>e.open=true')
    page.locator('.annotation-card summary').first.click()
    page.locator('.annotation-suggestion summary').click()
    page.get_by_role('button',name='预览源码差异').click()
    page.wait_for_function("document.querySelector('.annotation-suggestion pre:last-of-type').textContent.includes('-Stable')")
    assert page.evaluate("reviewAnnotations.getSnapshot().threads[0].messages[0].suggested_replacement.status") == 'pending'
    page.get_by_role('button',name='接受并应用源码建议').click()
    page.wait_for_function("reviewAnnotations.getSnapshot().threads[0].messages[0].suggested_replacement.status==='applied'")
    assert not page.evaluate('reviewAnnotations.getSnapshot().threads[0].resolved')

from test_annotation_delivery_browser import live_report, chrome, ready


def test_offline_suggestion_backup_and_no_source_write(live_report, chrome):
    root, entry, output, source = live_report
    original = entry.read_bytes()
    page=chrome.new_page();ready(page,(output/'report.html').as_uri())
    assert page.evaluate('''async()=>{const api=reviewAnnotations,u=api.getContent().units.find(u=>u.text.startsWith('Stable'));return api.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body:'Offline suggestion',author:{name:'Tester',type:'user'},category:'suggestion',suggested_replacement:{original_chunk:'Stable',replacement_chunk:'<script>Updated</script>'}});}''')
    bundle=page.evaluate('reviewAnnotationDelivery.backup()')
    thread=next(iter(bundle['threads'].values()));message=thread['messages'][0]
    assert message['suggested_replacement']['replacement_chunk']=='<script>Updated</script>'
    assert page.locator('.annotation-suggestion script').count()==0
    assert not page.evaluate('''async x=>reviewAnnotations.update({operation:'suggestion_apply',thread_id:x.t,message_id:x.m})''',{'t':thread['id'],'m':message['id']})
    assert '本机源码服务' in page.locator('#annotation-status').text_content()
    assert entry.read_bytes()==original


def test_live_apply_rebuild_loads_new_content(live_report, chrome):
    from latex_review.serve import create_server
    from threading import Thread
    root,entry,output,source=live_report
    with create_server(output,annotation_project_root=root) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            page=chrome.new_page();ready(page,f'http://127.0.0.1:{server.server_port}/report.html')
            old=page.evaluate('reviewAnnotations.getContent().version_id')
            assert page.evaluate('''async()=>{const api=reviewAnnotations,u=api.getContent().units.find(u=>u.text.startsWith('Stable'));return api.update({operation:'create',selection:{unit_id:u.id,start:0,end:6,quote:'Stable'},body:'Update source',author:{name:'Tester',type:'user'},category:'suggestion',suggested_replacement:{original_chunk:'Stable',replacement_chunk:'Updated'}});}''')
            assert page.evaluate('''async()=>{const t=reviewAnnotations.getSnapshot().threads[0];return reviewAnnotations.update({operation:'suggestion_apply',thread_id:t.id,message_id:t.messages[0].id});}''')
            page.wait_for_function('old=>reviewAnnotations?.getContent()?.version_id && reviewAnnotations.getContent().version_id!==old',arg=old)
            page.wait_for_function("reviewAnnotations.getSnapshot().threads[0]?.messages[0].suggested_replacement.status==='applied'")
            assert not page.evaluate('reviewAnnotations.getSnapshot().threads[0].resolved')
            assert 'Updated original' in entry.read_text()
        finally:
            server.shutdown();worker.join()
