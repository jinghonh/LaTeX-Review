"""HTTP progress must remain responsive while a mutation holds the project lock."""
import json
from threading import Event, Thread
from urllib.request import Request, urlopen

from latex_review.annotations import AnnotationStore, MANIFEST
from latex_review.serve import create_server
from test_annotations import content_at, select, AUTHOR


def test_async_progress_does_not_wait_for_storage_lock(tmp_path, monkeypatch):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for review.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'report.html').write_text('<h1>Review</h1>')
    (report / MANIFEST).write_text(json.dumps(content))
    entered, release = Event(), Event()
    original = AnnotationStore._apply
    def delayed(store, data, content, request):
        entered.set()
        assert release.wait(5)
        return original(store, data, content, request)
    monkeypatch.setattr(AnnotationStore, '_apply', delayed)
    with create_server(report) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            envelope = {'project_id': content['project_id'], 'version_id': content['version_id'],
                        'request': {'operation': 'create', 'expected_revision': 0,
                                    'selection': select(content, 'paragraph'), 'body': 'Check',
                                    'author': AUTHOR, 'category': 'note'}}
            request = Request(base + '/api/annotations/update?async=1', json.dumps(envelope).encode(),
                              {'Content-Type': 'application/json', 'X-LaTeX-Review-Token': server.annotation_token})
            with urlopen(request, timeout=2) as response:
                assert response.status == 202
                job = json.load(response)
            assert entered.wait(2), server.annotation_jobs.snapshot(job['task_id'])
            state = json.load(urlopen(base + job['status_url'], timeout=1))
            assert state['state'] == 'running' and state['phase'] == 'apply'
            assert json.load(urlopen(base + '/api/annotations/jobs', timeout=1))['jobs'][0]['task_id'] == job['task_id']
            release.set()
            # Wait for the worker and retain the first receipt.
            server.annotation_jobs.executor.shutdown(wait=True)
            state = json.load(urlopen(base + job['status_url'], timeout=1))
            assert state['state'] == 'complete' and state['result']['revision'] == 1
            compact = json.load(urlopen(base + '/api/annotations/status'))
            assert 'history' not in compact['threads'][0]
            full = json.load(urlopen(base + '/api/annotations/status?view=full'))
            assert 'history' in full['threads'][0]
            metadata = json.load(urlopen(base + '/api/annotations/status?view=revision'))
            assert 'threads' not in metadata and metadata['revision'] == 1
        finally:
            release.set()
            server.shutdown()
            thread.join()


def test_async_conflict_retains_error_and_does_not_write(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for review.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'report.html').write_text('<h1>Review</h1>')
    (report / MANIFEST).write_text(json.dumps(content))
    with create_server(report) as server:
        from latex_review.annotations import execute_request
        store = AnnotationStore.for_content(content)
        envelope = {'project_id': content['project_id'], 'version_id': '0' * 64, 'request': {}}
        job = server.annotation_jobs.submit(lambda progress: execute_request(store, content, envelope, progress))
        server.annotation_jobs.executor.shutdown(wait=True)
        result = server.annotation_jobs.snapshot(job['task_id'])
        assert result['state'] == 'failed' and result['error'] == 'conflict'
        assert store.metadata(content)['revision'] == 0
