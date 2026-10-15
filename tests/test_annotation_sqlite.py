"""Transactional/delta contracts for the bounded fast annotation path."""
from copy import deepcopy
import sqlite3
import json
import pytest
from latex_review.annotations import AnnotationStore, AnnotationConflict, AnnotationError
from latex_review.annotation_storage import Database
from test_annotations import content_at, select, AUTHOR


def setup(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for fast review.')
    store = AnnotationStore.for_content(content)
    request = dict(operation='create', expected_revision=0, request_id='single',
                   selection=select(content, 'paragraph'), body='Check', author=AUTHOR, category='note')
    return store, content, request


def test_single_receipt_atomic_and_only_changed_threads(tmp_path):
    store, content, request = setup(tmp_path)
    first = store.dispatch(content, request, incremental=True)
    second = store.dispatch(content, dict(request, expected_revision=1, request_id='second'), incremental=True)
    assert first['incremental'] and len(second['threads']) == 1
    assert second['start_revision'] == 1 and second['revision'] == 2
    assert store.dispatch(content, dict(request, expected_revision=999), incremental=True) == first
    assert store.metadata(content)['revision'] == 2
    with pytest.raises(AnnotationConflict):
        store.dispatch(content, dict(request, body='Changed'), incremental=True)
    deleted = store.dispatch(content, dict(operation='delete_thread', expected_revision=2,
        thread_id=second['result']['thread_id']), incremental=True)
    assert deleted['threads'] == [] and deleted['deleted_thread_ids'] == [second['result']['thread_id']]


def test_failed_write_rolls_back_receipt_and_revision(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    store.metadata(content)
    real = store._persist
    def failing(data):
        real(data)
        raise OSError('disk failure after SQL writes')
    monkeypatch.setattr(store, '_persist', failing)
    with pytest.raises(OSError):
        store.dispatch(content, request, incremental=True)
    assert store.metadata(content)['revision'] == 0 and not store.export()['threads']
    with store._locked() as data:
        assert 'single' not in data['requests']


def test_batch_delta_and_preflight_are_transactional(tmp_path):
    store, content, request = setup(tmp_path)
    item = {k:v for k,v in request.items() if k not in ('expected_revision','request_id')}
    batch = dict(operation='batch',request_id='batch',expected_revision=0,atomic=True,
                 items=[dict(item,id=str(i)) for i in range(10)])
    preview = store.batch(content, dict(batch,dry_run=True), incremental=True)
    result = store.batch(content, dict(batch,preflight_token=preview['preflight_token']), incremental=True)
    assert result['revision'] == 1 and result['start_revision'] == 0 and len(result['threads']) == 10
    assert result['preflight_reused'] and result['committed']
    assert store.batch(content,batch,incremental=True)==result
    assert store.metadata(content)['revision']==1
    oversized=dict(batch,request_id='large',expected_revision=1,items=[dict(item,id=str(i)) for i in range(101)])
    with pytest.raises(AnnotationError,match='100'):
        store.batch(content,oversized,incremental=True)


def test_migration_preserves_all_data_and_receipts(tmp_path):
    store, content, request = setup(tmp_path)
    receipt = store.dispatch(content,request,incremental=True)
    with store._locked() as data:
        legacy = deepcopy(data)
    store.path.unlink()
    from latex_review.version_storage import pack_storage
    store.legacy_path.write_text(json.dumps(pack_storage(legacy)))
    assert store.metadata(content)['revision']==1
    assert store.dispatch(content,request,incremental=True)==receipt
    assert store.export()=={k:v for k,v in legacy.items() if k!='requests'}
    assert not store.legacy_path.exists() and store.legacy_path.with_suffix('.json.migrated').exists()


def test_fast_submission_does_not_restore_historical_versions(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    store.dispatch(content,request,incremental=True)
    with store._locked() as data:
        for i in range(100):
            historical=deepcopy(content);historical['version_id']=f'{i:064x}'
            data['versions'][historical['version_id']]=historical
        store._persist(data)
    statements=[]
    connect=Database.connect
    def traced(self):
        connection=connect(self);connection.set_trace_callback(statements.append);return connection
    monkeypatch.setattr(Database,'connect',traced)
    store.dispatch(content,dict(request,request_id='new',expected_revision=1),incremental=True)
    assert not any('SELECT id FROM versions' in sql for sql in statements)
    assert sum('SELECT payload FROM versions WHERE' in sql for sql in statements)==1
    assert not any('SELECT id FROM threads' in sql for sql in statements)


def test_change_feed_tracks_deletions_and_cross_process_writes(tmp_path):
    store, content, request = setup(tmp_path)
    first=store.dispatch(content,request,incremental=True)
    other=AnnotationStore.for_content(content)
    second=other.dispatch(content,dict(request,request_id='second',expected_revision=1),incremental=True)
    deleted=store.dispatch(content,dict(operation='delete_thread',expected_revision=2,
        thread_id=first['result']['thread_id']),incremental=True)
    delta=other.changes(content,1)
    assert delta['start_revision']==1 and delta['revision']==3
    assert delta['deleted_thread_ids']==[first['result']['thread_id']]
    assert [t['id'] for t in delta['threads']]==[second['result']['thread_id']]
    assert not other.changes(content,3)['threads']


def test_simultaneous_writers_one_commits_other_conflicts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store,content,request=setup(tmp_path)
    store.metadata(content)
    barrier=Barrier(3)
    def write(i):
        barrier.wait()
        try:
            return AnnotationStore.for_content(content).dispatch(content,
                dict(request,request_id=f'writer-{i}'),incremental=True)['revision']
        except AnnotationConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(write,range(3)))
    assert results.count(1)==1 and results.count('conflict')==2
    assert store.metadata(content)['revision']==1 and len(store.export()['threads'])==1


def test_compressed_receipt_retry_preserves_exact_result(tmp_path):
    store,content,request=setup(tmp_path)
    request['body']='x'*12000
    first=store.dispatch(content,request,incremental=True)
    with sqlite3.connect(store.path) as connection:
        payload=connection.execute('SELECT payload FROM requests WHERE id=?',('single',)).fetchone()[0]
    assert isinstance(payload,bytes) and len(payload)<12000
    assert AnnotationStore.for_content(content).dispatch(content,request,incremental=True)==first
    assert store.metadata(content)['revision']==1
