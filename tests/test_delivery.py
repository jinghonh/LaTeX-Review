"""交付形式必须共享资源规则，并遵守相同译文选择规则。"""
import io
import json
import tarfile

import pytest

from latex_review import delivery, translation
from test_upload import report
from test_translation import _content_report


@pytest.mark.parametrize('resource', ['../secret.png', '/secret.png', 'diagnostics.json', 'assets/missing.png'])
def test_delivery_forms_reject_same_bad_resource(tmp_path, resource):
    directory = report(tmp_path)
    html = f'<img src="{resource}">'
    (directory / 'report.html').write_text(html)
    with pytest.raises(delivery.DeliveryError):
        delivery.single_file_html(html, directory)
    with pytest.raises(delivery.DeliveryError):
        delivery.build_archive(directory, 'paper', io.BytesIO())


def test_parent_symlink_is_rejected_by_both_forms(tmp_path):
    directory = report(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'chart.png').write_bytes(b'chart')
    (directory / 'assets/link').symlink_to(outside, target_is_directory=True)
    html = '<img src="assets/link/chart.png">'
    (directory / 'report.html').write_text(html)
    with pytest.raises(delivery.DeliveryError):
        delivery.single_file_html(html, directory)
    with pytest.raises(delivery.DeliveryError):
        delivery.build_archive(directory, 'paper', io.BytesIO())


def test_single_file_preserves_external_links_and_embeds_quoted_resource(tmp_path):
    directory = report(tmp_path)
    html = "<img src='assets/new/chart.png?v=1'><a href='#anchor'>x</a>"
    result = delivery.single_file_html(html, directory)
    assert "src='data:image/png;base64," in result
    assert "href='#anchor'" in result


def test_configured_delivery_discards_embedded_results_without_session(tmp_path, monkeypatch):
    directory = report(tmp_path)
    (directory / 'report.html').write_text('<body><script type="application/json" id="translation-results">{"old-model": {}}</script></body>')
    (directory / 'translation-units.json').write_text('[]')
    def forbidden(*args, **kwargs):
        raise AssertionError('只读交付不能创建翻译会话')
    monkeypatch.setattr(translation, 'TranslationManager', forbidden)
    assert 'old-model' in delivery.publication_html(directory)
    assert 'old-model' not in delivery.publication_html(directory, {'base_url': 'https://example.invalid/v1', 'model': 'new-model'})


@pytest.mark.parametrize('invalid', ['manifest', 'cache'])
def test_incompatible_translation_delivers_original_with_warning(tmp_path, invalid):
    generated = _content_report(tmp_path, 'Old sentence.', 'New sentence.')
    directory = generated.directory
    units = json.loads((directory / 'translation-units.json').read_text())
    values = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
    if invalid == 'manifest':
        units[0]['rule_version'] = 'outdated'
        (directory / 'translation-units.json').write_text(json.dumps(units))
    else:
        cache = directory.parent / 'translation-cache'
        cache.mkdir()
        key = translation.cache_key(units[0], translation.TranslationConfig.from_mapping(values))
        (cache / (key + '.json')).write_text('not JSON')
    html = delivery.publication_html(directory, values)
    assert 'delivery-warning' in html
    assert 'New sentence.' in html
    single = delivery.single_file_html(html, directory)
    stream = io.BytesIO()
    delivery.build_archive(directory, 'paper', stream, values)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        published = archive.extractfile('report.html').read().decode()
    assert 'delivery-warning' in single and 'delivery-warning' in published


def test_delivery_forms_keep_separate_size_limits(tmp_path, monkeypatch):
    directory = report(tmp_path)
    html = (directory / 'report.html').read_text()
    monkeypatch.setattr(delivery, '_MAX_EMBEDDED_ASSET', 4)
    with pytest.raises(delivery.DeliveryError, match='内嵌上限'):
        delivery.single_file_html(html, directory)
    delivery.build_archive(directory, 'paper', io.BytesIO())
    monkeypatch.setattr(delivery, 'MAX_BYTES', 4)
    with pytest.raises(delivery.DeliveryError, match='200 MiB'):
        delivery.build_archive(directory, 'paper', io.BytesIO())
