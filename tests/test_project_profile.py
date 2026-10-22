"""Multi-entry papers retain original positions and never write into read-only roots."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from latex_review.contract import ComparisonSource
from latex_review.project_profile import directory, load, main, output, validate
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    root = tmp_path / 'paper'
    root.mkdir()
    config = write(tmp_path, 'profile.toml', '''[project]
read_only = true
entries = ["journal/main.tex", "journal/supplement.tex"]
[source]
omit_macros = ["omitfloat"]
[source.include_macros]
sharedinput = "../shared/#1"
paperfloat = "../shared/floats/#1"
[source.environments]
widefigure = "figure"
widetable = "table"
''')
    write(root, 'journal/main.tex', r'''\documentclass{article}
\begin{document}
\omitfloat{hidden}
\sharedinput{body}
\paperfloat{hidden}
\paperfloat{visible}
\IfFileExists{../shared/references.bib}{Selected branch.}{Wrong branch.}
\end{document}''')
    write(root, 'journal/supplement.tex', r'\begin{document}Supplement.\end{document}')
    write(root, 'shared/body.tex', 'Shared paragraph.\n')
    write(root, 'shared/floats/visible.tex', r'\begin{widefigure}\caption{Visible caption.}\end{widefigure}')
    write(root, 'shared/references.bib', '')
    assert main([str(root), '--config', str(config)]) == 0
    return root


def test_configured_includes_omission_condition_and_source_mapping(profile):
    source = ProjectSource('new', profile, 'journal/main.tex', ComparisonSource('directory', str(profile)))
    expanded = source.expand()
    assert not expanded.diagnostics
    assert 'Selected branch.' in expanded.text and 'Wrong branch.' not in expanded.text
    assert r'\paperfloat{hidden}' not in expanded.text
    start = expanded.text.index('Shared paragraph.')
    origin = expanded.origin_ranges(start, start + len('Shared paragraph.'))[0].origin
    assert origin.file == 'shared/body.tex' and origin.start == 0 and origin.confidence == 'exact'
    project = parse_project(expanded)
    assert any(n.review.type == 'figure' and 'Visible caption.' in n.review.raw_latex for n in project.nodes)
    assert not (profile / '.latex-review').exists()


def test_readonly_annotations_and_entry_outputs_are_external(profile):
    from latex_review.live import build, prepare_request
    from latex_review.annotations import load_content, AnnotationStore
    source = ProjectSource('new', profile, 'journal/main.tex', ComparisonSource('directory', str(profile)))
    target = output(profile, source.entry, 'live')
    target.mkdir(parents=True)
    build(source, target)
    content = load_content(target)
    spans = [u['source_span'] for u in content['units'] if u.get('source_span', {}).get('reliable')]
    assert spans and all(not s['writable'] for s in spans)
    store = AnnotationStore._for_content(content, profile)
    assert store.directory.is_relative_to(directory(profile))
    with AnnotationStore.open(content, profile) as session:
        session.read()
        from test_annotations import create
        created = create(session, 'Shared paragraph.')
        assert created['result']['thread_id']
    with AnnotationStore.open(content, profile) as session:
        assert len(session.read()['threads']) == 1
    from latex_review.annotations import AnnotationError
    with pytest.raises(AnnotationError, match='只读'):
        store._suggestion(content, {'operation': 'suggestion_apply'})
    assert not (profile / '.latex-review').exists()
    a = prepare_request(profile / 'journal/main.tex', root=profile)
    b = prepare_request(profile / 'journal/supplement.tex', root=profile)
    assert a['output'] != b['output'] and a['cache'] != b['cache']
    assert not Path(a['output']).is_relative_to(profile)
    with pytest.raises(ValueError, match='只读'):
        prepare_request(profile / 'journal/main.tex', root=profile, output=profile / 'preview')


@pytest.mark.parametrize('values', [
    {'source': {'include_macros': {'bad': '/tmp/#1'}}},
    {'source': {'include_macros': {'bad': '../paper/#1/#1'}}},
    {'source': {'environments': {'wide': 'equation'}}},
    {'project': {'read_only': 'true'}},
    {'compile': {'sequence': ['../outside.tex']}},
])
def test_reject_invalid_profiles(values):
    with pytest.raises(ValueError):
        validate(values)


def test_private_multi_entry_compile_with_parent_relative_input(tmp_path):
    import shutil
    from latex_review.compilation import compile_side
    if not shutil.which('latexmk') or not shutil.which('pdflatex'):
        pytest.skip('TeX tools unavailable')
    root = tmp_path / 'paper'
    for entry, text in [('main', 'Main'), ('supplement', 'Supplement')]:
        write(root, f'journal/{entry}.tex', rf'''\documentclass{{article}}
\usepackage{{xr}}
\externaldocument{{{'supplement' if entry == 'main' else 'main'}}}
\begin{{document}}\section{{{text}}}\label{{{entry}}}
\input{{../shared/body}}
See Section~\ref{{{'supplement' if entry == 'main' else 'main'}}}.
\end{{document}}''')
    write(root, 'shared/body.tex', r'Shared content. \begin{equation}x=1\end{equation}')
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    source = ProjectSource('new', root, 'journal/main.tex', ComparisonSource('directory', str(root)))
    target = tmp_path / 'report'
    target.mkdir()
    status, issues = compile_side(source, target, sequence=('journal/main.tex', 'journal/supplement.tex'))
    assert status.status == 'success', status.reason
    assert (target / status.pdf).is_file()
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_compiled_external_reference_has_number(profile):
    from latex_review.preview import _reference_html
    source = ProjectSource('new', profile, 'journal/main.tex', ComparisonSource('directory', str(profile)))
    project = replace(parse_project(source.expand()), compiled_numbering={'labels': {'external': 'S-I'}})
    html = _reference_html('external', r'\ref{external}', project, 'new')
    assert 'S-I' in html and '??' not in html and 'external-ref' in html


def test_profile_does_not_apply_to_unlisted_entry(profile):
    assert load(profile, 'other/main.tex') == {}
    assert load(profile, 'journal/main.tex')['project']['read_only']
