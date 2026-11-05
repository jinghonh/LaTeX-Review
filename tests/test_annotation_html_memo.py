"""HTML-only immutable memoization must never reuse mutable source-bound units."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from annotation_content_oracle import legacy_build_content
from latex_review.annotations import content as module
from latex_review.contract import ComparisonSource
from latex_review.preview import render_preview
from latex_review.sources import ProjectSource, SourceError
from latex_review.structure import parse_project
from latex_review import project_profile


@pytest.fixture(autouse=True)
def clean_memo():
    with module._HTML_CACHE_LOCK:
        module._HTML_CACHE.clear()
    yield
    with module._HTML_CACHE_LOCK:
        module._HTML_CACHE.clear()


def parsed(root, body, configuration=None):
    (root / 'main.tex').write_text('\\begin{document}\n' + body + '\n\\end{document}')
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)),
                           configuration=configuration or {})
    return parse_project(source.expand())


BODIES = [
    'First.\n\nSecond paragraph.',
    'Unicode 中文 é 😀.\n\nA & B < C.',
    r'Inline $x+1$ and \textbf{bold} with \ref{missing}.',
    r'\section{Heading}\label{s}Text.\begin{equation}y=x\label{eq}\end{equation}',
    r'\begin{figure}\caption{A caption $x$}\label{f}\end{figure}',
    r'\begin{table}\caption{Caption}\begin{tabular}{cc}H & H\\ A & B\\\end{tabular}\end{table}',
    r'\begin{itemize}\item First \item Second $y$\end{itemize}',
    r'\unknown{unsupported}\begin{verbatim}<script>ignored</script>\end{verbatim}',
    'Line one.\r\n\r\nSecond.\rThird.',
]


@pytest.mark.parametrize('body', BODIES)
def test_cold_and_hot_exact_legacy_output(tmp_path, body, monkeypatch):
    project = parsed(tmp_path, body)
    html = render_preview(None, project).html
    expected = legacy_build_content(project, html)
    assert module.build_content(project, html) == expected
    monkeypatch.setattr(module, '_extract_html_units', lambda *a: pytest.fail('hot cache parsed HTML'))
    assert module.build_content(project, html) == expected


def test_same_html_new_saved_bytes_and_permissions_are_rebound(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    html = render_preview(None, project).html
    first = module.build_content(project, html)
    with (tmp_path / 'main.tex').open('a') as stream:
        stream.write('\n% saved revision\n')
    source = replace(project.expanded.source, configuration={'project': {'read_only': True}})
    current = parse_project(source.expand())
    assert render_preview(None, current).html == html
    expected = legacy_build_content(current, html)
    monkeypatch.setattr(module, '_extract_html_units', lambda *a: pytest.fail('same HTML should hit'))
    actual = module.build_content(current, html)
    assert actual == expected
    assert actual['version_id'] != first['version_id']
    for unit in actual['units']:
        assert not unit['source_span']['writable']
        assert unit['source_span']['sha256'] == sha256((tmp_path / 'main.tex').read_bytes()).hexdigest()


def test_parent_labels_sections_raw_and_changes_are_always_fresh(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    html = render_preview(None, project).html
    module.build_content(project, html)
    nodes = tuple(replace(node, labels=('fresh-label',), review=replace(
        node.review, parent_id='fresh-parent', section_path=('Fresh section',), raw_latex='Fresh raw.'))
        for node in project.nodes)
    current = replace(project, nodes=nodes)
    changes = (SimpleNamespace(id='fresh-change', new_node_id='fresh-parent'),)
    expected = legacy_build_content(current, html, changes=changes)
    monkeypatch.setattr(module, '_extract_html_units', lambda *a: pytest.fail('metadata poisoned key'))
    actual = module.build_content(current, html, changes=changes)
    assert actual == expected
    assert actual['units'][0]['labels'] == ['fresh-label']
    assert actual['units'][0]['section_path'] == ['Fresh section']
    assert actual['units'][0]['parent_node_id'] == 'fresh-parent'
    assert actual['units'][0]['change_ids'] == ['fresh-change']


def test_rendered_membership_type_and_html_invalidate(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    html = render_preview(None, project).html
    extract = module._extract_html_units
    calls = []
    def counting(*args):
        calls.append(True)
        return extract(*args)
    monkeypatch.setattr(module, '_extract_html_units', counting)
    module.build_content(project, html)
    removed = replace(project, nodes=project.nodes[1:])
    assert module.build_content(removed, html) == legacy_build_content(removed, html)
    changed = replace(project, nodes=tuple(replace(n, review=replace(n.review, type='equation')) for n in project.nodes))
    assert module.build_content(changed, html) == legacy_build_content(changed, html)
    changed_html = html.replace('First.', 'Changed text.')
    assert module.build_content(project, changed_html) == legacy_build_content(project, changed_html)
    assert len(calls) == 4


def test_returned_mutations_and_threads_cannot_corrupt_templates(tmp_path, monkeypatch):
    project = parsed(tmp_path, r'First $x$ and more.')
    html = render_preview(None, project).html
    expected = legacy_build_content(project, html)
    damaged = module.build_content(project, html)
    damaged['units'][0]['text'] = 'corrupt'
    damaged['units'][0]['atoms'].clear()
    damaged['units'][0]['source_span']['writable'] = False
    cached = next(iter(module._HTML_CACHE.values()))
    with pytest.raises(FrozenInstanceError):
        cached.units[0].text = 'corrupt'
    assert isinstance(cached.units, tuple) and isinstance(cached.units[0].atoms, tuple)
    monkeypatch.setattr(module, '_extract_html_units', lambda *a: pytest.fail('warm template missing'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: module.build_content(project, html), range(8))) == [expected] * 8


def test_cache_is_bounded_and_oversized_html_bypasses(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.')
    html = render_preview(None, project).html
    for i in range(4):
        module.build_content(project, html + f'<!-- {i} -->')
    assert len(module._HTML_CACHE) == 2
    assert html + '<!-- 0 -->' not in module._HTML_CACHE
    monkeypatch.setattr(module, '_HTML_CACHE_MAX_CHARS', 0)
    with module._HTML_CACHE_LOCK:
        module._HTML_CACHE.clear()
    assert module.build_content(project, html) == legacy_build_content(project, html)
    assert not module._HTML_CACHE


def test_same_html_other_root_and_macro_identity_are_rebound(tmp_path, monkeypatch):
    from latex_review.macros import MacroPlaceholder
    first_root, other_root = tmp_path / 'first', tmp_path / 'other'
    first_root.mkdir()
    other_root.mkdir()
    project = parsed(first_root, 'First.')
    other = parsed(other_root, 'First.')
    html = render_preview(None, project).html
    assert render_preview(None, other).html == html
    first = module.build_content(project, html)
    expected = legacy_build_content(other, html)
    monkeypatch.setattr(module, '_extract_html_units', lambda *a: pytest.fail('root is not HTML input'))
    actual = module.build_content(other, html)
    assert actual == expected
    assert actual['project_id'] != first['project_id']
    macros = replace(other, macros={'unused': MacroPlaceholder(0, 'literal', 'Fresh')})
    rebound = module.build_content(macros, html)
    assert rebound == legacy_build_content(macros, html)
    assert rebound['version_id'] != actual['version_id']


def test_hot_cache_still_fails_closed_on_mid_enrichment_profile_change(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.')
    html = render_preview(None, project).html
    module.build_content(project, html)
    values = iter([{'project': {'read_only': False}}, {'project': {'read_only': True}}])
    monkeypatch.setattr(project_profile, 'settings', lambda source: next(values))
    with pytest.raises(SourceError, match='项目配置已变化'):
        module.build_content(project, html)
