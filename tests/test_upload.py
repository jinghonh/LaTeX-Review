import io
import json
from pathlib import Path
import sys
import tarfile

import pytest
from latex_review.upload import UploadError, build_archive, load_upload_config
from latex_review import cli, serve, upload

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy'
sys.path.insert(0, str(DEPLOY))
import publish


def report(tmp_path):
    directory = tmp_path / 'report'
    directory.mkdir()
    (directory / 'report.html').write_text('<html><img src="assets/new/chart.png"><script type="application/json" id="translation-results">{}</script></html>')
    (directory / 'assets/new').mkdir(parents=True)
    (directory / 'assets/new/chart.png').write_bytes(b'chart')
    (directory / 'diff.json').write_text(json.dumps({'old': {'kind': 'git', 'identifier': 'a' * 40}, 'new': {'kind': 'worktree', 'identifier': 'working-tree'}}))
    (directory / 'diagnostics.json').write_text('private diagnostics')
    (directory / 'main.tex').write_text('private source')
    (directory / 'original.pdf').write_bytes(b'private PDF')
    return directory


def server(tmp_path):
    root = tmp_path / 'server'
    root.mkdir()
    (root / 'papers.json').write_text(json.dumps({'test-paper': 'Test paper', 'other': 'Other'}))
    return root


def test_archive_contains_only_referenced_resources(tmp_path):
    directory = report(tmp_path)
    stream = io.BytesIO()
    release = build_archive(directory, 'test-paper', stream)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        assert set(archive.getnames()) == {'report.html', 'release.json', 'assets/new/chart.png'}
        metadata = json.load(archive.extractfile('release.json'))
        assert metadata['release'] == release
        assert metadata['comparison'] == 'aaaaaaaaaaaa → 工作区'


@pytest.mark.parametrize('resource', ['../secret.png', '/secret.png', 'diagnostics.json', 'assets/new/link.png'])
def test_reject_unsafe_or_missing_resources(tmp_path, resource):
    directory = report(tmp_path)
    (directory / 'report.html').write_text(f'<img src="{resource}">')
    if resource.endswith('link.png'):
        (directory / resource).symlink_to(directory / 'main.tex')
    with pytest.raises(UploadError):
        build_archive(directory, 'test-paper', io.BytesIO())


def test_server_history_retains_ten_and_current_is_complete(tmp_path):
    directory, root = report(tmp_path), server(tmp_path)
    releases = []
    for _ in range(12):
        stream = io.BytesIO()
        releases.append(build_archive(directory, 'test-paper', stream))
        stream.seek(0)
        result = publish.receive(root, stream)
        assert result['release'] == releases[-1]
        assert (root / 'site/test-paper/latest/assets/new/chart.png').read_bytes() == b'chart'
    catalog = json.loads((root / 'catalog.json').read_text())
    assert len(catalog['test-paper']) == 10
    assert set(path.name for path in (root / 'site/test-paper/releases').iterdir()) == set(releases[-10:])
    assert (root / 'site/test-paper/latest').readlink() == Path('releases') / releases[-1]
    portal = (root / 'site/index.html').read_text()
    assert 'Test paper' in portal and '尚未发布报告' in portal
    assert '工作区' in portal and 'aaaaaaaaaaaa' in portal
    assert not (root / 'site/test-paper/latest/release.json').exists()


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE), ('assets/link.png', tarfile.SYMTYPE), ('main.tex', tarfile.REGTYPE)])
def test_bad_archive_preserves_current_report(tmp_path, name, kind):
    directory, root = report(tmp_path), server(tmp_path)
    valid = io.BytesIO()
    build_archive(directory, 'test-paper', valid)
    valid.seek(0)
    publish.receive(root, valid)
    before = (root / 'site/test-paper/latest').readlink()
    bad = io.BytesIO()
    with tarfile.open(fileobj=bad, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.size = 0
        archive.addfile(member, io.BytesIO())
    bad.seek(0)
    with pytest.raises(ValueError):
        publish.receive(root, bad)
    assert (root / 'site/test-paper/latest').readlink() == before
    assert not (tmp_path / 'escape').exists()


def test_upload_requires_explicit_registered_entry(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    config = tmp_path / 'latex-review/upload.toml'
    config.parent.mkdir()
    entry = tmp_path / 'main.tex'
    config.write_text(f'host="ali-review"\nbase_url="https://reports.example.org"\n[[papers]]\nentry="{entry}"\nslug="test-paper"\n')
    assert load_upload_config(entry) == ('ali-review', 'https://reports.example.org', 'test-paper')
    with pytest.raises(UploadError):
        load_upload_config(tmp_path / 'unregistered.tex')


def test_cli_upload_failure_keeps_local_report_and_opens_browser(tmp_path, monkeypatch):
    from test_cli import project
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    def fail(directory, entry, config):
        assert (directory / 'report.html').is_file()
        calls.append('upload')
        raise UploadError('offline')
    def preview(args):
        assert Path(args[0]).joinpath('report.html').is_file()
        calls.append('browser')
        return 0
    monkeypatch.setattr(upload, 'upload_report', fail)
    monkeypatch.setattr(serve, 'main', preview)
    assert cli.main(['main.tex', '--open-browser', '--upload']) == 16
    assert calls == ['upload', 'browser']
    calls.clear()
    assert cli.main(['main.tex']) == 0
    assert calls == []


def test_archive_embeds_existing_translation_without_request(tmp_path, monkeypatch):
    from test_translation import _content_report
    from latex_review import translation
    generated = _content_report(tmp_path, 'Old sentence.', 'New sentence.')
    units = json.loads((generated.directory / 'translation-units.json').read_text())
    values = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
    config = translation.TranslationConfig.from_mapping(values)
    cache = generated.directory.parent / 'translation-cache'
    cache.mkdir()
    for unit in units:
        key = translation.cache_key(unit, config)
        translated = {'sentences': [{'id': sentence['number'], 'text': '译文：' + sentence['text']} for sentence in unit['sentences']]}
        (cache / (key + '.json')).write_text(json.dumps({'version': translation.RULE_VERSION, 'key': key, 'translation': translated}))
    def forbidden(*args, **kwargs):
        raise AssertionError('上传不能请求翻译服务')
    monkeypatch.setattr(translation, 'request_translation', forbidden)
    stream = io.BytesIO()
    build_archive(generated.directory, 'test-paper', stream, values)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        html = archive.extractfile('report.html').read().decode()
        assert '译文：' in html
        assert 'https://example.invalid' not in html
        assert '<script type="application/json" id="translation-results">{}</script>' not in html
