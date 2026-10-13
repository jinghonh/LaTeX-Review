import errno
import base64
import gzip
import json
import re
from pathlib import Path

from latex_review.delivery import publication_html
from latex_review.report import _write_managed
from latex_review.resource_cache import deduplicate_resource
from test_annotation_delivery_browser import live_report


def test_local_report_and_portable_export(live_report):
    _, _, output, _ = live_report
    html = (output / 'report.html').read_text()
    assert not re.search(r'id="annotation-(content|backup|assets)"', html)
    portable = publication_html(output)
    for name in ('content', 'backup', 'assets'):
        value = re.search(rf'id="annotation-{name}">(.*?)</script>', portable, re.S)[1]
        assert isinstance(json.loads(value), dict)


def test_shared_resources_survive_report_replacement(tmp_path):
    first = tmp_path / '.latex-review' / 'first'
    second = first.parent / 'second'
    first.mkdir(parents=True)
    second.mkdir()
    relative = Path('assets/image.png')
    a = _write_managed(first, relative, content=b'original')
    b = _write_managed(second, relative, content=b'original')
    assert a.stat().st_ino == b.stat().st_ino
    _write_managed(first, relative, content=b'changed')
    assert a.read_bytes() == b'changed'
    assert b.read_bytes() == b'original'


def test_hardlink_failure_retains_resource(tmp_path, monkeypatch):
    path = tmp_path / '.latex-review' / 'assets' / 'image.png'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original')
    def unsupported(*args, **kwargs):
        raise OSError(errno.EXDEV, 'different filesystem')
    monkeypatch.setattr('latex_review.resource_cache.os.link', unsupported)
    assert not deduplicate_resource(path)
    assert path.read_bytes() == b'original'


def test_cache_symlink_is_not_followed(tmp_path):
    root = tmp_path / '.latex-review'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'resource-cache').symlink_to(outside, target_is_directory=True)
    path = root / 'image.png'
    path.write_bytes(b'original')
    assert not deduplicate_resource(path)
    assert list(outside.iterdir()) == []


def test_shared_publication_keeps_exact_backup_and_current_content(live_report):
    from latex_review.delivery import prepare_publication
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    original = publication_html(output, include_assets=False)
    extract = lambda html, name: json.loads(re.search(rf'id="annotation-{name}">(.*?)</script>', html, re.S)[1])
    backup = extract(original, 'backup')
    current = extract(original, 'content')
    compact = prepare_publication(output, 'paper').html.decode()
    envelope = extract(compact, 'backup-gzip')
    decoded = gzip.decompress(base64.b64decode(envelope['data']))
    assert len(decoded) == envelope['size']
    from latex_review.delivery import decode_annotation_backup
    assert decode_annotation_backup(envelope) == backup
    assert decode_annotation_backup(envelope)['versions'][extract(compact, 'current-version')] == current
    assert len(compact.encode()) < len(original.encode())


def test_compressed_backup_codec_rejects_damage():
    import pytest
    from latex_review.delivery import encode_annotation_backup, decode_annotation_backup, DeliveryError
    original = {'versions': {'historic': {'units': [{'text': '完整历史中文'}]}}, 'threads': {}}
    encoded = encode_annotation_backup(original)
    assert decode_annotation_backup(encoded) == original
    for damaged in (dict(encoded, size=encoded['size']-1), dict(encoded, data='broken'), dict(encoded, encoding='unknown')):
        with pytest.raises(DeliveryError, match='备份读取失败'):
            decode_annotation_backup(damaged)


def test_carried_compressed_backup_survives_reembedding(live_report):
    from latex_review.delivery import embed_annotations, prepare_publication
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    original = publication_html(output, include_assets=False)
    extract = lambda html, name: json.loads(re.search(rf'id="annotation-{name}">(.*?)</script>', html, re.S)[1])
    content = extract(original, 'content')
    packed = prepare_publication(output, 'paper').html.decode()
    restored = embed_annotations(packed, content, include_assets=False)
    assert extract(restored, 'backup') == extract(original, 'backup')
    assert 'id="annotation-backup-gzip"' not in restored
