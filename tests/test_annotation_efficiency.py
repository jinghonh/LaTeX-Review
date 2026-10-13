"""Regression checks for compact reads, relocation caching and preflight reuse."""
import argparse
from copy import deepcopy

import pytest

from latex_review import annotations
from latex_review.annotation_output import add_read_options, read_projection
from latex_review.annotations import AnnotationConflict, AnnotationStore, locate, make_anchor
from test_annotations import AUTHOR, content_at, select, mutate
from test_annotation_batch import setup


def test_default_summary_and_case_insensitive_author(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'A paragraph for review.')
    store = AnnotationStore.for_content(content)
    state = mutate(store, content, 'create', selection=select(content, 'paragraph'),
                   body='Check this.', author={'name': 'Antigravity', 'type': 'agent'}, category='note')
    parser = argparse.ArgumentParser()
    add_read_options(parser)
    compact = read_projection(state, parser.parse_args(['--author', 'antigravity']))
    assert compact['total_matched'] == 1
    assert 'history' not in compact['threads'][0]
    assert 'original_anchor' not in compact['threads'][0]
    full = read_projection(state, parser.parse_args(['--full']))
    assert full['threads'] == state['threads']


def test_preflight_reused_across_store_instances_without_apply(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    request['dry_run'] = True
    preview = store.batch(content, request)
    assert preview['preflight_token']
    def unexpected(*args):
        pytest.fail('commit repeated preflight mutation')
    monkeypatch.setattr(AnnotationStore, '_apply', unexpected)
    request['dry_run'] = False
    result = AnnotationStore.for_content(content).batch(content, request)
    assert result['committed'] and result['preflight_reused']
    assert len(store.read(content)['threads']) == 1


@pytest.mark.parametrize('changed', ['revision', 'body', 'version', 'same_version_content'])
def test_stale_preflight_rejected(tmp_path, changed):
    store, content, request = setup(tmp_path)
    store.batch(content, {**request, 'dry_run': True})
    if changed == 'revision':
        mutate(store, content, 'create', selection=request['items'][0]['selection'],
               body='Other writer', author=AUTHOR, category='note')
        request['expected_revision'] = 1
    elif changed == 'body':
        request = deepcopy(request)
        request['items'][0]['body'] = 'Changed'
    elif changed == 'version':
        content = deepcopy(content)
        content['version_id'] = 'a' * 64
    else:
        content = deepcopy(content)
        content['units'][0]['text'] += ' Changed'
    with pytest.raises(AnnotationConflict, match='预检'):
        store.batch(content, request)


def test_read_caches_relocation_across_process_instances(tmp_path, monkeypatch):
    root = tmp_path / 'paper'
    old, _ = content_at(root, 'A paragraph for review.')
    store = AnnotationStore.for_content(old)
    mutate(store, old, 'create', selection=select(old, 'paragraph'), body='Check', author=AUTHOR, category='note')
    new, _ = content_at(root, 'A paragraph for careful review.')
    first = store.read(new)
    def unexpected(*args):
        pytest.fail('same-version read repeated fuzzy relocation')
    monkeypatch.setattr(annotations, 'locate', unexpected)
    second = AnnotationStore.for_content(new).read(new)
    assert second == first
    reconciled = mutate(store, new, 'reconcile')
    assert reconciled['result']['reconciled'] == 1
    assert mutate(store, new, 'reconcile')['revision'] == reconciled['revision']


def test_deleted_selection_keeps_container_without_replacement(tmp_path):
    root = tmp_path / 'paper'
    old, _ = content_at(root, 'A paragraph with obsolete words and enough surviving context for review.')
    anchor = make_anchor(old, select(old, 'obsolete words '))
    new, _ = content_at(root, 'A paragraph with and enough surviving context for review.')
    association = locate(new, anchor)
    assert association['status'] == 'needs_relocation' and association['anchor'] is None
    assert association['container']['unit_id'] == new['units'][0]['id']
    assert association['container']['selection_deleted'] is True


def test_ambiguous_containers_are_not_guessed(tmp_path):
    root = tmp_path / 'paper'
    old, _ = content_at(root, 'A paragraph with obsolete words and enough surviving context for review.')
    anchor = make_anchor(old, select(old, 'obsolete words '))
    new, _ = content_at(root, 'A paragraph with and enough surviving context for review.\n\n'
                            'A paragraph with and enough surviving context for review.')
    association = locate(new, anchor)
    assert 'container' not in association and association['anchor'] is None


def test_deleted_table_cell_keeps_labelled_table_navigation_and_backup(tmp_path):
    root = tmp_path / 'paper'
    body = r'\begin{table}\caption{Results}\label{tab:results}\begin{tabular}{cc}H & H\\ Obsolete & Surviving\\\end{tabular}\end{table}'
    old, _ = content_at(root, body)
    store = AnnotationStore.for_content(old)
    mutate(store, old, 'create', selection=select(old, 'Obsolete'), body='Check cell', author=AUTHOR, category='note')
    new, _ = content_at(root, body.replace('Obsolete', ''))
    association = mutate(store, new, 'reconcile')['threads'][0]['association']
    assert association['anchor'] is None
    assert association['container']['scope'] == 'table'
    assert association['container']['selection_deleted']
    backup = store.export()
    assert mutate(store, new, 'import', bundle=backup)['result']['duplicates'] == 1


def test_preflight_revision_content_drift_expiry_and_token(tmp_path, monkeypatch):
    from latex_review import annotation_preflight
    store, content, request = setup(tmp_path)
    preview = store.batch(content, {**request, 'dry_run': True})
    with pytest.raises(AnnotationConflict, match='预检'):
        store.batch(content, {**request, 'preflight_token': 'wrong-token'})
    monkeypatch.setattr(annotation_preflight.time, 'time', lambda: preview['preflight_expires_at'] + 1)
    with pytest.raises(AnnotationConflict, match='预检'):
        store.batch(content, request)


def test_repreflight_after_revision_conflict_and_preserves_other_writer(tmp_path):
    store, content, request = setup(tmp_path)
    store.batch(content, {**request, 'dry_run': True})
    mutate(store, content, 'create', selection=request['items'][0]['selection'],
           body='Other writer', author=AUTHOR, category='note')
    request['expected_revision'] = 1
    with pytest.raises(AnnotationConflict):
        store.batch(content, request)
    store.batch(content, {**request, 'dry_run': True})
    result = store.batch(content, request)
    assert result['preflight_reused'] and result['revision'] == 2
    assert len(store.read(content)['threads']) == 2


def test_cached_atomic_failure_does_not_write_successful_items(tmp_path):
    store, content, request = setup(tmp_path)
    request.update(atomic=True)
    request['items'].append({**request['items'][0], 'id': 'bad', 'body': ''})
    store.batch(content, {**request, 'dry_run': True})
    result = store.batch(content, request)
    assert result['preflight_reused'] and not result['committed']
    assert not store.read(content)['threads'] and store.metadata(content)['revision'] == 0


def test_batch_does_not_construct_location_view(tmp_path, monkeypatch):
    store, content, request = setup(tmp_path)
    def unexpected(*args, **kwargs):
        pytest.fail('batch constructed a full location view')
    monkeypatch.setattr(store, '_view', unexpected)
    assert store.batch(content, {**request, 'dry_run': True})['dry_run']
    assert store.batch(content, request)['committed']


@pytest.mark.parametrize('change,reason', [('revision', 'annotation_revision_changed'), ('body', 'payload_changed'),
                                         ('version', 'version_changed'), ('content', 'content_changed'),
                                         ('expiry', 'expired'), ('token', 'token_mismatch')])
def test_preflight_reports_actual_change(tmp_path, monkeypatch, change, reason):
    from latex_review import annotation_preflight
    store, content, request = setup(tmp_path)
    preview = store.batch(content, {**request, 'dry_run': True})
    if change == 'revision':
        mutate(store, content, 'create', selection=request['items'][0]['selection'], body='Other', author=AUTHOR, category='note')
    elif change == 'body':
        request['items'][0]['body'] = 'Changed'
    elif change == 'version':
        content = deepcopy(content)
        content['version_id'] = 'a' * 64
    elif change == 'content':
        content = deepcopy(content)
        content['units'][0]['text'] += ' Changed'
    elif change == 'expiry':
        monkeypatch.setattr(annotation_preflight.time, 'time', lambda: preview['preflight_expires_at'] + 1)
    else:
        request['preflight_token'] = 'wrong'
    with pytest.raises(AnnotationConflict) as caught:
        store.batch(content, request)
    detail = caught.value.details
    assert detail['code'] == 'preflight_stale' and reason in detail['reasons']
    assert detail['expected_revision'] == 0
    assert detail['actual_revision'] == (1 if change == 'revision' else 0)
    if change == 'version':
        assert 'payload_changed' not in detail['reasons']
    else:
        assert detail['reasons'] == [reason]
