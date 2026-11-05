"""Status observation caches validated identity by manifest bytes, not metadata."""
import cProfile
import json
import os
from types import SimpleNamespace

import pytest

from latex_review.annotations import MANIFEST, AnnotationError
from latex_review.annotations import content as content_module
from latex_review.checks.http import _status_content_identity


def manifest(version='a', **changes):
    return {'schema_version': '1.0', 'project_id': 'b' * 64, 'entry': 'main.tex',
            'version_id': version * 64,
            'coordinate_system': 'utf16-normalized-whitespace-with-formula-atoms',
            'units': [], **changes}


def test_unchanged_manifest_validates_once_and_caller_cannot_mutate_cache(tmp_path):
    path = tmp_path / MANIFEST
    path.write_text(json.dumps(manifest()))
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    with content_module._VALIDATION_RECEIPT_LOCK:
        content_module._VALIDATION_RECEIPTS.clear()
    with cProfile.Profile() as profile:
        first = _status_content_identity(handler)
        first['entry'] = 'forged.tex'
        assert _status_content_identity(handler) == {'entry': 'main.tex', 'version_id': 'a' * 64}
    calls = sum(entry.callcount for entry in profile.getstats()
                if entry.code is content_module.validate_content.__code__)
    assert calls == 1


def test_same_size_same_mtime_edit_and_invalid_units_cannot_reuse_identity(tmp_path):
    path = tmp_path / MANIFEST
    path.write_text(json.dumps(manifest()))
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    assert _status_content_identity(handler)['version_id'] == 'a' * 64
    stat = path.stat()
    path.write_text(json.dumps(manifest('c')))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert _status_content_identity(handler)['version_id'] == 'c' * 64
    path.write_text(json.dumps(manifest('c', units=[{'invalid': True}])))
    with pytest.raises(AnnotationError):
        _status_content_identity(handler)


def test_manifest_identity_is_scoped_to_owned_server_and_raw_json(tmp_path):
    path = tmp_path / MANIFEST
    path.write_text(json.dumps(manifest()))
    first = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    assert _status_content_identity(first)['entry'] == 'main.tex'
    second = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    path.write_text('{broken JSON')
    with pytest.raises(ValueError):
        _status_content_identity(second)
    with pytest.raises(ValueError):
        _status_content_identity(first)
