"""Static publication keeps browser annotations but offers no standalone HTML export."""
import base64
import pytest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from latex_review.delivery import prepare_publication
from test_annotation_delivery_browser import live_report, chrome, ready, create
from test_annotations import envelope


def test_static_publication_annotations_and_export_policy(live_report, chrome):
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    (output / 'assets').mkdir()
    (output / 'assets/image.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    html = (output / 'report.html').read_text().replace('</main>', '<img src="assets/image.png"></main>')
    (output / 'report.html').write_text(html)
    publication = prepare_publication(output, 'paper')
    (output / 'report.html').write_bytes(publication.html)
    with ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(output))) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            page = chrome.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            ready(page, f'http://127.0.0.1:{server.server_port}/report.html')
            create(page)
            bundle = page.evaluate('reviewAnnotationDelivery.backup()')
            assert len(bundle['threads']) == 1
            assert page.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)', bundle)
            bundle = page.evaluate('reviewAnnotationDelivery.backup()')
            page.locator("#annotation-overview").evaluate("e=>e.open=true")
            page.locator(".annotation-delivery").evaluate("e=>e.open=true")
            with page.expect_download() as downloaded:
                page.get_by_role('button', name='导出批注 JSON', exact=True).click()
            import json
            assert json.loads(open(downloaded.value.path()).read()) == bundle
            page.reload()
            page.wait_for_function('window.reviewAnnotationDeliveryReady===true')
            assert page.evaluate('reviewAnnotationDelivery.backup()') == bundle
            # An old complete IndexedDB record migrates on the next transaction.
            page.evaluate('''async b=>{const db=await new Promise((ok,no)=>{const r=indexedDB.open('latex-review-annotations',1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error);});await new Promise((ok,no)=>{const tx=db.transaction('projects','readwrite');tx.objectStore('projects').put(b,b.project_id);tx.oncomplete=ok;tx.onerror=no;});db.close();}''', bundle)
            page.reload()
            page.wait_for_function('window.reviewAnnotationDeliveryReady===true')
            assert page.evaluate('reviewAnnotationDelivery.backup()') == bundle
            stored = page.evaluate('''async()=>{const db=await new Promise(ok=>{const r=indexedDB.open('latex-review-annotations',1);r.onsuccess=()=>ok(r.result);});return await new Promise(ok=>{const r=db.transaction('projects').objectStore('projects').get(reviewAnnotations.getContent().project_id);r.onsuccess=()=>{ok(r.result);db.close();};});}''')
            assert set(stored) == {'schema_version','project_id','revision','versions','threads'}
            assert all(isinstance(u, dict) for c in stored['versions'].values() for u in c['units'])
            assert page.get_by_role('button', name='导出最新 HTML', exact=True).count() == 0
            assert not page.locator('#translation-export').is_visible()
            assert page.evaluate('''async()=>{try{await reviewAnnotationDelivery.exportHTML();return false;}catch(e){return e.message.includes('在线报告不提供');}}''')
            page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
            assert not errors
            page.close()
        finally:
            server.shutdown()
            worker.join()


def test_compressed_backup_waits_for_idle_or_annotation_request(live_report, chrome):
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    (output / 'packed.html').write_bytes(prepare_publication(output, 'paper').html)
    page = chrome.new_page()
    page.route('https://**', lambda r: r.abort())
    page.add_init_script('window.requestIdleCallback=fn=>{window.pendingAnnotationLoad=fn;return 1;};window.cancelIdleCallback=()=>{};')
    page.goto((output / 'packed.html').as_uri())
    page.wait_for_function('Boolean(window.pendingAnnotationLoad)')
    assert page.locator('main').is_visible()
    assert not page.evaluate('Boolean(window.reviewAnnotationDeliveryReady)')
    assert page.evaluate('performance.getEntriesByName("annotation-decode").length') == 0
    page.locator('#annotation-overview').evaluate('e=>e.open=true')
    page.wait_for_function('window.reviewAnnotationDeliveryReady===true')
    assert page.evaluate('performance.getEntriesByName("annotation-decode").length') == 1
    create(page)
    assert len(page.evaluate('reviewAnnotationDelivery.backup()')['threads']) == 1
    page.close()


@pytest.mark.parametrize("damage", ["gzip", "size", "contract", "missing-pool", "reference", "cycle", "versions"])
def test_corrupt_compressed_backup_never_overwrites_browser_data(live_report, chrome, damage):
    import json
    import re
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    html = prepare_publication(output, 'paper').html.decode()
    target = output / 'packed.html'
    target.write_text(html)
    page = chrome.new_page()
    ready(page, target.as_uri())
    create(page, 'Must survive corrupted delivery')
    before = page.evaluate('reviewAnnotationDelivery.backup()')
    pattern = r'(<script type="application/json" id="annotation-backup-gzip">)(.*?)(</script>)'
    envelope = json.loads(re.search(pattern, html, re.S)[2])
    if damage == 'gzip':
        envelope['data'] = base64.b64encode(b'broken gzip').decode()
    elif damage == 'size':
        envelope['size'] -= 1
    elif damage in ('missing-pool', 'reference', 'cycle', 'versions'):
        import gzip
        value = json.loads(gzip.decompress(base64.b64decode(envelope['data'])))
        if damage == 'versions':
            value['bundle']['versions'] = []
        elif damage == 'missing-pool':
            del value['units']
        else:
            next(iter(value['bundle']['versions'].values()))['units'][0] = len(value['units']) if damage == 'reference' else {'baseline': 'self'}
        raw = json.dumps(value).encode()
        envelope['data'] = base64.b64encode(gzip.compress(raw)).decode()
        envelope['size'] = len(raw)
    else:
        import gzip
        envelope['data'] = base64.b64encode(gzip.compress(b'null')).decode()
        envelope['size'] = 4
    target.write_text(re.sub(pattern, lambda m: m[1]+json.dumps(envelope)+m[3], html, flags=re.S))
    page.reload()
    page.wait_for_function('window.reviewAnnotationDeliveryError')
    assert '浏览器记录未覆盖' in page.locator('#annotation-status').text_content()
    assert not page.evaluate('Boolean(window.reviewAnnotationDeliveryReady)')
    target.write_text(html)
    page.reload()
    page.wait_for_function('window.reviewAnnotationDeliveryReady===true')
    assert page.evaluate('reviewAnnotationDelivery.backup()') == before
    page.close()


def test_local_content_read_failure_is_visible(live_report, chrome):
    from latex_review.serve import create_server
    root, _, output, _ = live_report
    with create_server(output, annotation_project_root=root) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            page = chrome.new_page()
            page.route('https://**', lambda r: r.abort())
            page.route('**/api/annotations/content', lambda r: r.fulfill(status=503, content_type='application/json', body='{"message":"正文读取失败"}'))
            page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
            page.wait_for_function('document.querySelector("#annotation-status").textContent.includes("正文读取失败")')
            assert not page.evaluate('Boolean(window.reviewAnnotationDeliveryReady)')
            assert page.locator('main').is_visible()
            page.close()
        finally:
            server.shutdown()
            worker.join()


def test_compressed_publication_preserves_history_and_location(live_report, chrome):
    from latex_review.annotations import AnnotationStore, load_content
    from latex_review.live import build
    root, entry, output, source = live_report
    content = load_content(output)
    store = AnnotationStore.open(content)
    u = next(u for u in content['units'] if u['text'].startswith('Stable'))
    created = store.execute(envelope(content, {'operation': 'create', 'expected_revision': 0,
        'selection': {'unit_id': u['id'], 'start': 0, 'end': 6, 'quote': 'Stable'},
        'body': 'History remains', 'author': {'name': 'Tester', 'type': 'user'}, 'category': 'question'}))
    tid = created['result']['thread_id']
    entry.write_text(entry.read_text().replace('Other unique words.', 'Other updated unique words.'))
    build(source, output, 2)
    current = load_content(output)
    u = next(u for u in current['units'] if u['text'].startswith('Other'))
    revision = store.export()['revision']
    AnnotationStore.open(current).execute(envelope(current, {'operation': 'associate', 'expected_revision': revision,
        'thread_id': tid, 'selection': {'unit_id': u['id'], 'start': 0, 'end': 5, 'quote': 'Other'}}))
    (output / 'diff.json').write_text('{}')
    target = output / 'packed.html'
    target.write_bytes(prepare_publication(output, 'paper').html)
    page = chrome.new_page()
    ready(page, target.as_uri())
    backup = page.evaluate('reviewAnnotationDelivery.backup()')
    assert len(backup['versions']) == 2
    assert backup['threads'][tid]['original_anchor']['quote'] == 'Stable'
    assert len(backup['threads'][tid]['history']) == 2
    assert page.evaluate('''()=>{const t=reviewAnnotations.getSnapshot().threads[0];reviewAnnotations.focus(t);return t.association.anchor.quote;}''') == 'Other'
    assert page.evaluate('''()=>{const b=reviewAnnotations.getContent(),t=reviewAnnotations.getSnapshot().threads[0];return reviewAnnotationDelivery.locate(b,t.original_anchor).anchor.quote;}''') == 'Stable'
    page.reload()
    page.wait_for_function('window.reviewAnnotationDeliveryReady===true')
    assert page.evaluate('reviewAnnotationDelivery.backup()') == backup
    page.close()


def test_local_export_resource_failure_preserves_annotations(live_report, chrome):
    from latex_review.serve import create_server
    root, _, output, _ = live_report
    (output / 'assets').mkdir()
    (output / 'assets/image.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    target = output / 'report.html'
    target.write_text(target.read_text().replace('</main>', '<img src="assets/image.png"></main>'))
    with create_server(output, annotation_project_root=root) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            page = chrome.new_page()
            ready(page, f'http://127.0.0.1:{server.server_port}/report.html')
            create(page)
            before = page.evaluate('reviewAnnotationDelivery.backup()')
            page.route('**/assets/image.png', lambda r: r.fulfill(status=503, body='unavailable'))
            message = page.evaluate('''async()=>{try{await reviewAnnotationDelivery.exportHTML();return 'unexpected success';}catch(e){return e.message;}}''')
            assert message == '资源读取失败：assets/image.png'
            assert page.evaluate('reviewAnnotationDelivery.backup()') == before
            page.close()
        finally:
            server.shutdown()
            worker.join()


def test_legacy_historical_reader_reads_new_storage(live_report, chrome):
    import re
    from pathlib import Path
    from latex_review.delivery import publication_html
    from latex_review.annotations import AnnotationStore, load_content
    _,_,output,_=live_report
    (output/'diff.json').write_text('{}')
    # The new publication carries no threads, so it must recover the legacy
    # reader's browser storage. The current UI sends request_id, which the
    # historical delivery script does not support; seed its carried backup.
    (output/'new.html').write_bytes(prepare_publication(output,'paper').html)
    content = load_content(output)
    unit = next(u for u in content['units'] if u['text'].startswith('Stable'))
    AnnotationStore.open(content).execute(envelope(content, {'operation': 'create', 'expected_revision': 0,
        'selection': {'unit_id': unit['id'], 'start': 0, 'end': 6}, 'body': 'Legacy note',
        'author': {'name': 'Tester', 'type': 'user'}, 'category': 'note'}))
    legacy=Path(__file__).parent.joinpath('fixtures/annotation_delivery_legacy.js').read_text()
    old=re.sub(r'<script>/\* Portable annotation contract.*?</script>',lambda m:'<script>'+legacy+'</script>',publication_html(output),flags=re.S)
    (output/'old.html').write_text(old)
    with ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(output))) as server:
        worker=Thread(target=server.serve_forever);worker.start()
        try:
            context=chrome.new_context();page=context.new_page();base=f'http://127.0.0.1:{server.server_port}/'
            ready(page,base+'old.html');assert page.evaluate('reviewAnnotations.getSnapshot().threads.length')==1
            ready(page,base+'new.html');assert page.evaluate('reviewAnnotations.getSnapshot().threads.length')==1
            bundle=page.evaluate('reviewAnnotationDelivery.backup()')
            current=page.evaluate('reviewAnnotations.getContent().version_id');import copy
            extra='0'*64;bundle['versions'][extra]=copy.deepcopy(bundle['versions'][current]);bundle['versions'][extra]['version_id']=extra
            assert page.evaluate('async b=>reviewAnnotationDelivery.importBundle(b)',bundle)
            sharing=page.evaluate('''async ids=>{const db=await new Promise(ok=>{const r=indexedDB.open('latex-review-annotations',1);r.onsuccess=()=>ok(r.result);});const data=await new Promise(ok=>{const r=db.transaction('projects').objectStore('projects').get(reviewAnnotations.getContent().project_id);r.onsuccess=()=>ok(r.result);});db.close();return data.versions[ids[0]].units[0]===data.versions[ids[1]].units[0];}''',[current,extra]);assert sharing
            exported=page.evaluate('''async ids=>{const b=await reviewAnnotationDelivery.backup();const before=b.versions[ids[0]].units[0].text;b.versions[ids[1]].units[0].text='changed';return b.versions[ids[0]].units[0].text===before;}''',[current,extra]);assert exported
            ready(page,base+'old.html');assert page.evaluate('reviewAnnotations.getSnapshot().threads.length')==1
            context.close()
        finally:server.shutdown();worker.join()
