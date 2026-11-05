"""Identity-only freshness must retain every optimistic capture fence."""
import os

import pytest

from latex_review.checks import snapshot as module
from latex_review.sources import ProjectSource, SourceError


def source(tmp_path, body='First paragraph.'):
    path = tmp_path / 'main.tex'
    path.write_text('\\begin{document}\n' + body + '\n\\end{document}')
    return path


def test_identity_matches_full_capture_without_invoking_parser(tmp_path, monkeypatch):
    path = source(tmp_path, 'Text α😀 $x$ and \\ref{missing}.')
    full = module.capture_snapshot(path, root=tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('verification must not parse again')
    monkeypatch.setattr(module, 'parse_project', forbidden)
    assert module.capture_content_id(path, root=tmp_path) == full.content_id
    assert module.capture_content_id(path, root=tmp_path, include_title=False) != full.content_id


@pytest.mark.parametrize('command,name,value', [
    (r'\input{missing}', 'missing.tex', 'Now present.'),
    (r'\bibliography{missing}', 'missing.bib', '@article{x,title={Now present}}'),
])
def test_missing_dependency_appearance_changes_identity(tmp_path, command, name, value):
    path = source(tmp_path, command)
    before = module.capture_snapshot(path, root=tmp_path)
    (tmp_path / name).write_text(value)
    current = module.capture_content_id(path, root=tmp_path)
    assert current != before.content_id
    assert current == module.capture_snapshot(path, root=tmp_path).content_id


def test_same_size_same_mtime_bytes_and_configuration_still_invalidate(tmp_path):
    path = source(tmp_path, 'First paragraph.')
    before = module.capture_snapshot(path, root=tmp_path)
    stat = path.stat()
    path.write_text(path.read_text().replace('First', 'Other'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert module.capture_content_id(path, root=tmp_path) != before.content_id
    current = module.capture_content_id(path, root=tmp_path)
    assert module.capture_content_id(path, root=tmp_path, configuration={'checks': {'on_save': False}}) != current


def test_second_expansion_fence_catches_mid_verification_edit(tmp_path, monkeypatch):
    path = source(tmp_path)
    original = ProjectSource.expand
    calls = []
    def expand(self):
        calls.append(True)
        if len(calls) == 2:
            path.write_text(path.read_text().replace('First', 'Other'))
        return original(self)
    monkeypatch.setattr(ProjectSource, 'expand', expand)
    with pytest.raises(SourceError, match='一致源码快照'):
        module.capture_content_id(path, root=tmp_path, retries=0)
    assert len(calls) == 2


def test_include_macro_configuration_rebuilds_dependency_graph(tmp_path):
    path = source(tmp_path, r'\myinput{part}')
    (tmp_path / 'part.tex').write_text('Included paragraph.')
    before = module.capture_content_id(path, root=tmp_path)
    configuration = {'source': {'include_macros': {'myinput': '#1.tex'}}}
    configured = module.capture_content_id(path, root=tmp_path, configuration=configuration)
    assert configured != before
    assert configured == module.capture_snapshot(path, root=tmp_path, configuration=configuration).content_id


def test_symlink_containment_is_not_bypassed(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    outside = tmp_path / 'outside.tex'
    outside.write_text('Outside the paper.')
    (root / 'unsafe.tex').symlink_to(outside)
    path = source(root, r'\input{unsafe}')
    full = module.capture_snapshot(path, root=root, retries=0)
    # The established reader reports a diagnostic and omits unsafe includes;
    # verification must retain that contract, not follow the symlink.
    assert set(full.files) == {'main.tex'}
    assert 'Outside the paper.' not in full.parsed.expanded.text
    assert full.parsed.expanded.diagnostics
    assert module.capture_content_id(path, root=root, retries=0) == full.content_id


def test_profile_change_during_verification_fails_closed(tmp_path, monkeypatch):
    from latex_review import project_profile
    path = source(tmp_path)
    calls = []
    def settings(source):
        calls.append(True)
        return {} if len(calls) == 1 else {'checks': {'on_save': False}}
    monkeypatch.setattr(project_profile, 'settings', settings)
    with pytest.raises(SourceError, match='一致源码快照'):
        module.capture_content_id(path, root=tmp_path, retries=0)
