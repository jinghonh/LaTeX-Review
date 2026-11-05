"""Plain translation text must preserve the exact token-walker contract."""
from random import Random

import pytest

from latex_review import translation


def test_plain_text_matches_legacy_token_walker(monkeypatch):
    helper = translation._plain_sentence_text
    parts = ['  words\tbetween\r\n ', '3.14 and 1,000 ', 'α 中文😀 café cafe\u0301 ١٢ ',
             '<script> "quoted" & symbols _^#{}[] ', 'a--b ', "don't l’esprit ",
             '\u00a0\u2003 \v\f', '% comment\r\n', '\\% escaped ',
             r'\textbf{number 42}', r'$x^2$', r'\verb| a% b |', 'a~b']
    rng = Random(425)
    raw_cases = ['', ' ', '42', ' 42 ', *parts]
    raw_cases += [''.join(rng.choice(parts) for _ in range(8)) for _ in range(100)]
    for raw in raw_cases:
        monkeypatch.setattr(translation, '_plain_sentence_text', helper)
        fast = translation._protected_sentence(raw, lambda value: '<safe>' + value, token_start=7)
        monkeypatch.setattr(translation, '_plain_sentence_text', lambda raw: None)
        assert fast == translation._protected_sentence(raw, lambda value: '<safe>' + value, token_start=7), repr(raw)


def test_plain_text_keeps_number_tokens_without_scanning(monkeypatch):
    def unexpected(*args):
        raise AssertionError('plain text must not tokenize word by word')
    monkeypatch.setattr(translation, 'scan_latex', unexpected)
    result = translation._protected_sentence('  Evaluation 42.\n', str, token_start=3)
    assert result['text'] == 'Evaluation [[LR_3]].'
    assert result['protected'] == [{'token': '[[LR_3]]', 'latex': '42', 'html': '42', 'kind': 'number'}]


def test_reserved_translation_tokens_are_not_silently_accepted():
    with pytest.raises(translation.TranslationError):
        translation._protected_sentence('Untrusted [[LR_3]] text.', str)
