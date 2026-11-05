"""Explicit annotation conversion preserves source and finding authority."""
import json
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs

import pytest

from latex_review.annotations import AnnotationStore, load_content
from latex_review.checks.finding_review import fingerprint
from latex_review.checks.http import dispatch
from latex_review.checks.service import CheckCoordinator, CheckError
from latex_review.contract import ComparisonSource
from latex_review.identity import paper_identity
from latex_review.live import build
from latex_review.sources import ProjectSource


@pytest.fixture
def conversion(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Hello \ref{missing}.\end{document}')
    output = tmp_path / 'preview'
    output.mkdir()
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    build(source, output)
    identity = paper_identity(root, entry, require_entry=True)
    pid = identity['paper_id']
    service = CheckCoordinator(tmp_path / 'state', lambda key: identity if key == pid else None)
    service.start(pid, {})
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
    report = service.report(pid)
    finding = next(f for f in report['findings'] if f['lifecycle'] == 'current' and f['evidence'])
    content = load_content(output)
    body = {'input_id': report['input_id'], 'evidence_fingerprint': fingerprint(finding['evidence']),
            'page_version': content['version_id'], '_annotation_directory': str(output),
            '_page_snapshot': json.loads((output / 'checks-page.json').read_text())}
    yield service, pid, root, output, finding, body
    service.close()
    for context in service.papers.values():
        context['executor'].shutdown(wait=True)


def convert(service, pid, finding, body):
    return dispatch(service, pid, f"findings/{finding['finding_key']}/annotation", 'POST', {}, dict(body))[0]


def test_conversion_receipt_survives_reload_restart_and_knowledge_recheck(conversion, tmp_path):
    service, pid, root, output, finding, body = conversion
    before = (root / 'main.tex').read_bytes()
    first = convert(service, pid, finding, body)
    assert first['reused'] is False
    assert convert(service, pid, finding, body) == {**first, 'reused': True}
    # A real knowledge-only revision and recheck changes input identity, but
    # not exact source evidence. It must still reuse the original annotation.
    knowledge = service.knowledge(pid)
    service.knowledge(pid, {'registry': knowledge['registry'], 'expected_revision': knowledge['revision'],
                            'expected_hash': knowledge['content_hash'], 'expected_source': knowledge['source']})
    service.start(pid, {})
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
    changed_report = service.report(pid)
    assert changed_report['input_id'] != body['input_id']
    changed_body = {**body, 'input_id': changed_report['input_id']}
    assert convert(service, pid, finding, changed_body) == {**first, 'reused': True}
    restarted = CheckCoordinator(tmp_path / 'other-state', service.paper_lookup)
    try:
        restarted.start(pid, {})
        restarted._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
        latest = restarted.report(pid)
        again = convert(restarted, pid, finding, {**body, 'input_id': latest['input_id']})
        assert again == {**first, 'reused': True}
    finally:
        restarted.close()
        for context in restarted.papers.values():
            context['executor'].shutdown(wait=True)
    with AnnotationStore.open(load_content(output), root) as session:
        snapshot = session.read()
        assert len(snapshot['threads']) == 1
        thread = snapshot['threads'][0]
        assert thread['id'] == first['annotation_thread_id'] and not thread['resolved']
        assert thread['messages'][0]['author']['type'] == 'user'
        assert finding['evidence'][0]['quote'] in thread['messages'][0]['body']
    assert (root / 'main.tex').read_bytes() == before
    assert service.report(pid)['findings'][0]['lifecycle'] == 'current'
    assert all(f['review_state'] == 'unreviewed' for f in service.report(pid)['findings'])
    (root / 'main.tex').write_bytes(before.replace(b'\\ref{missing}', b'fixed'))
    with pytest.raises(CheckError) as error:
        convert(service, pid, finding, body)
    assert error.value.code in ('page_changed', 'page_input_conflict', 'stale_input')
    service.start(pid, {})
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
    assert next(f for f in service.report(pid)['findings'] if f['finding_key'] == finding['finding_key'])['lifecycle'] == 'resolved'


def test_conversion_rejects_unfresh_or_forged_proof_without_annotations(conversion, monkeypatch):
    service, pid, root, output, finding, body = conversion
    for changes in ({'evidence_fingerprint': 'bad'}, {'page_version': 'wrong'}, {'input_id': 'old'}, {'operation': 'suggestion_apply'}):
        with pytest.raises(CheckError):
            convert(service, pid, finding, {**body, **changes})
    report = service.report(pid)
    current = next(f for f in report['findings'] if f['finding_key'] == finding['finding_key'])
    monkeypatch.setattr(service, 'report', lambda *_: report)
    for lifecycle in ('resolved', 'stale', 'unknown'):
        current['lifecycle'] = lifecycle
        with pytest.raises(CheckError) as error:
            convert(service, pid, finding, body)
        assert error.value.code == 'finding_not_current'
    assert not (root / '.latex-review' / 'annotations').exists()


@pytest.mark.parametrize('operation', ['delete_thread', 'update_metadata'])
def test_deleted_or_retagged_linked_annotation_is_not_recreated(conversion, operation):
    service, pid, root, output, finding, body = conversion
    first = convert(service, pid, finding, body)
    content = load_content(output)
    with AnnotationStore.open(content, root) as session:
        snapshot = session.read()
        session.execute({'project_id': content['project_id'], 'version_id': content['version_id'], 'request': {
            'operation': operation, 'expected_revision': snapshot['revision'],
            'thread_id': first['annotation_thread_id'], **({'tags': []} if operation == 'update_metadata' else {})}})
        revision = session.metadata()['revision']
    with pytest.raises(CheckError) as error:
        convert(service, pid, finding, body)
    assert error.value.code == 'annotation_link_unavailable'
    with AnnotationStore.open(content, root) as session:
        assert session.metadata()['revision'] == revision
        assert len(session.read()['threads']) == (0 if operation == 'delete_thread' else 1)


def test_conversion_anchors_exact_include_instance(conversion):
    service, pid, root, output, _, _ = conversion
    (root / 'main.tex').write_text(r'\documentclass{article}\begin{document}\input{part}\par\input{part}\end{document}')
    (root / 'part.tex').write_text(r'Included \ref{missing}.')
    build(ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root))), output)
    service.start(pid, {})
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
    report = service.report(pid)
    finding = next(f for f in report['findings'] if f['lifecycle'] == 'current' and f['evidence'])
    content = load_content(output)
    body = {'input_id': report['input_id'], 'evidence_fingerprint': fingerprint(finding['evidence']),
            'page_version': content['version_id'], '_annotation_directory': str(output),
            '_page_snapshot': json.loads((output / 'checks-page.json').read_text())}
    linked = convert(service, pid, finding, body)
    with AnnotationStore.open(content, root) as session:
        note = next(t for t in session.read()['threads'] if t['id'] == linked['annotation_thread_id'])
    unit = next(u for u in content['units'] if u['id'] == note['association']['anchor']['unit_id'])
    document = service._paper(pid)['document']
    node = next(n for n in document.parsed.nodes if n.review.id == unit['node_id'])
    assert finding['evidence'][0]['include_instance'] in {
        e['include_instance'] for e in document.evidence(node.expanded_start, node.expanded_end)}


def test_browser_explicit_conversion_and_repeat_after_reload(conversion, monkeypatch):
    api = pytest.importorskip('playwright.sync_api')
    from latex_review.serve import create_server
    service, pid, root, output, finding, body = conversion
    before = (root / 'main.tex').read_bytes()
    forwarded = []
    def bridge(paper_id, suffix, payload, *, query=''):
        forwarded.append((suffix, payload))
        return dispatch(service, paper_id, suffix, 'GET' if payload is None else 'POST', parse_qs(query), payload)[0]
    monkeypatch.setattr('latex_review.local_client.checks_request', bridge)
    server = create_server(output, annotation_project_root=root)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            page.on('dialog', lambda dialog: dialog.accept())
            url = f'http://127.0.0.1:{server.server_port}/report.html?panel=checks'
            for _ in range(2):
                page.goto(url)
                page.locator('.workspace-findings button').first.click()
                page.get_by_role('button', name='转为批注（不解决问题）', exact=True).wait_for()
                page.wait_for_function('window.reviewAnnotations?.isReady()')
                page.get_by_role('button', name='转为批注（不解决问题）', exact=True).click()
                page.wait_for_function('!document.getElementById("workspace-reading").hidden')
                assert page.locator('.annotation-overview').get_attribute('open') is not None
            # A forged browser directory must be overwritten at the worker,
            # not forwarded or opened by the entry conversion helper.
            forged = page.evaluate('''async ({body,key})=>{const capability=await(await fetch('api/checks/status')).json();const response=await fetch(`api/checks/findings/${key}/annotation`,{method:'POST',headers:{'Content-Type':'application/json','X-LaTeX-Review-Token':capability.token,'X-LaTeX-Review-Paper-ID':capability.paper_id,'X-LaTeX-Review-Page-Version':body.page_version,'X-LaTeX-Review-Checks-Content-ID':body._page_snapshot.content_id},body:JSON.stringify({...body,_annotation_directory:'/nonexistent/browser-chosen'})});return {status:response.status,value:await response.json()};}''', {'body': body, 'key': finding['finding_key']})
            assert forged['status'] == 200 and forged['value']['reused']
            assert all(payload['_annotation_directory'] == str(output) for suffix, payload in forwarded if suffix.endswith('/annotation'))
            browser.close()
        with AnnotationStore.open(load_content(output), root) as session:
            assert len(session.read()['threads']) == 1
        assert (root / 'main.tex').read_bytes() == before
        assert next(f for f in service.report(pid)['findings'] if f['finding_key'] == finding['finding_key'])['lifecycle'] == 'current'
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
