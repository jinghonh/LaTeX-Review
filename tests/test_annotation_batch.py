from copy import deepcopy
import pytest
from latex_review.version_storage import unpack_storage
from latex_review.annotations import AnnotationConflict, AnnotationError, AnnotationStore
from latex_review.annotation_audit import audit
from test_annotations import content_at, select, AUTHOR


def setup(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for review.')
    store = AnnotationStore.for_content(content)
    item = {'id': 'one', 'operation': 'create', 'selection': select(content, 'paragraph'),
            'body': 'Check this.', 'author': AUTHOR, 'category': 'question'}
    request = {'operation': 'batch', 'expected_revision': 0, 'request_id': 'test', 'items': [item]}
    return store, content, request


def test_partial_once_and_idempotency(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    request['items'].append({**request['items'][0], 'id': 'bad', 'body': ''})
    calls = []
    write = store._write
    monkeypatch.setattr(store, '_write', lambda data: (calls.append(1), write(data)))
    response = store.batch(content, request)
    assert [r['ok'] for r in response['results']] == [True, False]
    assert calls == [1]
    assert store.batch(content, request) == response
    assert calls == [1]
    retry = deepcopy(request)
    retry['expected_revision'] = 999
    assert store.batch(content, retry) == response
    retry['items'][0]['body'] = 'Changed'
    with pytest.raises(AnnotationConflict, match='ID'):
        store.batch(content, retry)
    assert 'requests' not in store.export()
    assert AnnotationStore.for_content(content).batch(content, request) == response


def test_atomic_and_dry_run(tmp_path):
    store, content, request = setup(tmp_path)
    request['atomic'] = True
    request['items'].append({**request['items'][0], 'id': 'bad', 'body': ''})
    response = store.batch(content, request)
    assert not response['committed'] and all(not r['ok'] for r in response['results'])
    assert __import__('json').loads(store.path.read_text())['storage_encoding'] == 'shared-units-v1'
    assert store.read(content)['revision'] == 0 and not store.read(content)['threads']
    assert store.batch(content, request) == response
    request['request_id'] = 'preflight'
    request['items'].pop()
    request['dry_run'] = True
    response = store.batch(content, request)
    assert response['results'][0]['ok'] and not response['committed']
    assert 'message_id' not in response['results'][0]
    assert 'preflight' not in unpack_storage(__import__('json').loads(store.path.read_text()))['requests']
    request['dry_run'] = False
    assert store.batch(content, request)['committed']


def test_reply_validation_and_audit(tmp_path):
    store, content, request = setup(tmp_path)
    before = store.read(content)
    created = store.batch(content, request)
    result = created['results'][0]
    item = {'id': 'reply', 'operation': 'reply', 'thread_id': result['thread_id'],
            'reply_to': result['message_id'], 'body': 'Evidence remains missing.', 'author': AUTHOR,
            'category': 'note'}
    request.update(request_id='reply', expected_revision=1, items=[item, {**item, 'id': 'bad', 'reply_to': 'missing'}])
    response = store.batch(content, request)
    assert [r['ok'] for r in response['results']] == [True, False]
    after = store.read(content)
    assert not after['threads'][0]['resolved']
    report = audit(before, after, created)
    assert report['external_thread_ids'] == [] and len(report['external_messages']) == 1
    report = audit(after, after, response, expected_items=[item])
    assert report['ok']
    wrong = {**item, 'author': {'name': 'Other', 'type': 'agent'}}
    assert audit(after, after, response, expected_items=[wrong])['issues'][0]['code'] == 'wrong_author'
    missing = {**item, 'id': 'missing', 'thread_id': 'missing'}
    assert audit(after, after, response, expected_items=[missing])['issues'][0]['code'] == 'missing_thread'


def test_batch_envelope_validation(tmp_path):
    store, content, request = setup(tmp_path)
    request['items'].append(deepcopy(request['items'][0]))
    with pytest.raises(AnnotationError, match='重复'):
        store.batch(content, request)
    request['items'].pop()
    request['expected_revision'] = 1
    with pytest.raises(AnnotationConflict):
        store.batch(content, request)


def test_all_failed_batch_keeps_revision_and_audit_detects_duplicate_replies(tmp_path):
    store, content, request = setup(tmp_path)
    request['items'][0]['body'] = ''
    rejected = store.batch(content, request)
    assert not rejected['committed'] and rejected['revision'] == 0
    assert store.batch(content, request) == rejected
    request['items'][0]['body'] = 'Initial'
    request['request_id'] = 'new-request'
    created = store.batch(content, request)['results'][0]
    before = store.read(content)
    item = dict(operation='reply', thread_id=created['thread_id'], body='Same reply', author=AUTHOR, category='note')
    request.update(request_id='two-replies', expected_revision=before['revision'],
                   items=[dict(item, id='r1'), dict(item, id='r2')])
    response = store.batch(content, request)
    checked = audit(before, store.read(content), response, expected_items=request['items'])
    assert not checked['ok'] and any(issue['code'] == 'duplicate_reply' for issue in checked['issues'])
