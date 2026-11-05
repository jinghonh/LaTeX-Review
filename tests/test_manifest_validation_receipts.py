"""Pure exact-byte admission receipts must never become mutable/provenance caches."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from latex_review.annotations import content as c
from latex_review.annotations import MANIFEST, AnnotationError
from latex_review.checks.http import _status_content_identity
from latex_review import contract

LEGACY_VALIDATE = c.validate_content


@pytest.fixture(autouse=True)
def isolated_receipts():
    prior_main, prior_thread = sys.getprofile(), threading.getprofile()
    with c._VALIDATION_RECEIPT_LOCK:
        c._VALIDATION_RECEIPTS.clear()
    yield
    sys.setprofile(prior_main)
    threading.setprofile(prior_thread)
    with c._VALIDATION_RECEIPT_LOCK:
        c._VALIDATION_RECEIPTS.clear()


def manifest(version='a'):
    return {'schema_version': '1.0', 'project_id': 'b' * 64, 'entry': 'main.tex',
            'version_id': version * 64,
            'coordinate_system': 'utf16-normalized-whitespace-with-formula-atoms',
            'units': [{'id': 'first', 'kind': 'text', 'node_id': 'node', 'text': '😀\ufffc',
                       'raw_latex': 'alpha', 'selector': '#node', 'order': 0,
                       'source': {'file': 'main.tex', 'start_line': 1, 'end_line': 1},
                       'section_path': ['Section'], 'labels': ['label'], 'change_ids': [],
                       'atoms': [{'offset': 2, 'formula_node_id': 'math', 'raw_latex': 'x', 'selector': '#math'}],
                       'source_span': {'file': 'main.tex', 'start': 0, 'end': 5, 'sha256': 'd' * 64,
                                       'original_chunk': 'alpha', 'reliable': True, 'writable': True, 'reason': ''}}]}


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def legacy(raw, universal=False):
    text = raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw
    if universal and isinstance(text, str):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    return LEGACY_VALIDATE(json.loads(text))


def outcome(call):
    try:
        return ('returned', call())
    except Exception as error:
        return ('raised', type(error), str(error), getattr(error, 'pos', None),
                getattr(error, 'lineno', None), getattr(error, 'colno', None))


def counted_validator(monkeypatch):
    # Observe the canonical code without replacing it and disabling receipts.
    calls = []
    code = c.validate_content.__code__
    def profile(frame, event, arg):
        if event == 'call' and frame.f_code is code and frame.f_back.f_code is not legacy.__code__:
            calls.append(frame.f_locals['content'])
    sys.setprofile(profile)
    threading.setprofile(profile)
    return calls


def test_fresh_full_nested_tree_on_every_load_and_true_receipts_only(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    calls = counted_validator(monkeypatch)
    first = c.load_content(tmp_path)
    first['units'][0]['atoms'][0]['offset'] = 0
    first['units'][0]['source_span']['writable'] = False
    first['units'][0]['labels'].append('forged')
    first['units'][0]['source']['file'] = '/forged'
    second = c.load_content(tmp_path / 'report.html')
    assert second == manifest() and second is not first
    assert second['units'][0] is not first['units'][0]
    assert len(calls) == 1
    assert len(c._VALIDATION_RECEIPTS) == 1
    for (namespace, digest), receipt in c._VALIDATION_RECEIPTS.items():
        assert receipt is True and type(digest) is bytes and len(digest) == 32 and digest != raw
        assert isinstance(namespace, tuple)


def test_serialized_producer_meta_matches_legacy_and_ignores_later_caller_mutation():
    value = manifest()
    expected = c.content_meta(value)
    raw = encode(value)
    value['project_id'] = 'invalid'
    del value['units'][0]['selector']
    assert c._content_meta_from_serialized(raw) == expected
    assert c.decode_validated_content(raw) == manifest()
    with pytest.raises(AnnotationError): c.content_meta(value)


def test_producer_notifier_status_export_share_one_canonical_raw_validation(tmp_path, monkeypatch):
    value = manifest()
    expected = c.content_meta(value)
    raw = encode(value)
    (tmp_path / MANIFEST).write_bytes(raw)
    calls = counted_validator(monkeypatch)
    assert c._content_meta_from_serialized(raw) == expected
    c.load_content(tmp_path)
    c.load_content(tmp_path)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    assert _status_content_identity(handler)['version_id'] == value['version_id']
    c.load_content(tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize('case', [b'{bad', bytes([255]), 'duplicate_id', 'source_span_length'])
def test_serialized_meta_rejects_invalid_bytes_without_receipt(case):
    raw = encode(invalid(case)) if isinstance(case, str) else case
    expected = outcome(lambda: legacy(raw))
    for _ in range(2):
        assert outcome(lambda: c._content_meta_from_serialized(raw)) == expected
    assert not c._VALIDATION_RECEIPTS


def test_live_producer_validates_the_exact_written_bytes_at_existing_meta_point(tmp_path, monkeypatch):
    from latex_review import live
    from latex_review.contract import ComparisonSource
    from latex_review.sources import ProjectSource
    (tmp_path / 'main.tex').write_text(r'\documentclass{article}\begin{document}Hello serialized 😀 $x$.\end{document}', encoding='utf-8')
    output = tmp_path / 'preview'
    output.mkdir()
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)),
                           configuration={'compile': {'enabled': False}})
    inputs = []
    original = c._content_meta_from_serialized
    def meta(raw):
        assert raw == (output / MANIFEST).read_bytes()
        inputs.append(raw)
        return original(raw)
    monkeypatch.setattr(c, '_content_meta_from_serialized', meta)
    live.build(source, output)
    assert inputs == [(output / MANIFEST).read_bytes()]
    expected = c.content_meta(json.loads(inputs[0].decode('utf-8')))
    assert expected.encode('utf-8') in (output / 'report.html').read_bytes()


def test_unchanged_receipt_still_reads_and_hashes_actual_bytes_each_time(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / MANIFEST
    raw = encode(manifest())
    path.write_bytes(raw)
    read_bytes, sha = Path.read_bytes, c.sha256
    reads, hashes = [], []
    def read(self):
        reads.append(self)
        return read_bytes(self)
    def hashed(value):
        hashes.append(value)
        return sha(value)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(c, 'sha256', hashed)
    calls = counted_validator(monkeypatch)
    c.load_content(tmp_path)
    c.load_content(tmp_path)
    assert reads == [path, path] and hashes == [raw, raw] and len(calls) == 1


def test_both_notifier_reads_status_miss_and_export_share_one_predicate(tmp_path, monkeypatch):
    (tmp_path / MANIFEST).write_bytes(encode(manifest()))
    calls = counted_validator(monkeypatch)
    first = c.load_content(tmp_path)
    second = c.load_content(tmp_path)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    assert _status_content_identity(handler) == {'entry': 'main.tex', 'version_id': 'a' * 64}
    exported = c.load_content(tmp_path)
    assert exported == first == second and len(calls) == 1
    assert exported['units'] is not first['units'] and first['units'] is not second['units']


def test_same_size_restored_mtime_changes_are_read_and_validated(tmp_path, monkeypatch):
    path = tmp_path / MANIFEST
    path.write_bytes(encode(manifest()))
    calls = counted_validator(monkeypatch)
    assert c.load_content(tmp_path)['version_id'] == 'a' * 64
    stat = path.stat()
    path.write_bytes(encode(manifest('c')))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert c.load_content(tmp_path)['version_id'] == 'c' * 64 and len(calls) == 2
    raw = path.read_bytes().replace(b'"order": 0', b'"order": -1')
    # Preserve total length, too, by removing one otherwise insignificant space.
    raw = raw.replace(b'": ', b'":', 1)
    assert len(raw) == stat.st_size
    path.write_bytes(raw)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    for _ in range(2):
        with pytest.raises(AnnotationError):
            c.load_content(tmp_path)
    assert len(calls) == 4 and len(c._VALIDATION_RECEIPTS) == 2


INVALID_CASES = ['duplicate_id', 'duplicate_order', 'boolean_order', 'units_not_list', 'bad_kind',
                 'split_surrogate_atom', 'atom_outside', 'duplicate_atom', 'bad_atom_type',
                 'bad_source_location', 'source_span_length', 'source_span_zero', 'bad_span_type',
                 'writable_unreliable', 'unexplained_unreliable', 'formula_text', 'bad_version', 'extra_key']


def invalid(case):
    value = manifest()
    unit = value['units'][0]
    if case in ('duplicate_id', 'duplicate_order'):
        other = deepcopy(unit)
        if case == 'duplicate_id': other['order'] = 1
        else: other['id'] = 'second'
        value['units'].append(other)
    elif case == 'boolean_order': unit['order'] = False
    elif case == 'units_not_list': value['units'] = {}
    elif case == 'bad_kind': unit['kind'] = 'unknown'
    elif case == 'split_surrogate_atom': unit['atoms'][0]['offset'] = 1
    elif case == 'atom_outside': unit['atoms'][0]['offset'] = 3
    elif case == 'duplicate_atom': unit['atoms'].append(deepcopy(unit['atoms'][0]))
    elif case == 'bad_atom_type': unit['atoms'][0]['offset'] = False
    elif case == 'bad_source_location': unit['source']['file'] = '/absolute.tex'
    elif case == 'source_span_length': unit['source_span']['end'] = 6
    elif case == 'source_span_zero': unit['source_span']['end'] = 0
    elif case == 'bad_span_type': unit['source_span']['writable'] = 'yes'
    elif case == 'writable_unreliable': unit['source_span']['reliable'] = False
    elif case == 'unexplained_unreliable': unit['source_span'].update(reliable=False, writable=False)
    elif case == 'formula_text': unit['kind'] = 'formula'
    elif case == 'bad_version': value['version_id'] = 'g' * 64
    elif case == 'extra_key': value['unrecognized'] = True
    return value


@pytest.mark.parametrize('case', INVALID_CASES)
def test_invalid_admission_matches_legacy_and_is_never_receipted(case, monkeypatch):
    raw = encode(invalid(case))
    expected = outcome(lambda: legacy(raw))
    assert expected[0] == 'raised'
    calls = counted_validator(monkeypatch)
    for _ in range(2):
        assert outcome(lambda: c.decode_validated_content(raw)) == expected
    assert len(calls) == 2 and not c._VALIDATION_RECEIPTS


@pytest.mark.parametrize('raw', [b'{bad', b'\xff', b'\xef\xbb\xbf{}', b'{\r\n "x":\r\n}', b'{\r"x":}',
                               b'[]', b'null', b'1', b'{"schema_version":"1.0"}'])
@pytest.mark.parametrize('universal', [False, True])
def test_json_utf8_newline_errors_exactly_match_each_legacy_reader(raw, universal):
    for _ in range(2):
        assert outcome(lambda: c.decode_validated_content(raw, _universal_newlines=universal)) == outcome(lambda: legacy(raw, universal))
    assert not c._VALIDATION_RECEIPTS


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_valid_whitespace_newlines_share_receipt_and_preserve_unicode_values(newline, monkeypatch):
    value = manifest()
    value['units'][0]['labels'] = ['literal CR\rLF\n and 😀']
    raw = json.dumps(value, indent=2, ensure_ascii=False).replace('\n', newline).encode('utf-8')
    calls = counted_validator(monkeypatch)
    assert c.decode_validated_content(raw) == legacy(raw)
    assert c.decode_validated_content(raw, _universal_newlines=True) == legacy(raw, True)
    assert len(calls) == 1


def test_direct_validator_and_mutable_producer_meta_never_consult_receipts(monkeypatch):
    value = manifest()
    calls = counted_validator(monkeypatch)
    c.decode_validated_content(encode(value))
    value['units'][0]['order'] = -1
    with pytest.raises(AnnotationError): c.content_meta(value)
    with pytest.raises(AnnotationError): c.validate_content(value)
    assert len(calls) == 3


def test_namespace_schema_and_validator_identity_invalidate_receipt_and_status(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    c.decode_validated_content(raw)
    _status_content_identity(handler)
    def reject(value):
        raise AnnotationError('replacement validator')
    with monkeypatch.context() as patch:
        patch.setattr(c, 'validate_content', reject)
        with pytest.raises(AnnotationError, match='replacement validator'): c.load_content(tmp_path)
        with pytest.raises(AnnotationError, match='replacement validator'): _status_content_identity(handler)
    with monkeypatch.context() as patch:
        patch.setattr(c, 'SCHEMA_VERSION', 'different-schema')
        with pytest.raises(AnnotationError): c.decode_validated_content(raw)
        with pytest.raises(AnnotationError): _status_content_identity(handler)


def test_namespace_change_during_admission_cannot_receipt_the_wrong_predicate(monkeypatch):
    raw = encode(manifest())
    namespace = c._validation_receipt_namespace
    def reject(value):
        raise AnnotationError('replacement during admission')
    def transition(validator=None):
        if validator is not None:
            monkeypatch.setattr(c, 'validate_content', reject)
        return namespace(validator)
    with monkeypatch.context() as patch:
        patch.setattr(c, '_validation_receipt_namespace', transition)
        assert c.decode_validated_content(raw) == manifest()
    assert not c._VALIDATION_RECEIPTS
    with pytest.raises(AnnotationError, match='replacement during admission'):
        c.decode_validated_content(raw)


def test_namespace_in_place_validator_code_change_does_not_reuse_old_receipt(monkeypatch):
    raw = encode(manifest())
    c.decode_validated_content(raw)
    def replacement(value):
        raise ValueError('changed-code')
    monkeypatch.setattr(c.validate_content, '__code__', replacement.__code__)
    with pytest.raises(ValueError, match='changed-code'):
        c.decode_validated_content(raw)


def test_namespace_source_location_predicate_replacement_requires_revalidation(monkeypatch):
    raw = encode(manifest())
    c.decode_validated_content(raw)
    def reject(self):
        raise ValueError('changed source location predicate')
    monkeypatch.setattr(contract.SourceLocation, '__post_init__', reject)
    with pytest.raises(AnnotationError): c.decode_validated_content(raw)


def test_repairing_validator_never_receipts_unvalidated_raw_or_status_identity(tmp_path, monkeypatch):
    value = manifest()
    del value['units'][0]['selector']
    raw = encode(value)
    (tmp_path / MANIFEST).write_bytes(raw)
    calls = []
    def repair(decoded):
        calls.append(decoded)
        decoded['units'][0]['selector'] = '#repaired'
        return LEGACY_VALIDATE(decoded)
    monkeypatch.setattr(c, 'validate_content', repair)
    for _ in range(2):
        assert 'latex-review-annotation-version' in c._content_meta_from_serialized(raw)
        assert c.decode_validated_content(raw)['units'][0]['selector'] == '#repaired'
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    for _ in range(2):
        assert _status_content_identity(handler)['entry'] == 'main.tex'
    assert len(calls) == 6 and not c._VALIDATION_RECEIPTS
    assert not hasattr(handler.server, '_checks_status_identity')


@pytest.mark.parametrize('helper', ['_text', '_integer', '_slice', 'utf16_length'])
def test_inplace_helper_code_change_invalidates_global_and_status_receipts(helper, tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    c.decode_validated_content(raw)
    _status_content_identity(handler)
    def replaced(*args, **kwargs):
        raise ValueError('helper-code')
    monkeypatch.setattr(getattr(c, helper), '__code__', replaced.__code__)
    with pytest.raises(ValueError, match='helper-code'): c.decode_validated_content(raw)
    with pytest.raises(ValueError, match='helper-code'): _status_content_identity(handler)


def test_inplace_source_location_code_and_constructor_defaults_invalidate_receipts(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    c.decode_validated_content(raw)
    _status_content_identity(handler)
    def replaced(*args, **kwargs):
        raise ValueError('post-init-code')
    with monkeypatch.context() as patch:
        patch.setattr(contract.SourceLocation.__post_init__, '__code__', replaced.__code__)
        with pytest.raises(AnnotationError): c.decode_validated_content(raw)
        with pytest.raises(AnnotationError): _status_content_identity(handler)
    with monkeypatch.context() as patch:
        patch.setattr(contract.SourceLocation.__init__, '__defaults__', (None, None, -1.0, None))
        with pytest.raises(AnnotationError): c.decode_validated_content(raw)
        with pytest.raises(AnnotationError): _status_content_identity(handler)


def test_noncanonical_source_predicate_repairs_always_run_without_receipt(monkeypatch):
    from latex_review.annotations import _validation
    raw = encode(manifest())
    original = _validation._integer
    calls = []
    def integer(value, name, minimum=0):
        calls.append(value)
        return original(value, name, minimum)
    monkeypatch.setattr(_validation, '_integer', integer)
    c.decode_validated_content(raw)
    c.decode_validated_content(raw)
    assert calls and not c._VALIDATION_RECEIPTS


def test_nonstandard_raw_types_are_freshly_validated_without_receipts(monkeypatch):
    raw = encode(manifest())
    calls = counted_validator(monkeypatch)
    class DifferentBytes(bytes):
        pass
    for value in (bytearray(raw), DifferentBytes(raw), raw.decode('utf-8')):
        assert c.decode_validated_content(value) == manifest()
        assert c.decode_validated_content(value) == manifest()
    assert len(calls) == 6 and not c._VALIDATION_RECEIPTS


def test_self_restoring_decoder_cannot_claim_an_existing_canonical_receipt(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    c.decode_validated_content(raw)
    _status_content_identity(handler)
    original = json.loads
    def self_restoring(text):
        value = original(text)
        del value['units'][0]['selector']
        json.loads = original
        return value
    for read in (lambda: c.decode_validated_content(raw), lambda: _status_content_identity(handler)):
        monkeypatch.setattr(json, 'loads', self_restoring)
        with pytest.raises(AnnotationError): read()
        assert json.loads is original


def test_nonstandard_decoder_cannot_reuse_a_byte_receipt(monkeypatch):
    raw = encode(manifest())
    c.decode_validated_content(raw)
    loads = json.loads
    def changed(text):
        value = loads(text)
        value['coordinate_system'] = 'invalid'
        return value
    monkeypatch.setattr(json, 'loads', changed)
    with pytest.raises(AnnotationError): c.decode_validated_content(raw)


def test_nonempty_decoder_memo_disables_warm_same_handler_hits_and_stores(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    assert _status_content_identity(handler)['entry'] == 'main.tex'
    warmed = handler.server._checks_status_identity
    calls = counted_validator(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setitem(json._default_decoder.memo, 'nonstandard-policy', True)
        assert _status_content_identity(handler)['entry'] == 'main.tex'
        assert _status_content_identity(handler)['entry'] == 'main.tex'
        assert len(calls) == 2
        assert handler.server._checks_status_identity is warmed
    assert _status_content_identity(handler)['entry'] == 'main.tex'
    assert len(calls) == 2


def test_decoder_keyword_policy_change_cannot_reuse_canonical_receipt(tmp_path, monkeypatch):
    raw = encode(manifest())
    (tmp_path / MANIFEST).write_bytes(raw)
    handler = SimpleNamespace(directory=tmp_path, server=SimpleNamespace())
    c.decode_validated_content(raw)
    _status_content_identity(handler)
    def remove_selector(value):
        value.pop('selector', None)
        return value
    monkeypatch.setitem(json.loads.__kwdefaults__, 'object_hook', remove_selector)
    with pytest.raises(AnnotationError): c.decode_validated_content(raw)
    with pytest.raises(AnnotationError): _status_content_identity(handler)


def test_nonstandard_nonstrict_decoder_does_not_share_different_newline_trees(monkeypatch):
    value = manifest()
    value['units'][0]['raw_latex'] = value['units'][0]['source_span']['original_chunk'] = 'a\r\nb'
    value['units'][0]['source_span']['end'] = 4
    raw = encode(value).replace(b'a\\r\\nb', b'a\r\nb')
    monkeypatch.setattr(json, '_default_decoder', json.JSONDecoder(strict=False))
    assert c.decode_validated_content(raw) == legacy(raw)
    with pytest.raises(AnnotationError): c.decode_validated_content(raw, _universal_newlines=True)
    assert not c._VALIDATION_RECEIPTS


def test_receipt_lru_is_bounded_and_evicted_bytes_revalidate(monkeypatch):
    monkeypatch.setattr(c, '_VALIDATION_RECEIPT_LIMIT', 3)
    calls = counted_validator(monkeypatch)
    raws = [encode(manifest(character)) for character in 'abcde']
    for raw in raws: c.decode_validated_content(raw)
    assert len(c._VALIDATION_RECEIPTS) == 3 and len(calls) == 5
    c.decode_validated_content(raws[-1])
    assert len(calls) == 5
    c.decode_validated_content(raws[0])
    assert len(calls) == 6 and len(c._VALIDATION_RECEIPTS) == 3


def test_receipt_lru_hit_moves_old_bytes_to_recent_position(monkeypatch):
    monkeypatch.setattr(c, '_VALIDATION_RECEIPT_LIMIT', 3)
    calls = counted_validator(monkeypatch)
    raws = [encode(manifest(character)) for character in 'abcd']
    for raw in raws[:3]: c.decode_validated_content(raw)
    c.decode_validated_content(raws[0])
    c.decode_validated_content(raws[3])
    assert len(calls) == 4
    c.decode_validated_content(raws[0])
    assert len(calls) == 4
    c.decode_validated_content(raws[1])
    assert len(calls) == 5


def test_nonstandard_validator_return_value_is_not_replaced_by_a_cached_tree(monkeypatch):
    calls = []
    def copied(value):
        calls.append(value)
        return deepcopy(LEGACY_VALIDATE(value))
    monkeypatch.setattr(c, 'validate_content', copied)
    raw = encode(manifest())
    assert c.decode_validated_content(raw) == manifest()
    assert c.decode_validated_content(raw) == manifest()
    assert len(calls) == 2 and not c._VALIDATION_RECEIPTS


def test_concurrent_receipts_are_bounded_and_all_returned_objects_are_independent(monkeypatch):
    monkeypatch.setattr(c, '_VALIDATION_RECEIPT_LIMIT', 3)
    calls = counted_validator(monkeypatch)
    barrier = threading.Barrier(8)
    raw = encode(manifest())
    def read(index):
        barrier.wait(timeout=5)
        return c.decode_validated_content(raw)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(read, range(8)))
    assert all(value == manifest() for value in results)
    assert len({id(value['units'][0]['source_span']) for value in results}) == 8
    assert 1 <= len(calls) <= 8
    results[0]['units'][0]['source_span']['writable'] = False
    assert results[-1]['units'][0]['source_span']['writable'] is True
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(c.decode_validated_content, [encode(manifest(char)) for char in 'bcdefabc']))
    assert len(c._VALIDATION_RECEIPTS) <= 3 and all(value is True for value in c._VALIDATION_RECEIPTS.values())
