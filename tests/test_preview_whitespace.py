"""Comparison-only whitespace acceleration preserves legacy rendering byte for byte."""
import random
import re

import pytest

from latex_review import preview
from latex_review.contract import ComparisonSource
from latex_review.macros import validate_macros
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project


def legacy_whitespace(value):
    return re.sub(r'\s+', ' ', value).strip()


def outcome(call):
    try:
        return ('returned', call())
    except Exception as error:
        return ('raised', type(error), str(error))


@pytest.mark.parametrize('text', [
    '', ' \t\r\n\f\v', 'plain', '  alpha  beta \r\n gamma\t',
    '\x00alpha\x1cbeta\x1d\x1e\x1fgamma\x00',
    '\u0085\u00a0alpha\u1680beta\u2000\u2001gamma\u2028\u2029',
    '\u202f\u205f\u3000中文\u00a0😀 é\u3000',
    '\u180ealpha\u200bbeta\ufeffgamma',  # Not Unicode whitespace.
    '\ud800 alpha\t\udfff',  # Python strings may contain unpaired surrogates.
    '<p class="x">  Alpha.\n\t<span>中文 😀.</span> </p>',
])
def test_builtin_string_matches_original_regex(text):
    assert preview._normalized_whitespace(text) == legacy_whitespace(text)


def test_all_unicode_codepoints_match_original_regex():
    # Includes C0 separators U+001C..U+001F and every surrogate/non-BMP codepoint.
    text = ''.join(map(chr, range(0x110000)))
    assert preview._normalized_whitespace(text) == legacy_whitespace(text)
    whitespace = ''.join(character for character in text if character.isspace())
    assert preview._normalized_whitespace(whitespace) == legacy_whitespace(whitespace) == ''
    separated = '|'.join('left' + character + 'right' for character in whitespace)
    assert preview._normalized_whitespace(separated) == legacy_whitespace(separated)


def test_seeded_random_full_unicode_and_whitespace_runs_match_regex():
    rng = random.Random(20261105)
    whitespace = '\t\n\r\f\v\x1c\x1d\x1e\x1f \u0085\u00a0\u1680\u2000\u2028\u2029\u202f\u205f\u3000'
    for _ in range(400):
        text = ''.join(rng.choice(whitespace) if rng.randrange(3) == 0 else chr(rng.randrange(0x110000))
                       for _ in range(rng.randrange(512)))
        assert preview._normalized_whitespace(text) == legacy_whitespace(text)


class NonstandardString(str):
    def split(self, *args, **kwargs):
        raise AssertionError('custom split must not be called')

    def __str__(self):
        return 'a forged string representation'


class OtherObject:
    def split(self, *args, **kwargs):
        raise AssertionError('custom split must not be called')


@pytest.mark.parametrize('value', [NonstandardString(' a\t b '), NonstandardString('plain'),
                                   b'a b', bytearray(b'a b'), None, 1, OtherObject()])
def test_nonstandard_inputs_keep_legacy_result_or_error(value):
    assert outcome(lambda: preview._normalized_whitespace(value)) == outcome(lambda: legacy_whitespace(value))


BODIES = [
    'Alpha  beta.\n\tSecond sentence 中文 😀.',
    r'Inline $x+1$ agrees. Another \textbf{bold} sentence.',
    r'\badge{Macro name} agrees. A second \emph{sentence} follows.',
    r'See \ref{missing} and \cite{absent}. Unsupported \unknown{raw} remains.',
    r'\begin{figure}\caption{A  caption $x$. Second \badge{caption} sentence.}\label{fig:a}\end{figure}',
    '\\section{Heading}\nA sentence. % private comment\nAnother sentence.\n\n'
    r'\begin{figure}\caption{Plain caption. Second sentence.}\end{figure}',
]


def project(root, body):
    root.mkdir()
    (root / 'main.tex').write_text(r'\documentclass{article}\begin{document}' + body + r'\end{document}', encoding='utf-8')
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    macros = validate_macros({'badge': {'arguments': 1, 'strategy': 'replace', 'template': '【#1】'}})
    return parse_project(source.expand(), macros=macros)


@pytest.mark.parametrize('body', BODIES)
@pytest.mark.parametrize('two_sides', [False, True])
def test_real_render_bytes_diagnostics_anchors_and_inline_calls_match_legacy(tmp_path, monkeypatch, body, two_sides):
    old = project(tmp_path / 'old', body.replace('Second', 'Previous')) if two_sides else None
    new = project(tmp_path / 'new', body)
    highlights = {(side, node.review.id): frozenset({1})
                  for side, parsed in [('old', old), ('new', new)] if parsed
                  for node in parsed.nodes if node.review.type in {'paragraph', 'figure'}}
    calls = []
    inline = preview._inline_html

    def tracked(raw, parsed, side, children=None, diagnostics=None, source=None, base_start=None):
        calls.append((raw, side, tuple(child.review.id for child in children or ()), source, base_start))
        return inline(raw, parsed, side, children, diagnostics, source, base_start)

    monkeypatch.setattr(preview, '_inline_html', tracked)
    fast = preview._normalized_whitespace
    monkeypatch.setattr(preview, '_normalized_whitespace', legacy_whitespace)
    expected = preview.render_preview(old, new, sentence_highlights=highlights)
    expected_calls = list(calls)
    calls.clear()
    monkeypatch.setattr(preview, '_normalized_whitespace', fast)
    actual = preview.render_preview(old, new, sentence_highlights=highlights)
    assert actual.html.encode('utf-8') == expected.html.encode('utf-8')
    assert actual.diagnostics == expected.diagnostics
    assert actual.anchors() == expected.anchors()
    assert actual._fragment_plan == expected._fragment_plan
    assert actual.compose(style='.unchanged{color:red}', head='<meta name="same" content="same">') == expected.compose(
        style='.unchanged{color:red}', head='<meta name="same" content="same">')
    assert calls == expected_calls
    assert 'review-sentence' in actual.html
    assert re.findall(r'id="[^"]+-sentence-\d+"', actual.html)


def test_full_diff_and_standalone_reports_match_original_normalization(tmp_path, monkeypatch):
    from latex_review.comparison import compare_projects
    from latex_review.report import write_report
    old = project(tmp_path / 'old', 'Stable sentence. A small gain.\n\n'
                  r'\begin{figure}\caption{Plain caption. An old observation.}\end{figure}')
    new = project(tmp_path / 'new', 'Stable sentence. A large gain.\n\n'
                  r'\begin{figure}\caption{Plain caption. A new observation.}\end{figure}')
    comparison = compare_projects(old, new)
    fast = preview._normalized_whitespace
    monkeypatch.setattr(preview, '_normalized_whitespace', legacy_whitespace)
    write_report(old, new, comparison, tmp_path / 'legacy', pdf_converter='', single_file=True)
    monkeypatch.setattr(preview, '_normalized_whitespace', fast)
    write_report(old, new, comparison, tmp_path / 'fast', pdf_converter='', single_file=True)
    for name in ('report.html', 'report-single.html', 'diagnostics.json', 'diff.json', 'translation-units.json'):
        assert (tmp_path / 'fast' / name).read_bytes() == (tmp_path / 'legacy' / name).read_bytes()
    html = (tmp_path / 'fast' / 'report.html').read_text()
    assert 'review-sentence sentence-changed' in html
    assert 'figcaption' in html
