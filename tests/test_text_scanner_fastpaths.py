"""Differential fast scanner tests against the original character-step paths."""
from random import Random
from types import SimpleNamespace

from latex_review import text_diff


class EveryCharacter:
    def search(self, raw, start):
        return SimpleNamespace(start=lambda: start)


class NoAsciiRun:
    def match(self, raw, start):
        return None


class ForceCommentScan(str):
    def __contains__(self, value):
        return True if value == '%' else super().__contains__(value)


def samples():
    parts = ['Ordinary prose ', 'Mr. Smith ', 'e.g. text ', '3.14 ', 'a--b ',
             "don't l’esprit a''b ", 'café cafe\u0301 αβ 中文😀 ١٢ ', '\r\n',
             'One. Two! Three? ', r'\textbf{one. two}', r'\citep[a][b]{x}',
             r'\verb|x.y%|', r'\begin{verbatim}x.y%\end{verbatim}',
             r'$x.y$', r'\(x.y\)', r'\[x.y\]', '\\% escaped ',
             '% comment\n', '\\\\% comment\r\n', '{}[]~_^&# ', '\\', '$', '%']
    yield from parts
    rng = Random(425)
    for _ in range(300):
        yield ''.join(rng.choice(parts) for _ in range(rng.randrange(1, 12)))


def test_sentence_event_jumps_match_original_character_steps(monkeypatch):
    event = text_diff._SENTENCE_EVENT
    for raw in samples():
        monkeypatch.setattr(text_diff, '_SENTENCE_EVENT', event)
        # Exercise the scanner itself, not a memoized result from either path.
        fast = text_diff._split_sentences(raw)
        monkeypatch.setattr(text_diff, '_SENTENCE_EVENT', EveryCharacter())
        assert fast == text_diff._split_sentences(raw), repr(raw)


def test_ascii_word_runs_match_unicode_character_scanning(monkeypatch):
    pattern = text_diff._ASCII_WORD
    for raw in samples():
        monkeypatch.setattr(text_diff, '_ASCII_WORD', pattern)
        fast = text_diff.scan_latex(raw)
        monkeypatch.setattr(text_diff, '_ASCII_WORD', NoAsciiRun())
        assert fast == text_diff.scan_latex(raw), repr(raw)


def test_no_comment_shortcut_matches_complete_comment_scanner():
    for raw in samples():
        assert text_diff._without_comments(raw) == text_diff._without_comments(ForceCommentScan(raw))
