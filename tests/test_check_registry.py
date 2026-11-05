"""Knowledge persistence failures must never overwrite author or source data."""
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path

import pytest

from latex_review.checks.registry import (
    KnowledgeConflict, KnowledgeCorrupt, KnowledgeSourceConflict,
    KnowledgeStore, KnowledgeValidationError, canonical_content_hash,
    empty_registry, root_identity, validate_registry,
)


def _store(tmp_path, **kwargs):
    root = tmp_path / 'paper'
    root.mkdir(exist_ok=True)
    return KnowledgeStore(root, tmp_path / 'state', **kwargs)


def _save(store, value=None):
    current = store.read()
    return store.write(value or current['registry'], expected_revision=current['revision'],
                       expected_hash=current['content_hash'])


def _race(root, state, revision, digest, name, event, results):
    store = KnowledgeStore(root, state)
    registry = empty_registry()
    registry['revision'] = revision
    registry['concepts'] = [{'id': name, 'canonical_name': name}]
    event.wait(10)
    try:
        store.write(registry, expected_revision=revision, expected_hash=digest)
        results.put(('ok', name))
    except KnowledgeConflict:
        results.put(('conflict', name))


def test_auto_activates_existing_sole_external_source(tmp_path):
    external = _store(tmp_path, source='external')
    value = empty_registry()
    value['concepts'] = [{'id': 'portable', 'canonical_name': 'Portable knowledge'}]
    saved = _save(external, value)
    automatic = _store(tmp_path).read()
    assert automatic['source'] == 'external'
    assert automatic['registry'] == saved['registry']
    assert not external.project_path.exists()


def test_noncooperating_manual_edit_during_backup_is_not_overwritten(tmp_path, monkeypatch):
    store = _store(tmp_path)
    original = _save(store)
    author_edit = deepcopy(original['registry'])
    author_edit['concepts'] = [{'id': 'author', 'canonical_name': 'Author manual save'}]
    atomic = store._atomic
    def delayed_backup(path, raw):
        atomic(path, raw)
        if path == store.backup_path:
            store.path.write_text(json.dumps(author_edit))
    monkeypatch.setattr(store, '_atomic', delayed_backup)
    with pytest.raises(KnowledgeConflict):
        store.write(original['registry'], expected_revision=original['revision'], expected_hash=original['content_hash'])
    assert store.read()['registry'] == author_edit


def test_read_is_side_effect_free_and_basic_empty_knowledge(tmp_path):
    store = _store(tmp_path)
    (store.root / 'main.tex').write_text('source remains intact')
    before = list(tmp_path.rglob('*'))
    result = store.read()
    assert result['status'] == 'missing'
    assert result['registry'] == empty_registry()
    assert result['content_hash'] == canonical_content_hash(empty_registry())
    assert list(tmp_path.rglob('*')) == before


def test_conditional_write_catches_manual_edit_without_revision_change(tmp_path):
    store = _store(tmp_path)
    original = _save(store)
    edited = deepcopy(original['registry'])
    edited['concepts'] = [{'id': 'mixing', 'canonical_name': 'Spectral mixing'}]
    store.path.write_text(json.dumps(edited))
    with pytest.raises(KnowledgeConflict):
        store.write(original['registry'], expected_revision=original['revision'], expected_hash=original['content_hash'])
    assert store.read()['registry'] == edited


def test_whitespace_key_order_and_unicode_have_canonical_hash(tmp_path):
    store = _store(tmp_path)
    value = empty_registry()
    value['concepts'] = [{'id': 'unicode', 'canonical_name': '混合 🧪'}]
    current = _save(store, value)
    store.path.write_text(json.dumps(current['registry'], ensure_ascii=True, separators=(',', ':')))
    assert store.read()['content_hash'] == current['content_hash']
    assert _save(store)['revision'] == 2


def test_stale_revision_and_submitted_revision_are_rejected(tmp_path):
    store = _store(tmp_path)
    previous = store.read()
    now = _save(store)
    with pytest.raises(KnowledgeConflict):
        store.write(previous['registry'], expected_revision=0, expected_hash=previous['content_hash'])
    with pytest.raises(KnowledgeConflict):
        store.write(previous['registry'], expected_revision=now['revision'], expected_hash=now['content_hash'])


def test_cross_process_updates_are_serialized(tmp_path):
    store = _store(tmp_path)
    current = _save(store)
    ctx = multiprocessing.get_context('spawn')
    event, results = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_race, args=(store.root, store.state_dir, current['revision'],
                                               current['content_hash'], name, event, results))
                 for name in ('first', 'second')]
    for process in processes:
        process.start()
    event.set()
    outcomes = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert sorted(status for status, _ in outcomes) == ['conflict', 'ok']
    winner = next(name for status, name in outcomes if status == 'ok')
    assert store.read()['registry']['concepts'][0]['id'] == winner
    assert store.read()['revision'] == 2


def test_read_only_storage_never_writes_project(tmp_path):
    store = _store(tmp_path, read_only=True)
    (store.root / 'main.tex').write_text('unchanged')
    store.root.chmod(0o555)
    try:
        result = _save(store)
        assert result['source'] == 'external'
        assert store.path == tmp_path / 'state' / 'knowledge' / root_identity(store.root) / 'knowledge.json'
        assert list(store.root.iterdir()) == [store.root / 'main.tex']
        assert (store.root / 'main.tex').read_text() == 'unchanged'
        with pytest.raises(KnowledgeValidationError):
            KnowledgeStore(store.root, store.state_dir, source='project', read_only=True)
    finally:
        store.root.chmod(0o755)


def test_root_only_identity_and_shared_entry_lock(tmp_path):
    store = _store(tmp_path)
    same_root = KnowledgeStore(store.root / '.', store.state_dir)
    other_root = tmp_path / 'second-paper'
    other_root.mkdir()
    other = KnowledgeStore(other_root, store.state_dir)
    assert root_identity(store.root) == root_identity(store.root / '.')
    assert store.lock_path == same_root.lock_path
    assert store.root_identity != other.root_identity
    from latex_review.identity import paper_identity
    main = paper_identity(store.root, 'main.tex')['paper_id']
    supplement = paper_identity(store.root, 'supplement.tex')['paper_id']
    assert main != supplement
    assert store.root_identity not in (main, supplement)


def test_sources_are_never_silently_merged(tmp_path):
    project = _store(tmp_path, source='project')
    external = _store(tmp_path, source='external')
    _save(project)
    external_value = empty_registry()
    external_value['concepts'] = [{'id': 'external', 'canonical_name': 'External'}]
    _save(external, external_value)
    with pytest.raises(KnowledgeSourceConflict):
        _store(tmp_path).read()
    assert project.read()['registry']['concepts'] == []
    assert external.read()['registry']['concepts'] == external_value['concepts']
    assert project.read()['alternatives'] == [str(external.path)]


def test_corrupt_read_preserves_bytes_and_write_requires_recovery(tmp_path):
    store = _store(tmp_path)
    _save(store)
    second = _save(store)
    corrupt = b'{"revision":2, broken\xff'
    store.path.write_bytes(corrupt)
    current = store.read()
    assert current['status'] == 'corrupt'
    assert current['registry'] == empty_registry()
    assert current['content_hash'].startswith('corrupt:')
    with pytest.raises(KnowledgeCorrupt):
        store.write(empty_registry(), expected_revision=0, expected_hash=current['content_hash'])
    assert store.path.read_bytes() == corrupt
    result = store.recover(expected_revision=0, expected_hash=current['content_hash'])
    assert result['status'] == 'ok'
    assert result['revision'] >= second['revision']
    preserved = list(store.path.parent.glob(store.path.name + '.corrupt.*'))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == corrupt
    assert store.backup_path.exists()


def test_recovery_without_valid_backup_preserves_corruption(tmp_path):
    store = _store(tmp_path)
    store.path.write_bytes(b'broken')
    current = store.read()
    with pytest.raises(KnowledgeCorrupt):
        store.recover(expected_revision=0, expected_hash=current['content_hash'])
    assert store.path.read_bytes() == b'broken'


@pytest.mark.parametrize('source', ['project', 'external'])
def test_recovery_rejects_backup_symlink_without_disclosing_private_notes(tmp_path, source):
    store = _store(tmp_path, source=source)
    _save(store)
    private_notes = tmp_path / 'private-notes.json'
    private = empty_registry()
    private['concepts'] = [{'id': 'private', 'canonical_name': 'SECRET PRIVATE AUTHOR NOTE'}]
    raw = json.dumps(private).encode()
    private_notes.write_bytes(raw)
    store.backup_path.symlink_to(private_notes)
    active = store.path.read_bytes()
    current = store.read()
    preflight_calls = []
    with pytest.raises(KnowledgeCorrupt, match='no valid backup') as error:
        store.recover(expected_revision=current['revision'], expected_hash=current['content_hash'], preflight=preflight_calls.append)
    assert 'SECRET PRIVATE AUTHOR NOTE' not in str(error.value)
    assert preflight_calls == []
    assert store.path.read_bytes() == active
    assert private_notes.read_bytes() == raw
    assert store.backup_path.is_symlink()
    assert not list(store.path.parent.glob(store.path.name + '.recovery.*'))
    assert not store.read()['registry']['concepts']


def test_recovery_rejects_backup_link_swapped_at_open(tmp_path, monkeypatch):
    store = _store(tmp_path)
    current = _save(store)
    private_notes = tmp_path / 'private-notes.json'
    private_notes.write_text(json.dumps(empty_registry()))
    import latex_review.checks.registry as module
    original_open = module.os.open
    intercepted = []
    def swap_at_open(path, flags, *args, **kwargs):
        if path == store.backup_path.name and kwargs.get('dir_fd') is not None:
            assert not store.backup_path.exists()
            store.backup_path.symlink_to(private_notes)
            intercepted.append(flags)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, 'open', swap_at_open)
    with pytest.raises(KnowledgeCorrupt, match='no valid backup'):
        store.recover(expected_revision=current['revision'], expected_hash=current['content_hash'])
    assert len(intercepted) == 1 and intercepted[0] & module.os.O_NOFOLLOW
    assert store.read()['registry'] == current['registry']


@pytest.mark.parametrize('kind', ['directory', 'fifo'])
def test_recovery_rejects_nonregular_backups_without_blocking(tmp_path, kind):
    import os
    store = _store(tmp_path)
    current = _save(store)
    if kind == 'directory':
        store.backup_path.mkdir()
    else:
        os.mkfifo(store.backup_path)
    with pytest.raises(KnowledgeCorrupt, match='no valid backup'):
        store.recover(expected_revision=current['revision'], expected_hash=current['content_hash'])
    assert store.read()['registry'] == current['registry']


def test_atomic_backup_write_replaces_symlink_not_outside_target(tmp_path):
    store = _store(tmp_path)
    current = _save(store)
    private_notes = tmp_path / 'private-notes.json'
    raw = b'private notes must not be overwritten'
    private_notes.write_bytes(raw)
    store.backup_path.symlink_to(private_notes)
    latest = _save(store)
    assert private_notes.read_bytes() == raw
    assert not store.backup_path.is_symlink()
    assert json.loads(store.backup_path.read_bytes()) == current['registry']
    assert not list(store.path.parent.glob('*.tmp'))
    recovered = store.recover(expected_revision=latest['revision'], expected_hash=latest['content_hash'])
    assert recovered['registry']['concepts'] == current['registry']['concepts']
    assert private_notes.read_bytes() == raw


def test_failed_atomic_replace_retains_previous_file(tmp_path, monkeypatch):
    store = _store(tmp_path)
    current = _save(store)
    import latex_review.checks.registry as module
    replace = module.os.replace
    def fail(source, target):
        if Path(target) == store.path:
            raise OSError('simulated crash before replace')
        return replace(source, target)
    monkeypatch.setattr(module.os, 'replace', fail)
    with pytest.raises(OSError, match='simulated crash'):
        _save(store)
    assert store.read()['registry'] == current['registry']
    assert json.loads(store.backup_path.read_bytes()) == current['registry']
    assert not list(store.path.parent.glob('*.tmp'))


def test_import_preview_conflicts_and_explicit_resolution(tmp_path):
    store = _store(tmp_path)
    value = empty_registry()
    value['concepts'] = [{'id': 'shared', 'canonical_name': 'Existing meaning'}]
    current = _save(store, value)
    incoming = deepcopy(value)
    incoming['concepts'][0]['canonical_name'] = 'Different meaning'
    incoming['concepts'].append({'id': 'new', 'canonical_name': 'New concept'})
    preview = store.preview_import(incoming)
    assert preview['conflicts'][0]['id'] == 'shared'
    assert preview['additions'] == [{'collection': 'concepts', 'id': 'new'}]
    assert store.read()['registry'] == current['registry']
    with pytest.raises(KnowledgeConflict):
        store.import_registry(incoming, expected_revision=current['revision'], expected_hash=current['content_hash'])
    result = store.import_registry(incoming, expected_revision=current['revision'], expected_hash=current['content_hash'],
                                   resolutions={'concepts/shared': 'current'})
    assert result['registry']['concepts'] == [value['concepts'][0], incoming['concepts'][1]]


def test_filesystem_readonly_root_uses_external_even_for_privileged_process(tmp_path):
    store = _store(tmp_path)
    store.root.chmod(0o555)
    try:
        external = KnowledgeStore(store.root, store.state_dir)
        assert external.read_only and external.source == 'external'
        saved = _save(external)
        assert saved['source'] == 'external'
        assert not store.project_path.exists()
        with pytest.raises(KnowledgeValidationError, match='read-only'):
            KnowledgeStore(store.root, store.state_dir, source='project')
        with pytest.raises(KnowledgeValidationError, match='read-only'):
            _save(store)  # Permissions changed after this store was constructed.
    finally:
        store.root.chmod(0o755)


def test_imported_deleted_decision_cannot_restore_foreign_confirmed_record(tmp_path):
    origin = _store(tmp_path)
    _decision(origin, {'canonical_name': 'Foreign root concept', 'status': 'confirmed'})
    deleted = _decision(origin, None)
    incoming = deleted['registry']
    destination_root = tmp_path / 'destination'
    destination_root.mkdir()
    destination = KnowledgeStore(destination_root, tmp_path / 'other-state')
    baseline = destination.read()
    imported = destination.import_registry(incoming, expected_revision=0, expected_hash=baseline['content_hash'])
    assert imported['registry']['decisions'][-1]['before'] == incoming['decisions'][-1]['before']
    assert all(item['imported'] for item in imported['registry']['decisions'])
    with pytest.raises(KnowledgeConflict, match='imported decision history'):
        destination.undo_decision(incoming['decisions'][-1]['id'], expected_revision=imported['revision'], expected_hash=imported['content_hash'])
    assert destination.read()['registry']['concepts'] == []
    assert not destination.preview_import(incoming)['conflicts']
    repeated = destination.import_registry(incoming, expected_revision=imported['revision'], expected_hash=imported['content_hash'])
    assert repeated['registry']['decisions'] == imported['registry']['decisions']


def _decision(store, changes, **kwargs):
    current = store.read()
    return store.decide('concepts', 'concept', changes, action='confirm', reason='Author verified', author='Alice',
                        evidence_fingerprint='evidence-v1', scope={'id': 'body', 'entry': 'main.tex'},
                        expected_revision=current['revision'], expected_hash=current['content_hash'], **kwargs)


@pytest.mark.parametrize('change', ['replace', 'remove', 'append'])
def test_generic_write_cannot_forge_or_replace_decision_history(tmp_path, change):
    store = _store(tmp_path)
    saved = _decision(store, {'canonical_name': 'Original'})
    proposed = deepcopy(saved['registry'])
    if change == 'replace':
        proposed['decisions'][0]['author'] = 'Forged author'
    elif change == 'remove':
        proposed['decisions'].clear()
    else:
        extra = deepcopy(proposed['decisions'][0])
        extra['id'] = 'forged-new-decision'
        proposed['decisions'].append(extra)
    with pytest.raises(KnowledgeConflict, match='audit history is immutable'):
        store.write(proposed, expected_revision=saved['revision'], expected_hash=saved['content_hash'])
    assert store.read()['registry'] == saved['registry']


def test_import_cannot_replace_existing_audit_record(tmp_path):
    store = _store(tmp_path)
    saved = _decision(store, {'canonical_name': 'Original'})
    proposed = deepcopy(saved['registry'])
    proposed['decisions'][0]['author'] = 'Different audit author'
    key = 'decisions/' + proposed['decisions'][0]['id']
    with pytest.raises(KnowledgeConflict, match='audit history cannot be replaced'):
        store.import_registry(proposed, expected_revision=saved['revision'], expected_hash=saved['content_hash'], resolutions={key: 'incoming'})
    assert store.read()['registry'] == saved['registry']


def test_undo_preflight_rejects_before_commit_and_cannot_modify_data(tmp_path):
    store = _store(tmp_path)
    _decision(store, {'canonical_name': 'Restorable'})
    saved = _decision(store, None)
    decision_id = saved['registry']['decisions'][-1]['id']
    def reject(registry):
        assert registry['concepts'][0]['canonical_name'] == 'Restorable'
        raise ValueError('restored evidence is stale')
    with pytest.raises(ValueError, match='restored evidence is stale'):
        store.undo_decision(decision_id, expected_revision=saved['revision'], expected_hash=saved['content_hash'], preflight=reject)
    assert store.read()['registry'] == saved['registry']
    accepted = store.undo_decision(decision_id, expected_revision=saved['revision'], expected_hash=saved['content_hash'], preflight=lambda registry: registry.clear())
    assert accepted['registry']['concepts'][0]['canonical_name'] == 'Restorable'


def test_recovery_preflight_rejects_before_file_changes_and_keeps_valid_backup_facts(tmp_path):
    store = _store(tmp_path)
    backup = _decision(store, {'canonical_name': 'Restorable', 'status': 'confirmed'})
    _decision(store, {'canonical_name': 'Latest'})
    store.path.write_bytes(b'corrupted author json')
    current = store.read()
    original_backup = store.backup_path.read_bytes()
    def reject(registry):
        assert registry['concepts'] == backup['registry']['concepts']
        raise ValueError('backup evidence is stale')
    with pytest.raises(ValueError, match='backup evidence is stale'):
        store.recover(expected_revision=0, expected_hash=current['content_hash'], preflight=reject)
    assert store.path.read_bytes() == b'corrupted author json'
    assert store.backup_path.read_bytes() == original_backup
    assert not list(store.path.parent.glob('*.corrupt.*'))
    recovered = store.recover(expected_revision=0, expected_hash=current['content_hash'], preflight=lambda registry: registry.clear())
    assert recovered['registry']['concepts'] == backup['registry']['concepts']
    assert recovered['registry']['concepts'][0]['status'] == 'confirmed'


def test_decisions_reversible_export_preserves_audit_and_exceptions(tmp_path):
    store = _store(tmp_path)
    current = _decision(store, {'canonical_name': 'First name', 'status': 'confirmed'}, request_id='request-1')
    first = current['registry']['decisions'][0]['id']
    second = _decision(store, {'canonical_name': 'Second name'})
    second_id = second['registry']['decisions'][-1]['id']
    with pytest.raises(KnowledgeConflict):
        store.undo_decision(first, expected_revision=second['revision'], expected_hash=second['content_hash'])
    undone = store.undo_decision(second_id, expected_revision=second['revision'], expected_hash=second['content_hash'])
    assert undone['registry']['concepts'][0]['canonical_name'] == 'First name'
    assert undone['registry']['decisions'][-1]['undoes'] == second_id
    assert store.export() == undone['registry']
    assert len(store.export()['decisions']) == 3
    with pytest.raises(KnowledgeConflict):
        store.undo_decision(second_id, expected_revision=undone['revision'], expected_hash=undone['content_hash'])
    undone_first = store.undo_decision(first, expected_revision=undone['revision'], expected_hash=undone['content_hash'])
    assert undone_first['registry']['concepts'] == []
    # Undo itself is reversible; nothing is deleted from the audit log.
    redo = store.undo_decision(undone_first['registry']['decisions'][-1]['id'],
                               expected_revision=undone_first['revision'], expected_hash=undone_first['content_hash'])
    assert redo['registry']['concepts'][0]['canonical_name'] == 'First name'


def test_exception_import_preserves_decisions_and_marks_anchors_stale(tmp_path):
    store = _store(tmp_path)
    original = _decision(store, {'canonical_name': 'Knowledge'})
    incoming = store.export()
    incoming['exceptions'] = [{'id': 'local-exception', 'rule_id': 'term.forbidden_name', 'reason': 'Implementation discussion',
                               'scope': {'id': 'implementation'}, 'valid_when': {'rule_version': '1'},
                               'invalid_when': ['rule_changed', 'evidence_changed']}]
    result = store.import_registry(incoming, expected_revision=original['revision'], expected_hash=original['content_hash'])
    assert result['registry']['decisions'] == incoming['decisions']
    assert result['registry']['exceptions'][0]['status'] == 'stale'
    exported = store.export()
    exported['decisions'].clear()
    assert store.export()['decisions'] == incoming['decisions']


@pytest.mark.parametrize('mutate', [
    lambda r: r.update(extra='unknown'),
    lambda r: r['concepts'].append({'id': 'x', 'canonical_name': 'Name', 'extra': True}),
    lambda r: r['concepts'].extend([{'id': 'x', 'canonical_name': 'A'}, {'id': 'x', 'canonical_name': 'B'}]),
    lambda r: r['occurrences'].append({'id': 'x', 'target_id': 'x', 'role': 'invented'}),
    lambda r: r.update(revision=-1),
])
def test_strict_schema_rejects_unknown_or_ambiguous_knowledge(mutate):
    value = empty_registry()
    mutate(value)
    with pytest.raises(KnowledgeValidationError):
        validate_registry(value)


def test_duplicate_json_keys_are_corrupt_not_last_write_wins(tmp_path):
    store = _store(tmp_path)
    raw = json.dumps(empty_registry()).replace('"revision": 0', '"revision": 0, "revision": 1')
    store.path.write_text(raw)
    assert store.read()['status'] == 'corrupt'
    assert store.path.read_text() == raw


def test_paths_cannot_write_tex_or_escape_read_only_boundary(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KnowledgeValidationError):
        KnowledgeStore(store.root, store.state_dir, path='main.tex')
    with pytest.raises(KnowledgeValidationError):
        KnowledgeStore(store.root, store.state_dir, source='external', path=store.root / 'knowledge.json')
    with pytest.raises(KnowledgeValidationError):
        KnowledgeStore(store.root, store.state_dir, source='project', path=tmp_path / 'knowledge.json')
    with pytest.raises(KnowledgeValidationError):
        KnowledgeStore(store.root, store.root / 'state', source='external')
