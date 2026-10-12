import errno
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
