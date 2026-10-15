from copy import deepcopy
import pytest
from latex_review.version_storage import unpack_storage
from latex_review.annotations import AnnotationConflict, AnnotationError, AnnotationStore
from latex_review.annotation_audit import audit
from test_annotations import content_at, select, AUTHOR
from test_annotations import envelope, storage_path, stored_ids


def setup(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for review.')
    store = AnnotationStore.open(content)
    item = {'id': 'one', 'operation': 'create', 'selection': select(content, 'paragraph'),
            'body': 'Check this.', 'author': AUTHOR, 'category': 'question'}
    request = {'operation': 'batch', 'expected_revision': 0, 'request_id': 'test', 'items': [item]}
    return store, content, request


def test_partial_once_and_idempotency(tmp_path):
    store, content, request = setup(tmp_path)
    request['items'].append({**request['items'][0], 'id': 'bad', 'body': ''})
    response = store.execute(envelope(content, request))
    assert [r['ok'] for r in response['results']] == [True, False]
    persisted = storage_path(content).read_bytes()
    assert store.read()['revision'] == 1
    assert len(store.read()['threads']) == 1
    assert store.execute(envelope(content, request)) == response
    assert storage_path(content).read_bytes() == persisted
    retry = deepcopy(request)
    retry['expected_revision'] = 999
    assert store.execute(envelope(content, retry)) == response
    assert storage_path(content).read_bytes() == persisted
    retry['items'][0]['body'] = 'Changed'
    with pytest.raises(AnnotationConflict, match='ID'):
        store.execute(envelope(content, retry))
    assert 'requests' not in store.export()
    assert AnnotationStore.open(content).execute(envelope(content, request)) == response


def test_atomic_and_dry_run(tmp_path):
    store, content, request = setup(tmp_path)
    request['atomic'] = True
    request['items'].append({**request['items'][0], 'id': 'bad', 'body': ''})
    response = store.execute(envelope(content, request))
    assert not response['committed'] and all(not r['ok'] for r in response['results'])
    assert storage_path(content).read_bytes().startswith(b'SQLite format 3')
    assert store.read()['revision'] == 0 and not store.read()['threads']
    assert store.execute(envelope(content, request)) == response
    request['request_id'] = 'preflight'
    request['items'].pop()
    request['dry_run'] = True
    response = store.execute(envelope(content, request))
    assert response['results'][0]['ok'] and not response['committed']
    assert 'message_id' not in response['results'][0]
    assert 'preflight' not in stored_ids(content, 'requests')
    request['dry_run'] = False
    assert store.execute(envelope(content, request))['committed']


def test_reply_validation_and_audit(tmp_path):
    store, content, request = setup(tmp_path)
    before = store.read()
    created = store.execute(envelope(content, request))
    result = created['results'][0]
    item = {'id': 'reply', 'operation': 'reply', 'thread_id': result['thread_id'],
            'reply_to': result['message_id'], 'body': 'Evidence remains missing.', 'author': AUTHOR,
            'category': 'note'}
    request.update(request_id='reply', expected_revision=1, items=[item, {**item, 'id': 'bad', 'reply_to': 'missing'}])
    response = store.execute(envelope(content, request))
    assert [r['ok'] for r in response['results']] == [True, False]
    after = store.read()
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
        store.execute(envelope(content, request))
    request['items'].pop()
    request['expected_revision'] = 1
    with pytest.raises(AnnotationConflict):
        store.execute(envelope(content, request))


def test_all_failed_batch_keeps_revision_and_audit_detects_duplicate_replies(tmp_path):
    store, content, request = setup(tmp_path)
    request['items'][0]['body'] = ''
    rejected = store.execute(envelope(content, request))
    assert not rejected['committed'] and rejected['revision'] == 0
    assert store.execute(envelope(content, request)) == rejected
    request['items'][0]['body'] = 'Initial'
    request['request_id'] = 'new-request'
    created = store.execute(envelope(content, request))['results'][0]
    before = store.read()
    item = dict(operation='reply', thread_id=created['thread_id'], body='Same reply', author=AUTHOR, category='note')
    request.update(request_id='two-replies', expected_revision=before['revision'],
                   items=[dict(item, id='r1'), dict(item, id='r2')])
    response = store.execute(envelope(content, request))
    checked = audit(before, store.read(), response, expected_items=request['items'])
    assert not checked['ok'] and any(issue['code'] == 'duplicate_reply' for issue in checked['issues'])


def test_resolve_25_frozen_targets_and_audit_state(tmp_path):
    store, content, request = setup(tmp_path)
    template = request['items'][0]
    request['items'] = [{**template, 'id': f'create-{index}', 'tags': ['topic']} for index in range(25)]
    created = store.execute(envelope(content, request))
    before = store.inventory()
    targets = [result['thread_id'] for result in created['results']]
    batch = {'operation': 'batch', 'expected_revision': created['revision'], 'request_id': 'resolve-25', 'atomic': True,
             'items': [{'id': f'resolve-{index}', 'operation': 'resolve', 'thread_id': tid} for index, tid in enumerate(targets)]}
    assert all(result['ok'] for result in store.execute(envelope(content, {**batch, 'dry_run': True}))['results'])
    result = store.execute(envelope(content, batch))
    assert result['committed'] and result['preflight_reused']
    assert all(t['resolved'] for t in store.inventory()['threads'])
    assert audit(before, store.inventory(), result, expected_items=batch['items'])['ok']
    assert store.execute(envelope(content, batch)) == result
    reopened = {'operation': 'batch', 'expected_revision': result['revision'], 'request_id': 'metadata-reopen', 'atomic': True,
                'items': [{'id': 'reopen', 'operation': 'reopen', 'thread_id': targets[0]},
                          {'id': 'metadata', 'operation': 'update_metadata', 'thread_id': targets[0], 'title': 'Reviewed', 'tags': ['other']}]}
    updated = store.execute(envelope(content, reopened))
    verification = audit(before, store.inventory(), result, expected_items=batch['items'])
    assert any(issue['code'] == 'wrong_resolved_state' for issue in verification['issues'])
    assert audit(before, store.inventory(), updated, expected_items=reopened['items'])['ok']


def test_metadata_atomic_rollback_and_old_backup(tmp_path):
    store, content, request = setup(tmp_path)
    created = store.execute(envelope(content, request))
    tid = created['results'][0]['thread_id']
    bundle = store.export()
    assert 'title' not in bundle['threads'][tid] and 'tags' not in bundle['threads'][tid]
    batch = {'operation': 'batch', 'expected_revision': 1, 'request_id': 'metadata-bad', 'atomic': True,
             'items': [{'id': 'good', 'operation': 'update_metadata', 'thread_id': tid, 'tags': ['topic']},
                       {'id': 'bad', 'operation': 'update_metadata', 'thread_id': tid, 'tags': ['x', 'x']}]}
    result = store.execute(envelope(content, batch))
    assert not result['committed'] and store.export() == bundle
