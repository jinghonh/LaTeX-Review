from hashlib import sha256

import pytest

from latex_review.checks import capture_snapshot, build_check_document, snapshot_from_project
from latex_review.sources import SourceError


@pytest.mark.parametrize('filename,content', [
    ('plot.pdf', b'%PDF-1.5\n%\x81\x82\x83\x84\n'),
    ('plot.png', b'\x89PNG\r\n\x1a\n\x00\xff'),
])
def test_binary_graphics_preserve_source_evidence_and_snapshot_identity(tmp_path, filename, content):
    from latex_review.checks import evaluate_current
    source = ('\\begin{document}\n中文 figure.\n'
              '\\begin{figure}\\includegraphics{' + filename + '}'
              '\\caption{A figure.}\\end{figure}\n\\end{document}')
    (tmp_path / 'main.tex').write_text(source, encoding='utf-8')
    graphic = tmp_path / filename
    graphic.write_bytes(content)
    snapshot = capture_snapshot('main.tex', root=tmp_path)
    assert snapshot.files[filename] == content
    document = build_check_document(snapshot)
    evidence = [item for unit in document.units for item in unit['evidence']]
    assert evidence
    assert all(item['file'] == 'main.tex' and
               item['quote'] == source[item['start']:item['end']] for item in evidence)
    evaluate_current(document)
    graphic.write_bytes(content + b'changed')
    assert capture_snapshot('main.tex', root=tmp_path).content_id != snapshot.content_id


@pytest.mark.parametrize('text', [
    'ordinary 中文 prose ' * 50 + '\r\n% comment \\input{hidden}\r\n' + r'\input{visible}\% text \\input{escaped} \\\input{third}',
    r'\begin{verbatim}\input{hidden}\end{verbatim}\begin{lstlisting}\include{hidden}\end{lstlisting}\verb|\input{hidden}|\input{visible}',
    r'\IfFileExists{present.tex}{\input{yes}}{\input{no}}\IfFileExists{missing.tex}{\input{no}}{\IfFileExists{present.tex}{\include{nested}}{\input{no}}}',
    r'\newcommand{\labelit}[1]{\label{#1}}\def\inputmacro#1{\input{#1}}\newenvironment{custom}{\input{a}}{\input{b}}\input{visible}',
    r'\newcommand{\bad}[2]{unclosed',
    '\\includegraphics*[width=1cm]{plot.png}\\input % comment\r\n bare.tex \\addbibresource[style=x]{refs.bib}\\myinput{custom.tex}',
])
def test_source_command_event_sweep_matches_character_scan(monkeypatch, text):
    import re
    from latex_review import sources
    exists = lambda name: name == 'present.tex'
    optimized = list(sources._commands(text, ('myinput',), exists))
    monkeypatch.setattr(sources, '_SOURCE_EVENT', re.compile(r'[\s\S]'))
    reference = list(sources._commands(text, ('myinput',), exists))
    assert optimized == reference


@pytest.mark.parametrize('body', [
    'ordinary 中文 🧪 prose ' * 50 + '\r\n \t\r\n second paragraph\n\nlast',
    r'\section{A}\label{sec:a} Text {grouped {nested}} $x+y$ and \$ signs. ' + '\n  \n' + r'\subsection{B}Next \ref{sec:a}.',
    r'\begin{abstract}A paragraph.\end{abstract}\begin{itemize}\item One.\item Two $x$.\end{itemize}',
    r'\begin{figure}\caption{A \textbf{caption}.}\label{f}\end{figure}\begin{equation}a=b\end{equation} Tail.',
    r'\newcommand{\mytext}{A paragraph}\mytext \unknown{Fallback} \[x=y\] $$z=0$$ Tail {incomplete',
])
def test_structure_event_sweep_matches_character_scan(tmp_path, monkeypatch, body):
    import re
    from latex_review import structure
    path = tmp_path / 'main.tex'
    path.write_text(r'\documentclass{article}\title{Title}\begin{document}' + body + r'\end{document}')
    optimized = capture_snapshot(path)
    # Every character is an event: the original character-by-character scan,
    # with the same now allocation-free blank matcher.
    monkeypatch.setattr(structure, '_SCAN_EVENT', re.compile(r'[\s\S]'))
    monkeypatch.setattr(structure, '_INLINE_EVENT', re.compile(r'[\s\S]'))
    reference = structure.parse_project(optimized.parsed.expanded, include_title=True)
    assert optimized.parsed == reference


def test_comment_mask_matches_original_character_loop():
    from random import Random
    from latex_review.structure import _masked

    def reference(text):
        chars = list(text)
        i = 0
        while i < len(text):
            if text[i] == '%':
                backslashes, k = 0, i - 1
                while k >= 0 and text[k] == '\\':
                    backslashes += 1
                    k -= 1
                if backslashes % 2:
                    i += 1
                    continue
                j = i
                while j < len(text) and text[j] not in '\r\n':
                    chars[j] = ' '
                    j += 1
                i = j
            else:
                i += 1
        return ''.join(chars)

    rng = Random(20261105)
    cases = ['', 'plain 中文 text', '\r\n% comment\r\nend',
             ''.join('\\' * n + '% comment\r\n' for n in range(12))]
    cases += [''.join(rng.choices('ab中文😀 \\%\r\n\t{}', k=rng.randrange(1000))) for _ in range(300)]
    for text in cases:
        assert _masked(text) == reference(text)


def test_compiled_blank_match_preserves_slice_relative_boundaries():
    import re
    from latex_review.structure import _BLANK
    text = 'x \t\r\n \n\ny\r\n\r\nz  \n\n'
    for start in range(len(text) + 1):
        for end in range(start, len(text) + 1):
            original = re.match(r'(?:[ \t]*\r?\n){2,}', text[start:end])
            optimized = _BLANK.match(text, start, end)
            assert bool(original) == bool(optimized)
            if original:
                assert original.group() == optimized.group()
                assert start + original.end() == optimized.end()


def test_live_and_diff_compile_render_defaults_share_check_identity(tmp_path):
    from latex_review.checks import evaluate_current, canonical_check_configuration
    path = tmp_path / 'main.tex'
    path.write_text(r'\title{Synthetic Title}\begin{document}\maketitle Body \ref{missing}.\end{document}')
    unrelated = {'compile': {'engine': 'latexmk', 'sequence': ['main.tex']},
                 'diff': {'comments': True}, 'output': {'path': 'report.html'},
                 'render': {'theme': 'dark'}, 'translation': {'language': 'zh'}}
    live = capture_snapshot(path)
    diff = capture_snapshot(path, configuration=unrelated)
    frozen_diff = snapshot_from_project(diff.parsed, raw_files=diff.files, include_title=True)
    assert live.identity == diff.identity == frozen_diff.identity
    assert live.configuration == diff.configuration == frozen_diff.configuration == {}
    live_report = evaluate_current(build_check_document(live))
    assert live_report == evaluate_current(build_check_document(diff), configuration=unrelated)
    assert live_report == evaluate_current(build_check_document(frozen_diff))
    assert canonical_check_configuration({'checks': {'enabled': False}, **unrelated}) == {'checks': {'enabled': False}}
    changed = capture_snapshot(path, configuration={'checks': {'enabled': False}, **unrelated})
    assert changed.content_id != live.content_id


def test_raw_unicode_crlf_repeated_includes_and_cross_file(tmp_path):
    main = b'\\begin{document}\r\n\\input{part}\\input{part}\\end{document}'
    part = '中文😀\r\n\\label{same}\r\n'.encode()
    (tmp_path / 'main.tex').write_bytes(main)
    (tmp_path / 'part.tex').write_bytes(part)
    snapshot = capture_snapshot('main.tex', root=tmp_path)
    document = build_check_document(snapshot)
    evidence = document.evidence(0, len(snapshot.parsed.expanded.text))
    included = [e for e in evidence if e['file'] == 'part.tex']
    assert len(included) == 2
    assert len({e['include_instance'] for e in included}) == 2
    assert all(e['quote'] == part.decode() and e['file_sha256'] == sha256(part).hexdigest() for e in included)
    assert all(e['end'] == len(part.decode()) for e in included)
    assert snapshot.content_id == capture_snapshot('main.tex', root=tmp_path).content_id
    assert snapshot_from_project(snapshot.parsed, raw_files=snapshot.files).files == snapshot.files
    units = document.units
    assert all(a['expanded_end'] <= b['expanded_start'] for a, b in zip(units, units[1:]))


def test_changes_during_parse_retry_and_fail(tmp_path, monkeypatch):
    import latex_review.checks.snapshot as module
    path = tmp_path / 'main.tex'
    path.write_text('Before')
    original = module.parse_project
    calls = []
    def changing(project, **kwargs):
        calls.append(True)
        parsed = original(project, **kwargs)
        path.write_text('After' if len(calls) % 2 else 'Before')
        return parsed
    monkeypatch.setattr(module, 'parse_project', changing)
    with pytest.raises(SourceError, match='一致') as error:
        capture_snapshot(path, retries=1)
    assert error.value.code == 'source_changed'
    assert len(calls) == 2


def test_supplied_source_preserves_parser_identity_and_title(tmp_path):
    from latex_review.contract import ComparisonSource
    from latex_review.sources import ProjectSource
    path = tmp_path / 'main.tex'
    path.write_text(r'\title{Unicode 标题}\begin{document}Body\end{document}')
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('worktree', 'HEAD:working'))
    snapshot = capture_snapshot('main.tex', source=source, include_title=True)
    assert snapshot.parsed.expanded.source.identity == source.identity
    assert any(node.review.type == 'title' for node in snapshot.parsed.nodes)
    assert snapshot.content_id != capture_snapshot('main.tex', source=source, include_title=False).content_id
    from latex_review.checks import snapshot_from_project
    frozen = snapshot_from_project(snapshot.parsed, raw_files=snapshot.files,
                                   configuration=snapshot.configuration, include_title=True)
    assert frozen.identity == snapshot.identity


def test_snapshot_configuration_detaches_nested_external_mutations(tmp_path):
    from latex_review.checks import evaluate_current, build_check_document, snapshot_from_project
    path = tmp_path / 'main.tex'
    path.write_text(r'\label{x}\label{x}')
    configuration = {'checks': {'enabled': True, 'rules': ['references']}}
    snapshot = capture_snapshot(path, configuration=configuration)
    baseline = evaluate_current(build_check_document(snapshot))
    frozen = snapshot_from_project(snapshot.parsed, raw_files=snapshot.files, configuration=configuration)
    configuration['checks']['rules'].clear()
    configuration['checks']['enabled'] = False
    snapshot.parsed.expanded.source.configuration['checks']['rules'].clear()
    assert snapshot.configuration['checks'] == {'enabled': True, 'rules': ['references']}
    assert frozen.configuration['checks'] == snapshot.configuration['checks']
    assert evaluate_current(build_check_document(snapshot)) == baseline


def test_missing_dependency_appears_changes_identity(tmp_path):
    (tmp_path / 'main.tex').write_text(r'\input{new}')
    before = capture_snapshot('main.tex', root=tmp_path)
    assert before.parsed.expanded.diagnostics
    (tmp_path / 'new.tex').write_text('New untracked text')
    after = capture_snapshot('main.tex', root=tmp_path)
    assert before.content_id != after.content_id
    assert 'new.tex' in after.files
