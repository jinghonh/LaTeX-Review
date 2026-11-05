"""Per-enrichment reuse must not outlive its parse or permissions fence."""
from hashlib import sha256

import pytest

from latex_review import annotation_sources, project_profile
from latex_review.annotations import build_content
from latex_review.contract import ComparisonSource
from latex_review.preview import render_preview
from latex_review.sources import ProjectSource, SourceError
from latex_review.structure import parse_project


def project(root, configuration=None):
    (root / 'main.tex').write_text('\\begin{document}\nFirst.\n\nSecond.\n\n\\input{part}\n\\end{document}')
    (root / 'part.tex').write_text('Included.\n\nAgain.')
    return parse_project(ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)),
                                       configuration=configuration or {}).expand())


def test_only_hash_once_per_captured_file_and_never_reuse_next_build(tmp_path, monkeypatch):
    parsed = project(tmp_path)
    html = render_preview(None, parsed).html
    calls = []
    def digest(raw):
        calls.append(raw)
        return sha256(raw)
    monkeypatch.setattr(annotation_sources, 'sha256', digest)
    first = build_content(parsed, html)
    assert len(first['units']) == 4
    assert len(calls) == 2
    for unit in first['units']:
        span = unit['source_span']
        assert span['sha256'] == sha256((tmp_path / span['file']).read_bytes()).hexdigest()
    build_content(parsed, html)
    assert len(calls) == 4


def test_configuration_change_during_enrichment_fails_closed(tmp_path, monkeypatch):
    parsed = project(tmp_path)
    html = render_preview(None, parsed).html
    values = iter([{'project': {'read_only': False}}, {'project': {'read_only': True}}])
    monkeypatch.setattr(project_profile, 'settings', lambda source: next(values))
    with pytest.raises(SourceError, match='项目配置已变化'):
        build_content(parsed, html)


def test_read_only_configuration_remains_nonwritable(tmp_path):
    parsed = project(tmp_path, {'project': {'read_only': True}})
    result = build_content(parsed, render_preview(None, parsed).html)
    assert result['units']
    assert all(not unit['source_span']['writable'] for unit in result['units'])
