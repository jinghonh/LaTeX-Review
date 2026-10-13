"""Incremental transfer, legacy compatibility and immutable release resources."""
import io
import json
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from latex_review import delivery, upload
from test_upload import report, server, publish
from test_annotation_delivery_browser import live_report


def prepared(tmp_path, text='first'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = report(tmp_path)
    (output / 'report.html').write_text('<html><head></head><body>' + text + '<img src="assets/new/chart.png"></body></html>')
    (output / 'assets/new/duplicate.png').write_bytes(b'chart')
    html = (output / 'report.html').read_text().replace('</body>', '<img src="assets/new/duplicate.png"></body>')
    (output / 'report.html').write_text(html)
    return delivery.prepare_publication(output, 'test-paper')


def incremental(root, publication, missing=None):
    if missing is None:
        missing = set(publish.query_resources(root, publication.manifest)['missing'])
    stream = io.BytesIO()
    stats = delivery.write_archive(publication, stream, missing=missing)
    stream.seek(0)
    receipt = publish.receive(root, stream, incremental=True)
    return stats, receipt


def test_incremental_upload_reuses_hash_across_paths_and_releases(tmp_path):
    root = server(tmp_path)
    first = prepared(tmp_path / 'first')
    stats, _ = incremental(root, first)
    assert stats == {'resource_files_sent': 1, 'resource_bytes_sent': 5}
    latest = root / 'site/test-paper/latest'
    a = latest / 'assets/new/chart.png'
    b = latest / 'assets/new/duplicate.png'
    inode = a.stat().st_ino
    assert b.stat().st_ino == inode
    second = prepared(tmp_path / 'second', 'second')
    stats, receipt = incremental(root, second)
    assert stats == {'resource_files_sent': 0, 'resource_bytes_sent': 0}
    assert receipt['resource_files_received'] == 0
    assert (latest / 'assets/new/chart.png').stat().st_ino == inode
    assert 'second' in (latest / 'report.html').read_text()


def test_missing_after_query_does_not_replace_latest(tmp_path):
    root = server(tmp_path)
    first = prepared(tmp_path / 'first')
    incremental(root, first)
    before = (root / 'site/test-paper/latest').readlink()
    second = prepared(tmp_path / 'second')
    assert publish.query_resources(root, second.manifest)['missing'] == []
    digest = next(iter(second.manifest['resources'].values()))['sha256']
    (root / 'resources/test-paper' / digest).unlink()
    with pytest.raises(publish.MissingResources) as failure:
        incremental(root, second, set())
    assert failure.value.receipt['missing'] == [digest]
    assert (root / 'site/test-paper/latest').readlink() == before
    assert (root / 'site/test-paper/latest/assets/new/chart.png').read_bytes() == b'chart'
    incremental(root, second, {digest})


def test_manifest_rejects_corruption_and_complete_size_limit(tmp_path, monkeypatch):
    root = server(tmp_path)
    publication = prepared(tmp_path / 'first')
    monkeypatch.setattr(publish, 'MAX_BYTES', publication.manifest['report_size'] + 9)
    with pytest.raises(ValueError, match='完整发布'):
        publish.query_resources(root, publication.manifest)
    monkeypatch.setattr(publish, 'MAX_BYTES', 200 * 1024 * 1024)
    publication.resources['assets/new/chart.png'] = b'wrong'
    with pytest.raises(ValueError, match='内容哈希'):
        incremental(root, publication)
    assert not (root / 'site/test-paper/latest').exists()


def test_legacy_receive_and_history_migration_preserve_content(tmp_path):
    root = server(tmp_path)
    publication = prepared(tmp_path / 'first')
    stream = io.BytesIO()
    delivery.write_archive(publication, stream)
    stream.seek(0)
    publish.receive(root, stream)
    latest = root / 'site/test-paper/latest'
    old_html = (latest / 'report.html').read_bytes()
    image = latest / 'assets/new/chart.png'
    temporary = image.with_suffix('.tmp')
    temporary.write_bytes(image.read_bytes())
    os.replace(temporary, image)
    assert image.stat().st_ino != (latest / 'assets/new/duplicate.png').stat().st_ino
    publish.deduplicate_existing(root)
    assert image.stat().st_ino == (latest / 'assets/new/duplicate.png').stat().st_ino
    assert (latest / 'report.html').read_bytes() == old_html


def test_client_incremental_retry_and_legacy_fallback(tmp_path, monkeypatch, capsys):
    root = server(tmp_path)
    (tmp_path / 'source').mkdir()
    output = report(tmp_path / 'source')
    monkeypatch.setattr(upload, 'load_upload_config', lambda _: ('host', 'https://example.org', 'test-paper'))
    calls = []
    def ssh(host, command, **kwargs):
        calls.append(command)
        if command == 'resource-query-v1':
            manifest = json.loads(kwargs['input'])
            result = publish.query_resources(root, manifest)
            # Simulate resources disappearing after the query.
            result['missing'] = []
            return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode(), stderr=b'')
        try:
            result = publish.receive(root, kwargs['stdin'], incremental=command == 'publish-incremental-v1')
        except publish.MissingResources as exc:
            return SimpleNamespace(returncode=2, stdout=json.dumps(exc.receipt).encode(), stderr=b'')
        return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode(), stderr=b'')
    monkeypatch.setattr(upload, '_ssh', ssh)
    assert upload.upload_report(output, output / 'main.tex').endswith('/test-paper/latest/report.html')
    assert calls == ['resource-query-v1', 'publish-incremental-v1', 'publish-incremental-v1']
    assert '5 字节' in capsys.readouterr().err
    calls.clear()
    def legacy(host, command, **kwargs):
        if command == 'resource-query-v1':
            calls.append(command)
            return SimpleNamespace(returncode=1, stdout=b'', stderr='此账号仅允许发布报告\n'.encode())
        return ssh(host, command, **kwargs)
    monkeypatch.setattr(upload, '_ssh', legacy)
    upload.upload_report(output, output / 'main.tex')
    assert calls == ['resource-query-v1', 'publish']


def test_query_network_failure_never_falls_back(tmp_path, monkeypatch):
    output = report(tmp_path)
    monkeypatch.setattr(upload, 'load_upload_config', lambda _: ('host', 'https://example.org', 'test-paper'))
    calls = []
    def failed(host, command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=255, stdout=b'', stderr=b'connection refused')
    monkeypatch.setattr(upload, '_ssh', failed)
    with pytest.raises(upload.UploadError, match='资源查询失败'):
        upload.upload_report(output, output / 'main.tex')
    assert calls == ['resource-query-v1']


def test_published_html_keeps_annotations_without_embedded_assets(live_report):
    _, _, output, _ = live_report
    (output / 'diff.json').write_text('{}')
    publication = delivery.prepare_publication(output, 'test-paper')
    html = publication.html.decode()
    assert 'id="annotation-content"' not in html and 'id="annotation-backup-gzip"' in html
    assert 'id="annotation-current-version"' in html
    assert 'id="annotation-assets"' not in html
    assert 'name="latex-review-publication" content="static"' in html
    assert '#translation-export{display:none!important}' in html


def test_retention_collects_only_unreferenced_resources(tmp_path):
    root = server(tmp_path)
    digests = []
    for index in range(12):
        base = tmp_path / str(index)
        base.mkdir()
        output = report(base)
        (output / 'assets/new/chart.png').write_bytes(f'image {index}'.encode())
        publication = delivery.prepare_publication(output, 'test-paper')
        digests.append(publication.manifest['resources']['assets/new/chart.png']['sha256'])
        incremental(root, publication)
    assert {p.name for p in (root / 'resources/test-paper').iterdir()} == set(digests[-10:])
    releases = list((root / 'site/test-paper/releases').iterdir())
    assert len(releases) == 10
    assert all((p / 'assets/new/chart.png').is_file() for p in releases)
    assert not list((root / 'site').rglob('*.resource-link'))


@pytest.mark.parametrize('bad', ['wrong_slug', 'wrong_manifest', 'unknown_hash'])
def test_client_rejects_untrusted_query_receipts(tmp_path, monkeypatch, bad):
    output = report(tmp_path)
    monkeypatch.setattr(upload, 'load_upload_config', lambda _: ('host', 'https://example.org', 'test-paper'))
    calls = []
    def query(host, command, **kwargs):
        calls.append(command)
        manifest = json.loads(kwargs['input'])
        receipt = {'protocol': 1, 'slug': manifest['slug'], 'manifest_id': delivery.manifest_id(manifest), 'missing': []}
        if bad == 'wrong_slug':
            receipt['slug'] = 'other'
        elif bad == 'wrong_manifest':
            receipt['manifest_id'] = '0' * 64
        else:
            receipt['missing'] = ['0' * 64]
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt).encode(), stderr=b'')
    monkeypatch.setattr(upload, '_ssh', query)
    with pytest.raises(upload.UploadError):
        upload.upload_report(output, output / 'main.tex')
    assert calls == ['resource-query-v1']
