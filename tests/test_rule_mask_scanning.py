"""Event skipping must preserve the original rule scanner's exact mask."""
import random
from types import SimpleNamespace

import latex_review.rules as rules


def test_rule_mask_event_scan_matches_character_scan(monkeypatch):
    event = rules._MASK_EVENT

    class CharacterScan:
        @staticmethod
        def search(text, index):
            return SimpleNamespace(start=lambda: index) if index < len(text) else None

    samples = [
        'plain Unicode 中文 prose\r\n\r\nnext paragraph',
        r'escaped \% text \\% hidden' + '\r\nnext',
        r'\verb|\label{not-real}%| text \label{real}',
        r'\verb*+%not-comment+\ref{x}',
        '\\begin{verbatim}\n% raw \\label{x}\n\\end{verbatim}\r\n\\label{y}',
        '\\verb\n%comment\r\n\\input{a}',
        '\\begin{lstlisting}\n\\ref{hidden}\n\\end{lstlisting}',
    ]
    randomizer = random.Random(55)
    alphabet = 'word中文%\\\r\n\t{}[]$*| '
    samples += [''.join(randomizer.choices(alphabet, k=400)) for _ in range(100)]
    for text in samples:
        monkeypatch.setattr(rules, '_MASK_EVENT', CharacterScan())
        original = rules._rule_mask(text)
        monkeypatch.setattr(rules, '_MASK_EVENT', event)
        assert rules._rule_mask(text) == original


def test_prose_view_event_scan_matches_character_scan(tmp_path, monkeypatch):
    from latex_review.checks import knowledge_rules
    from latex_review.checks.document import build_check_document
    from latex_review.checks.snapshot import capture_snapshot

    text = ('\\documentclass{article}\n\\begin{document}\n'
            'Unicode 中文 prose. \\textbf{Bold words} \\ref{x} \\label{x}\r\n\r\n'
            'Inline $x+y$ then \\(z\\) and escaped \\$ text. % hidden $q$\r\n'
            '\\begin{equation}a=b\\end{equation}\n'
            '\\verb|$raw$| and \\url{https://example.com/$x$}\n'
            '\\end{document}\n')
    (tmp_path / 'main.tex').write_bytes(text.encode())
    document = build_check_document(capture_snapshot('main.tex', root=tmp_path))
    optimized = knowledge_rules._view(document)

    class CharacterScan:
        @staticmethod
        def search(text, index, end):
            return SimpleNamespace(start=lambda: index) if index < end else None

    monkeypatch.setattr(knowledge_rules, '_PROSE_EVENT', CharacterScan())
    assert knowledge_rules._view(document) == optimized
