"""Bounded raw-source memoization preserves the exact frozen sentence contract."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from latex_review import text_diff as module
from test_text_scanner_fastpaths import samples


@pytest.fixture(autouse=True)
def clear_memo():
    module._memoized_sentences.cache_clear()
    yield
    module._memoized_sentences.cache_clear()


def test_seeded_cold_and_hot_match_uncached_scanner(monkeypatch):
    values = list(samples()) + ['\ud800.', '😀' * 2000 + '.', 'x' * 5000 + '.']
    expected = [module._split_sentences(raw) for raw in values]
    assert [module.split_sentences(raw) for raw in values] == expected
    # The second pass must hit eligible inputs, but large values must still scan.
    scan = module._split_sentences
    def oversized_only(raw):
        assert len(raw.encode('utf-8', errors='surrogatepass')) > module._SENTENCE_CACHE_MAX_BYTES
        return scan(raw)
    monkeypatch.setattr(module, '_split_sentences', oversized_only)
    assert [module.split_sentences(raw) for raw in values] == expected


def test_values_are_frozen_and_threads_reuse_only_immutable_facts(monkeypatch):
    raw = 'First sentence. Second 😀 sentence.'
    expected = module.split_sentences(raw)
    with pytest.raises(FrozenInstanceError):
        expected[0].text = 'corrupt'
    monkeypatch.setattr(module, '_split_sentences', lambda raw: pytest.fail('warm call rescanned'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(module.split_sentences, [raw] * 16)) == [expected] * 16


def test_changed_bytes_and_oversized_unicode_do_not_reuse(monkeypatch):
    scan = module._split_sentences
    calls = []
    def counting(raw):
        calls.append(raw)
        return scan(raw)
    monkeypatch.setattr(module, '_split_sentences', counting)
    assert module.split_sentences('First.') != module.split_sentences('Other.')
    large = '😀' * 1100
    assert len(large) < 4096 < len(large.encode())
    assert module.split_sentences(large) == module.split_sentences(large)
    assert calls == ['First.', 'Other.', large, large]
    assert module._memoized_sentences.cache_info().currsize == 2


def test_entry_bound_and_raw_subclass_bypass():
    for i in range(8200):
        module.split_sentences(f'Unique sentence {i}.')
    info = module._memoized_sentences.cache_info()
    assert info.maxsize == info.currsize == 8192
    class Source(str):
        pass
    before = module._memoized_sentences.cache_info()
    assert module.split_sentences(Source('Subclass.')) == module._split_sentences('Subclass.')
    assert module._memoized_sentences.cache_info() == before
