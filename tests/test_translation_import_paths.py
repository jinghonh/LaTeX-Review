"""Frozen pre-optimization translation oracle and branch/import proofs."""
import builtins
from copy import deepcopy
from types import SimpleNamespace

import pytest

from latex_review import translation as t

LEGACY_SOURCE = r'''
def _protected_sentence(raw: str, render, *, token_start: int = 0) -> dict:
    from .structure import _argument, _masked

    protected: list[dict] = []

    def add(latex: str, html: str, kind: str, **metadata) -> str:
        token = f"[[LR_{token_start + len(protected)}]]"
        protected.append({"token": token, "latex": latex, "html": html, "kind": kind, **metadata})
        return token

    def walk(text: str) -> str:
        parts = []
        ordinary = []
        def flush():
            parts.append(_NUMBER.sub(lambda m: add(m[0], escape(m[0]), "number"), "".join(ordinary)))
            ordinary.clear()
        for token in scan_latex(text)[0]:
            if token.kind in {"math", "citation", "reference", "command", "verbatim"}:
                flush()
                if re.match(r"\\label\b", token.text):
                    continue
                command = re.match(r"\\([A-Za-z]+)", token.text)
                name = command[1] if command else ""
                argument = _argument(_masked(token.text), command.end()) if command else None
                tag = _FORMATTING.get(name)
                opening = f"<{tag}>" if tag else ""
                if name == "href" and argument:
                    address = argument[1]
                    argument = _argument(_masked(token.text), argument[0])
                    tag = "span"
                    opening = "<span>"
                    try:
                        parsed = urlsplit(address)
                        safe = (parsed.scheme in {"http", "https"} and parsed.hostname
                                and not any(c in address for c in "\r\n\\"))
                    except ValueError:
                        safe = False
                    if safe:
                        tag = "a"
                        opening = f'<a href="{escape(address, quote=True)}" rel="noreferrer">'
                if tag and argument and argument[0] == len(token.text):
                    start = add(token.text[:argument[2]], opening, "format_open", source=name)
                    parts += [start, walk(token.text[argument[2]:argument[0] - 1]),
                              add("}", f"</{tag}>", "format_close", pair=start, source=name)]
                else:
                    parts.append(add(token.text, render(token.text), token.kind))
            else:
                ordinary.append(" " if token.kind == "space" else token.text)
        flush()
        return "".join(parts)

    clean = _without_comments(raw)[0]
    if _TOKEN.search(clean):
        raise TranslationError("原文包含保留的翻译占位符，无法可靠翻译")
    plain = _plain_sentence_text(clean)
    text = (walk(clean) if plain is None else
            _NUMBER.sub(lambda m: add(m[0], escape(m[0]), 'number'), plain))
    return {"text": text, "protected": protected}

def _build_unit(project, node, side: str, change_id: str, changed: set[int], *,
                diagnostics: list | None = None, highlight_structure: bool = True) -> dict | None:
    from .preview import _inline_html
    from .structure import _argument, _masked
    from .structured_diff import _caption

    kind, node_id = node.review.type, node.review.id
    raw = node.review.raw_latex
    if kind == "figure":
        raw = _caption(raw) or ""
    elif kind in _HEADINGS:
        command = re.match(r"\\[A-Za-z@]+", raw)
        arg = _argument(_masked(raw), command.end()) if command else None
        raw = raw[arg[2]:arg[0] - 1] if arg else node.review.plain_text or ""
    unit_id = f"{side}-{node_id}"
    unit = {"id": unit_id, "side": side, "node_id": node_id,
            "kind": kind, "change_id": change_id,
            "rule_version": RULE_VERSION, "sentences": []}
    sentences = []
    try:
        for number, sentence in enumerate(split_sentences(raw), 1):
            def render(text):
                return _inline_html(text, project, side, [], source=node.review.source)
            protected = _protected_sentence(sentence.text, render,
                                            token_start=sum(len(s["protected"]) for s in sentences))
            if not protected["text"].strip():
                continue
            sentences.append({"number": number, "changed": number in changed or (highlight_structure and kind != "paragraph"),
                              **protected})
    except TranslationError as exc:
        unit["error"] = str(exc)
        if diagnostics is not None:
            from .contract import Diagnostic
            diagnostics.append(Diagnostic("translation_unavailable", "warning", str(exc),
                source_old=node.review.source if side == "old" else None,
                source_new=node.review.source if side == "new" else None))
        return unit
    if sentences:
        unit["sentences"] = sentences
    return unit if sentences else None
'''


def legacy_namespace():
    namespace = dict(vars(t))
    exec(LEGACY_SOURCE, namespace)
    return namespace


CASES = [
    '', ' ', 'Simple literal prose. Another sentence. Final one.',
    'Numbers 3.14, -42, +5, −7, 1,000, .5 and 4e-7 with 25‰.',
    'Unicode 中文😀 café cafe\u0301 العربية ١٢ and\u00a0spaces.',
    'First\r\nline.\nSecond\tline.\v\fLast.', '<script> "quotes" & _ ^ # {} []',
    'Visible. % hidden \\textbf{comment}\r\nNext.', r'Escaped \% and \&.',
    r'Inline $x_1 + 3.14$ and \(y\). Next $z$.',
    r'\textbf{Bold \emph{nested 42}} and \underline{word}.',
    r'\href{https://example.org/a?q=1&x=2}{safe \emph{link 42}}.',
    r'\href{javascript:alert(1)}{unsafe} and \href{http://[bad}{bad}.',
    r'\cite{same,other}, \ref{sec:known}, \label{ignored}, \unknown{x}.',
    r'\verb| a% b | and text~with spacing.', r'\textbf{unterminated',
    'Reserved [[LR_7]] placeholder.', None, 42, b'bytes',
]


def outcome(function):
    try:
        return ('value', function())
    except Exception as error:
        return ('error', type(error), str(error))


@pytest.mark.parametrize('raw', CASES)
@pytest.mark.parametrize('start', [0, 7])
def test_protected_sentence_matches_frozen_oracle_and_callback_order(raw, start):
    results, calls = [], []
    for function in (legacy_namespace()['_protected_sentence'], t._protected_sentence):
        observed = []
        def render(value):
            observed.append(value)
            return '<context>' + value + '</context>'
        results.append(outcome(lambda: function(raw, render, token_start=start)))
        calls.append(observed)
    assert results[0] == results[1]
    assert calls[0] == calls[1]


@pytest.fixture
def project(tmp_path):
    from latex_review.contract import ComparisonSource
    from latex_review.sources import ProjectSource
    from latex_review.structure import parse_project
    (tmp_path / 'main.tex').write_text(r'\begin{document}\section{Known}\label{sec:known}Plain paragraph.\end{document}')
    return parse_project(ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path))).expand())


def node(raw, kind='paragraph'):
    from latex_review.contract import ReviewNode, SourceLocation
    from latex_review.structure import ParsedNode
    return ParsedNode(ReviewNode('unit', kind, raw, None, (), (), SourceLocation('main.tex', 1, 1)), 0, 1, ())


@pytest.mark.parametrize('raw', CASES)
@pytest.mark.parametrize('kind,side', [('paragraph','new'), ('section','old'), ('figure','new')])
def test_unit_and_diagnostics_match_frozen_oracle(project, raw, kind, side):
    if type(raw) is str and kind == 'section': raw = r'\section{' + raw + '}'
    if type(raw) is str and kind == 'figure': raw = r'\begin{figure}\caption{' + raw + r'}\end{figure}'
    outputs = []
    for function in (legacy_namespace()['_build_unit'], t._build_unit):
        diagnostics = []
        result = outcome(lambda: function(project, node(raw, kind), side, 'change', {2}, diagnostics=diagnostics))
        outputs.append((result, diagnostics))
    assert outputs[0] == outputs[1]


def test_protected_results_remain_fresh_nested_mutable_values():
    first = t._protected_sentence(r'Number 42 and $x$.', str)
    expected = deepcopy(first)
    first['protected'][0]['html'] = 'mutated'
    first['protected'].append({'unexpected': True})
    assert t._protected_sentence(r'Number 42 and $x$.', str) == expected


def test_plain_unit_avoids_all_three_local_import_paths(monkeypatch):
    imported = []
    original = builtins.__import__
    def tracked(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1 and name in {'preview','structure','structured_diff'}:
            imported.append((name, tuple(fromlist)))
        return original(name, globals, locals, fromlist, level)
    item = node('First 42 words. Next ordinary sentence. Final 3 words.')
    old = legacy_namespace()['_build_unit']
    monkeypatch.setattr(builtins, '__import__', tracked)
    expected = old(None, item, 'new', 'change', set())
    old_imports = list(imported)
    imported.clear()
    assert t._build_unit(None, item, 'new', 'change', set()) == expected
    assert imported == []
    assert ('preview', ('_inline_html',)) in old_imports
    assert ('structured_diff', ('_caption',)) in old_imports
    assert sum(name == 'structure' for name, fields in old_imports) == 1 + len(expected['sentences'])


@pytest.mark.parametrize('kind,raw', [('paragraph',r'$x$. Next $y$.'), ('section',r'\section{$x$. Next $y$.}'), ('figure',r'\caption{$x$. Next $y$.}')])
@pytest.mark.parametrize('side', ['old', 'new'])
@pytest.mark.parametrize('fail', [False, True])
def test_renderer_binding_context_fresh_lists_and_dynamic_source_match_legacy(monkeypatch, kind, raw, side, fail):
    from latex_review import preview
    from latex_review.contract import SourceLocation
    original_split = t.split_sentences
    def unexpected(*args, **kwargs):
        raise AssertionError('renderer must remain bound for the complete unit')
    def split(value):
        monkeypatch.setattr(preview, '_inline_html', unexpected)
        return original_split(value)
    monkeypatch.setattr(t, 'split_sentences', split)
    old = legacy_namespace()['_build_unit']
    outputs = []
    for function in (old, t._build_unit):
        context = SimpleNamespace(source_reads=0)
        class Review:
            id = 'callback-unit'
            type = kind
            raw_latex = raw
            plain_text = ''
            @property
            def source(self):
                context.source_reads += 1
                return SourceLocation('context.tex', context.source_reads, context.source_reads)
        item = SimpleNamespace(review=Review())
        seen = []
        def render(value, project, side, inline, *, source):
            seen.append((value, project is context, side, len(inline), source))
            inline.append('must not survive into another callback')
            monkeypatch.setattr(preview, '_inline_html', unexpected)
            if fail and len(seen) == 2:
                raise t.TranslationError('context renderer declined')
            return '<bound>' + value + '</bound>'
        monkeypatch.setattr(preview, '_inline_html', render)
        diagnostics = []
        result = function(context, item, side, 'change', set(), diagnostics=diagnostics)
        outputs.append((result, seen, context.source_reads, diagnostics))
    assert outputs[0] == outputs[1]
    assert len(outputs[1][1]) == 2
    assert all(call[1] and call[2] == side and call[3] == 0 for call in outputs[1][1])
    if fail:
        assert outputs[1][0]['error'] == 'context renderer declined'
        assert len(outputs[1][3]) == 1


@pytest.mark.parametrize('kind,raw,argument_imports,caption_imports', [
    ('paragraph', r'$x$.', 1, 0),
    ('section', r'\section{Plain heading words.}', 1, 0),
    ('figure', r'\caption{Plain caption words.}', 0, 1),
])
def test_only_required_branch_helpers_are_imported(project, monkeypatch, kind, raw, argument_imports, caption_imports):
    imported = []
    original = builtins.__import__
    def tracked(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1:
            imported.append((name, tuple(fromlist)))
        return original(name, globals, locals, fromlist, level)
    item = node(raw, kind)
    monkeypatch.setattr(builtins, '__import__', tracked)
    assert t._build_unit(project, item, 'new', 'change', set())
    assert imported.count(('structure', ('_argument', '_masked'))) == argument_imports
    assert imported.count(('structured_diff', ('_caption',))) == caption_imports
