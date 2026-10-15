"""Transactional/delta contracts for the bounded fast annotation path."""
from copy import deepcopy
import sqlite3
import json
import pytest
from latex_review.annotations import AnnotationStore, AnnotationConflict, AnnotationError, SCHEMA_VERSION
from latex_review.annotations._storage import Database
from test_annotations import content_at, select, AUTHOR
from test_annotations import envelope, storage_path, stored_ids


def database(content):
    return Database(storage_path(content), content['project_id'], SCHEMA_VERSION)


def setup(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for fast review.')
    store = AnnotationStore.open(content)
    request = dict(operation='create', expected_revision=0, request_id='single',
                   selection=select(content, 'paragraph'), body='Check', author=AUTHOR, category='note')
    return store, content, request


def test_single_receipt_atomic_and_only_changed_threads(tmp_path):
    store, content, request = setup(tmp_path)
    first = store.execute(envelope(content, request), incremental=True)
    second = store.execute(envelope(content, dict(request, expected_revision=1, request_id='second')), incremental=True)
    assert first['incremental'] and len(second['threads']) == 1
    assert second['start_revision'] == 1 and second['revision'] == 2
    assert store.execute(envelope(content, dict(request, expected_revision=999)), incremental=True) == first
    assert store.metadata()['revision'] == 2
    with pytest.raises(AnnotationConflict):
        store.execute(envelope(content, dict(request, body='Changed')), incremental=True)
    deleted = store.execute(envelope(content, dict(operation='delete_thread', expected_revision=2,
        thread_id=second['result']['thread_id'])), incremental=True)
    assert deleted['threads'] == [] and deleted['deleted_thread_ids'] == [second['result']['thread_id']]


def test_failed_write_rolls_back_receipt_and_revision(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    store.metadata()
    real = Database.persist
    def failing(data):
        real(data)
        raise OSError('disk failure after SQL writes')
    monkeypatch.setattr(Database, 'persist', staticmethod(failing))
    with pytest.raises(OSError):
        store.execute(envelope(content, request), incremental=True)
    assert store.metadata()['revision'] == 0 and not store.export()['threads']
    assert 'single' not in stored_ids(content, 'requests')


def test_batch_delta_and_preflight_are_transactional(tmp_path):
    store, content, request = setup(tmp_path)
    item = {k:v for k,v in request.items() if k not in ('expected_revision','request_id')}
    batch = dict(operation='batch',request_id='batch',expected_revision=0,atomic=True,
                 items=[dict(item,id=str(i)) for i in range(10)])
    preview = store.execute(envelope(content, dict(batch,dry_run=True)), incremental=True)
    result = store.execute(envelope(content, dict(batch,preflight_token=preview['preflight_token'])), incremental=True)
    assert result['revision'] == 1 and result['start_revision'] == 0 and len(result['threads']) == 10
    assert result['preflight_reused'] and result['committed']
    assert store.execute(envelope(content, batch), incremental=True)==result
    assert store.metadata()['revision']==1
    oversized=dict(batch,request_id='large',expected_revision=1,items=[dict(item,id=str(i)) for i in range(101)])
    with pytest.raises(AnnotationError,match='100'):
        store.execute(envelope(content, oversized), incremental=True)


def test_migration_preserves_all_data_and_receipts(tmp_path):
    store, content, request = setup(tmp_path)
    receipt = store.execute(envelope(content, request), incremental=True)
    with database(content).transaction() as data:
        legacy = deepcopy(data)
    storage_path(content).unlink()
    from latex_review.version_storage import pack_storage
    storage_path(content).with_suffix('.json').write_text(json.dumps(pack_storage(legacy)))
    assert store.metadata()['revision']==1
    assert store.execute(envelope(content, request), incremental=True)==receipt
    assert store.export()=={k:v for k,v in legacy.items() if k!='requests'}
    assert not storage_path(content).with_suffix('.json').exists() and storage_path(content).with_suffix('.json').with_suffix('.json.migrated').exists()


def test_fast_submission_does_not_restore_historical_versions(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    store.execute(envelope(content, request), incremental=True)
    with database(content).transaction() as data:
        for i in range(100):
            historical=deepcopy(content);historical['version_id']=f'{i:064x}'
            data['versions'][historical['version_id']]=historical
        Database.persist(data)
    statements=[]
    connect=Database.connect
    def traced(self):
        connection=connect(self);connection.set_trace_callback(statements.append);return connection
    monkeypatch.setattr(Database,'connect',traced)
    store.execute(envelope(content, dict(request,request_id='new',expected_revision=1)), incremental=True)
    assert not any('SELECT id FROM versions' in sql for sql in statements)
    assert sum('SELECT payload FROM versions WHERE' in sql for sql in statements)==1
    assert not any('SELECT id FROM threads' in sql for sql in statements)


def test_change_feed_tracks_deletions_and_cross_process_writes(tmp_path):
    store, content, request = setup(tmp_path)
    first=store.execute(envelope(content, request), incremental=True)
    other=AnnotationStore.open(content)
    second=other.execute(envelope(content, dict(request,request_id='second',expected_revision=1)),incremental=True)
    deleted=store.execute(envelope(content, dict(operation='delete_thread',expected_revision=2,
        thread_id=first['result']['thread_id'])), incremental=True)
    delta=other.changes(1)
    assert delta['start_revision']==1 and delta['revision']==3
    assert delta['deleted_thread_ids']==[first['result']['thread_id']]
    assert [t['id'] for t in delta['threads']]==[second['result']['thread_id']]
    assert not other.changes(3)['threads']


def test_simultaneous_writers_one_commits_other_conflicts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store, content, request = setup(tmp_path)
    store.metadata()
    barrier=Barrier(3)
    def write(i):
        barrier.wait()
        try:
            return AnnotationStore.open(content).execute(envelope(content,
                dict(request,request_id=f'writer-{i}')),incremental=True)['revision']
        except AnnotationConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(write,range(3)))
    assert results.count(1)==1 and results.count('conflict')==2
    assert store.metadata()['revision']==1 and len(store.export()['threads'])==1


def test_compressed_receipt_retry_preserves_exact_result(tmp_path):
    store, content, request = setup(tmp_path)
    request['body']='x'*12000
    first=store.execute(envelope(content, request),incremental=True)
    with sqlite3.connect(storage_path(content)) as connection:
        payload=connection.execute('SELECT payload FROM requests WHERE id=?',('single',)).fetchone()[0]
    assert isinstance(payload,bytes) and len(payload)<12000
    assert AnnotationStore.open(content).execute(envelope(content, request),incremental=True)==first
    assert store.metadata()['revision']==1
