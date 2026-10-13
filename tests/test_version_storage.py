from copy import deepcopy
import json

import pytest

from latex_review.version_storage import pack_versions, unpack_versions, pack_storage, unpack_storage
from latex_review.annotations import AnnotationStore, AnnotationError, AnnotationConflict
from test_annotations import content_at, mutate, select, AUTHOR


def test_shared_units_round_trip_and_independent_coordinates():
    unit = {'text': '正文😀', 'source': {'line': 1}, 'atoms': []}
    moved = dict(unit, source={'line': 99})
    original = {'versions': {'a': {'units': [unit, moved]}, 'b': {'units': [unit]}}, 'threads': {}, 'requests': {'x': 1}}
    packed = pack_versions(original)
    assert len(packed['units']) == 2
    restored = unpack_versions(packed)
    assert restored == original
    restored['versions']['a']['units'][0]['source']['line'] = 20
    assert restored['versions']['b']['units'][0]['source']['line'] == 1
    assert unpack_storage(pack_storage(original)) == original
    assert unpack_storage(original) == original


@pytest.mark.parametrize('reference', [-1, 1, True, 0.5, '0', {'baseline': 'a'}, ['a']])
def test_invalid_missing_boundary_and_cycle_reference(reference):
    packed = pack_versions({'versions': {'a': {'units': [{'text': 'x'}]}}})
    packed['bundle']['versions']['a']['units'] = [reference]
    with pytest.raises(ValueError, match='引用无效'):
        unpack_versions(packed)


def test_corrupt_pool_and_storage_checksum():
    packed = pack_versions({'versions': {'a': {'units': [{'text': 'x'}]}}})
    del packed['units']
    with pytest.raises(ValueError):
        unpack_versions(packed)
    stored = pack_storage({'versions': {'a': {'units': [{'text': 'x'}]}}})
    stored['data']['units'][0]['text'] = 'silent corruption'
    with pytest.raises(ValueError, match='校验失败'):
        unpack_storage(stored)


def test_old_storage_compaction_preserves_revision_history_and_conflicts(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'Original paragraph.')
    store = AnnotationStore.for_content(content)
    mutate(store, content, 'create', selection=select(content, 'Original'), body='Keep history', author=AUTHOR, category='note')
    bundle = store.export()
    # Simulate a pre-upgrade file, then explicitly migrate it.
    store.path.write_text(json.dumps(bundle))
    store.compact()
    assert store.export() == bundle
    assert store.metadata(content)['revision'] == bundle['revision']
    assert len(store.inventory(content)['threads']) == 1
    assert 'storage_encoding' in json.loads(store.path.read_text())
    changed = deepcopy(content)
    changed['units'][0]['source']['start_line'] = 99
    changed['units'][0]['source']['end_line'] = 99
    with pytest.raises(AnnotationConflict):
        store.read(changed)
    before = store.path.read_bytes()
    broken = json.loads(before)
    broken['data']['units'][0]['text'] = 'damage'
    store.path.write_text(json.dumps(broken))
    damaged = store.path.read_bytes()
    with pytest.raises(AnnotationError, match='存储读取失败'):
        store.compact()
    with pytest.raises(AnnotationError, match='存储读取失败'):
        store.metadata(content)
    assert store.path.read_bytes() == damaged
    store.path.write_bytes(before)
    assert store.export() == bundle


def test_shared_restore_rejects_expansion_limit():
    packed = {'encoding': 'shared-units-v1', 'units': [{'text': 'x' * (1024 * 1024)}],
              'bundle': {'versions': {'a': {'units': [0] * 201}}}}
    with pytest.raises(ValueError, match='200 MiB'):
        unpack_versions(packed)


def test_old_gzip_complete_backup_is_readable():
    import gzip
    import base64
    from latex_review.delivery import decode_annotation_backup
    bundle = {'versions': {'a': {'units': []}}, 'threads': {}}
    raw = json.dumps(bundle).encode()
    envelope = {'encoding': 'gzip-base64-v1', 'size': len(raw), 'data': base64.b64encode(gzip.compress(raw)).decode()}
    assert decode_annotation_backup(envelope) == bundle
