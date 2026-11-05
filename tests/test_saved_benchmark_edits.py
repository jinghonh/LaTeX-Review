"""The saved benchmark retains comments and independently proves visible edits."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

import pytest


path = Path(__file__).resolve().parents[1] / 'Docs/design/writing-check/evidence/benchmark_saved_checks.py'
spec = importlib.util.spec_from_file_location('saved_benchmark', path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_comment_recipe_exact_and_prose_changes_one_word_only(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    benchmark.generate(root, 2, 10)
    path = root / 'part0.tex'
    original = path.read_bytes()
    assert benchmark.write_saved_revision(path, 1, 'comment') is None
    assert path.read_bytes() == original + b'% saved revision 1\n'
    path.write_bytes(original)
    before = path.read_text().split()
    for index in range(1, 4):
        change = benchmark.write_saved_revision(path, index, 'prose')
        current = path.read_text().split()
        assert len(before) == len(current)
        assert sum(a != b for a, b in zip(before, current)) == 1
        assert path.read_text().count('\n\n') == original.decode().count('\n\n')
        assert change['after_text'] in path.read_text()
        assert change['before_text'] not in path.read_text()
        before = current
    assert sorted(p.name for p in root.iterdir()) == ['main.tex', 'part0.tex']


def test_prose_does_not_guess_missing_previous_revision(tmp_path):
    path = tmp_path / 'part0.tex'
    path.write_text('Unrelated text.')
    with pytest.raises(RuntimeError, match='exactly one'):
        benchmark.write_saved_revision(path, 1, 'prose')
    assert path.read_text() == 'Unrelated text.'


def test_visible_proof_requires_current_manifest_and_changed_text(tmp_path):
    change = {'before_text': 'Old sentence.', 'after_text': 'New sentence.', 'changed_ordinary_word': True}
    def publish(text, version='v2'):
        (tmp_path / 'annotation-content.json').write_text(json.dumps({'version_id': version, 'units': [
            {'kind': 'text', 'id': 'u', 'node_id': 'n', 'order': 0, 'text': text}]}))
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('New sentence.', 'v1')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('Old sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('Old sentence. New sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('New sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 2) is None
    proof = benchmark.visible_publication_proof(tmp_path, change, 'v2', 1)
    assert proof['verified_visible_text_change']
    assert proof['unit_text_sha256'] == benchmark.digest(b'New sentence.')
    assert proof['manifest_version_id'] == 'v2' and proof['unit_id'] == 'u'


def isolate_benchmark_environment(tmp_path, monkeypatch):
    # main changes these process variables; register each for pytest restoration
    # BEFORE invocation, rather than leaking the helper's deleted temp namespace.
    for name in ('LATEX_REVIEW_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        monkeypatch.setenv(name, str(tmp_path / ('initial-' + name)))


def test_owned_alias_root_is_canonical_without_weakening_production_state_guard(tmp_path, monkeypatch):
    from latex_review.local_paths import state_directory
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with tempfile.TemporaryDirectory(dir=alias) as folder:
        lexical = Path(folder)
        monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(lexical / 'entry-state'))
        with pytest.raises(ValueError, match='本地状态目录不可含符号链接'):
            state_directory()
        canonical = benchmark.canonical_private_root(folder)
        assert canonical == lexical.resolve() and canonical.parent == real.resolve()
        monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(canonical / 'entry-state'))
        assert state_directory() == canonical / 'entry-state'
        assert not any(path.is_symlink() for path in (canonical, *canonical.parents))


def test_tiny_real_saved_helper_uses_canonical_owned_alias_workspace(tmp_path, monkeypatch):
    """Correctness only: one tiny prose save, never reference latency evidence."""
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(tempfile, 'tempdir', str(alias))
    isolate_benchmark_environment(tmp_path, monkeypatch)
    output = tmp_path / 'result.json'
    monkeypatch.setattr(benchmark.sys, 'argv', [str(path), '--files', '2', '--paragraphs', '10',
                                             '--saves', '1', '--edit-kind', 'prose', '--output', str(output)])
    assert benchmark.main() == 0
    result = json.loads(output.read_text())
    assert result['complete'] and len(result['samples']) == 1
    assert result['samples'][0]['authoritative_match']
    proof = result['samples'][0]['visible_change_proof']
    assert proof['verified_visible_text_change'] and proof['published_text_units'] == 10
    assert 'startup_diagnostic' not in result
    assert result['source_paths_changed_during_measurement'] == []
    assert result['cleanup'] == {'owned_entry_exit_code': 0, 'owned_entry_exited': True,
                                 'owned_worker_exited': True, 'isolated_endpoint_removed': True}
    assert not list(real.iterdir())  # Only the helper's owned workspace was removed.


def test_startup_stderr_diagnostic_allowlists_conditions_and_omits_all_raw_secrets(tmp_path):
    secret = 'opaque-capability-DO-NOT-PUBLISH'
    stderr = ('Traceback (most recent call last):\n'
              'Authorization: Bearer ' + secret + '\nCookie: ' + secret + '\n'
              '{"token":"' + secret + '","nonce":"' + secret + '"}\n'
              '\x1b[31mPassword:\x1b[0m\n' + secret + '\n'
              'Unlabeled multiline value: \n' + secret + '\n'
              'ValueError: 本地状态目录不可含符号链接\n')
    (tmp_path / 'entry-subprocess.log').write_text(stderr)
    # These sensitive operational logs must NEVER be consulted by diagnostics.
    (tmp_path / 'entry.log').write_text('Permission denied ' + secret)
    state = tmp_path / 'entry-state'
    state.mkdir()
    (state / 'entry.log').write_text('No module named ' + secret)
    result = benchmark.entry_startup_diagnostic(tmp_path)
    encoded = json.dumps(result, ensure_ascii=False)
    assert result['recognized_conditions'] == ['state_directory_symlink_ancestor']
    assert result['recognized_exception_classes'] == ['ValueError']
    assert result['raw_text_omitted'] and result['read_status'] == 'read'
    assert not result['tail_truncated']
    assert secret not in encoded and 'Authorization' not in encoded and 'Cookie' not in encoded
    assert 'Password' not in encoded and 'nonce' not in encoded and 'token' not in encoded
    assert len(encoded.encode()) < 4096


def test_startup_stderr_is_bounded_and_keeps_only_static_tail_classification(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, '_STARTUP_STDERR_MAX_BYTES', 128)
    raw = b'opaque-secret-value\n' * 100000 + 'ValueError: 本地状态目录不可含符号链接\n'.encode()
    (tmp_path / 'entry-subprocess.log').write_bytes(raw)
    result = benchmark.entry_startup_diagnostic(tmp_path)
    assert result['read_bytes'] == 128 and result['observed_log_size_bytes'] == len(raw)
    assert result['tail_truncated']
    assert result['recognized_conditions'] == ['state_directory_symlink_ancestor']
    assert result['recognized_exception_classes'] == ['ValueError']
    assert 'opaque-secret-value' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'directory', 'fifo'])
def test_startup_stderr_refuses_non_owned_regular_log_shapes(tmp_path, kind):
    log = tmp_path / 'entry-subprocess.log'
    if kind == 'symlink':
        target = tmp_path / 'entry.log'
        target.write_text('ValueError: 本地状态目录不可含符号链接')
        log.symlink_to(target)
    elif kind == 'directory':
        log.mkdir()
    elif kind == 'fifo':
        os.mkfifo(log)
    result = benchmark.entry_startup_diagnostic(tmp_path)
    expected = {'missing': 'unavailable', 'symlink': 'symlink_refused',
                'directory': 'non_regular_file_refused', 'fifo': 'non_regular_file_refused'}
    assert result['read_status'] == expected[kind]
    assert 'recognized_conditions' not in result and result['raw_text_omitted']


def test_startup_stderr_stat_failure_cannot_mask_original_startup_error(tmp_path, monkeypatch):
    def denied(self):
        raise PermissionError('sensitive-operating-system-message')
    monkeypatch.setattr(Path, 'is_symlink', denied)
    result = benchmark.entry_startup_diagnostic(tmp_path)
    assert result['read_status'] == 'unavailable'
    assert 'sensitive-operating-system-message' not in json.dumps(result)


def test_unknown_and_malformed_utf8_startup_stderr_never_persists_raw_values(tmp_path):
    (tmp_path / 'entry-subprocess.log').write_bytes(b'\xff\xfeopaque-secret\x00\nunknown failure\n')
    result = benchmark.entry_startup_diagnostic(tmp_path)
    assert result['read_status'] == 'read' and result['recognized_conditions'] == []
    assert result['recognized_exception_classes'] == []
    assert 'opaque-secret' not in json.dumps(result) and 'unknown failure' not in json.dumps(result)


def test_startup_failure_persists_and_prints_only_sanitized_owned_stderr(tmp_path, monkeypatch, capsys):
    from latex_review import local_client
    isolate_benchmark_environment(tmp_path, monkeypatch)
    secret = 'must-not-survive-error-artifact'
    class FailedEntry:
        pid = 123456789
        returncode = 4
        def poll(self):
            return 4
        def wait(self, timeout=None):
            return 4
    def failed_start(*args, **kwargs):
        kwargs['stderr'].write(('nonce=' + secret + '\n本地入口操作失败：本地状态目录不可含符号链接\n').encode())
        kwargs['stderr'].flush()
        return FailedEntry()
    monkeypatch.setattr(benchmark.subprocess, 'Popen', failed_start)
    monkeypatch.setattr(local_client, 'endpoint', lambda: (_ for _ in ()).throw(local_client.LocalError('not started')))
    output = tmp_path / 'failed.json'
    monkeypatch.setattr(benchmark.sys, 'argv', [str(path), '--files', '2', '--paragraphs', '10',
                                             '--saves', '1', '--output', str(output)])
    assert benchmark.main() == 1
    result = json.loads(output.read_text())
    assert not result['complete'] and result['samples'] == []
    diagnostic = result['startup_diagnostic']
    assert diagnostic['recognized_conditions'] == ['state_directory_symlink_ancestor', 'entry_operation_failed']
    assert diagnostic['entry_return_code_at_failure'] == diagnostic['entry_return_code_after_cleanup'] == 4
    assert result['error']['message'] == 'private entry startup failed'
    assert diagnostic['phase'] == 'entry'
    assert secret not in output.read_text() and secret not in capsys.readouterr().out


def test_worker_readiness_failure_also_retains_sanitized_entry_stderr_only(tmp_path, monkeypatch):
    from latex_review import local_client
    isolate_benchmark_environment(tmp_path, monkeypatch)
    class Entry:
        pid = 123456789
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.returncode = 0
            return 0
    def start(*args, **kwargs):
        kwargs['stderr'].write(b'RuntimeError: unknown startup diagnostic secret\n')
        kwargs['stderr'].flush()
        return Entry()
    def failed_ensure(*args, **kwargs):
        raise local_client.LocalError('controlled worker readiness failure', 503)
    monkeypatch.setattr(benchmark.subprocess, 'Popen', start)
    monkeypatch.setattr(local_client, 'endpoint', lambda: {'pid': Entry.pid})
    monkeypatch.setattr(local_client, 'ensure', failed_ensure)
    monkeypatch.setattr(local_client, 'stop', lambda: {})
    output = tmp_path / 'worker-failed.json'
    monkeypatch.setattr(benchmark.sys, 'argv', [str(path), '--files', '2', '--paragraphs', '10',
                                             '--saves', '1', '--output', str(output)])
    assert benchmark.main() == 1
    result = json.loads(output.read_text())
    assert not result['complete'] and result['samples'] == []
    diagnostic = result['startup_diagnostic']
    assert diagnostic['phase'] == 'worker_ready'
    assert diagnostic['recognized_exception_classes'] == ['RuntimeError']
    assert diagnostic['entry_return_code_at_failure'] is None and diagnostic['entry_return_code_after_cleanup'] == 0
    assert 'unknown startup diagnostic secret' not in output.read_text()
