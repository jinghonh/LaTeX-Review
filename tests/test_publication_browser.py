"""Static publication keeps browser annotations but offers no standalone HTML export."""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from latex_review.delivery import prepare_publication
from test_annotation_delivery_browser import live_report, chrome, ready, create


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
            assert len(page.evaluate('reviewAnnotationDelivery.backup()')['threads']) == 1
            assert page.get_by_role('button', name='导出最新 HTML', exact=True).count() == 0
            assert not page.locator('#translation-export').is_visible()
            assert page.evaluate('''async()=>{try{await reviewAnnotationDelivery.exportHTML();return false;}catch(e){return e.message.includes('在线报告不提供');}}''')
            page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
            assert not errors
            page.close()
        finally:
            server.shutdown()
            worker.join()
