#!/usr/bin/env python3
"""Synthetic save -> authoritative current check, through the real private entry.

Run with the project virtualenv Python. No model, TeX engine, browser, or resident
user service is used. Filesystem and HTTP observations are upper bounds, NOT
instrumented internal build/queue durations. Output is checkpointed after saves.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, Request, build_opener

REPOSITORY = Path(__file__).resolve().parents[4]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def code_boundary():
    paths = sorted(p for p in (REPOSITORY / 'src' / 'latex_review').rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'})
    paths.extend([Path(__file__).resolve(), Path(__file__).with_name('benchmark_checks.py')])
    hashes = {str(p.relative_to(REPOSITORY)): digest(p.read_bytes()) for p in paths}
    return {'utc': datetime.now(timezone.utc).isoformat(), 'files': hashes,
            'aggregate_sha256': digest(json.dumps(hashes, sort_keys=True).encode())}


def canonical_private_root(folder):
    """Resolve only our newly allocated workspace, never a user's state path.

    macOS temporary roots may be lexical /var aliases for /private/var. Derive
    every owned state/cache/paper/report path from the canonical root so the
    production state-directory anti-symlink guard remains unchanged.
    """
    return Path(folder).resolve()


_STARTUP_STDERR_MAX_BYTES = 64 * 1024
_STARTUP_CONDITIONS = {
    'state_directory_symlink_ancestor': '本地状态目录不可含符号链接',
    'state_directory_not_absolute': '本地状态目录必须是绝对路径',
    'entry_operation_failed': '本地入口操作失败',
    'entry_port_invalid': '入口端口无效',
    'address_in_use': 'Address already in use',
    'permission_denied': 'Permission denied',
    'python_module_missing': 'No module named',
    'python_fatal_error': 'Fatal Python error',
}
_STARTUP_EXCEPTIONS = ('ValueError', 'RuntimeError', 'OSError', 'PermissionError',
                       'FileNotFoundError', 'ModuleNotFoundError', 'ImportError',
                       'SyntaxError', 'TypeError', 'AttributeError', 'LocalError',
                       'TimeoutError', 'ConnectionRefusedError', 'BrokenPipeError')


def entry_startup_diagnostic(private):
    """Bounded, allowlisted summary of ONLY the owned child stderr/stdout file.

    Do not preserve a raw tail: credentials can be unlabeled, multiline or split
    by truncation/ANSI escapes. Unknown text is always omitted, not heuristically
    redacted. In particular never open entry-state/entry.log or recipe files,
    which may contain capabilities/nonces. The fixed summary has bounded size.
    """
    summary = {'source': 'owned entry-subprocess.log only', 'raw_text_omitted': True}
    path = Path(private) / 'entry-subprocess.log'
    try:
        if path.is_symlink():
            return {**summary, 'read_status': 'symlink_refused'}
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                return {**summary, 'read_status': 'non_regular_file_refused'}
            os.lseek(descriptor, max(0, info.st_size - _STARTUP_STDERR_MAX_BYTES), os.SEEK_SET)
            raw = os.read(descriptor, _STARTUP_STDERR_MAX_BYTES)
        finally:
            os.close(descriptor)
    except OSError:
        return {**summary, 'read_status': 'unavailable'}
    text = raw.decode('utf-8', errors='replace')
    return {**summary, 'read_status': 'read', 'observed_log_size_bytes': info.st_size,
            'read_bytes': len(raw), 'tail_truncated': info.st_size > len(raw),
            'recognized_conditions': [name for name, message in _STARTUP_CONDITIONS.items() if message in text],
            'recognized_exception_classes': [name for name in _STARTUP_EXCEPTIONS
                                              if name + ':' in text or name + '：' in text]}


def host_sample():
    value = {'load_average': os.getloadavg(), 'logical_cpus': os.cpu_count()}
    if hasattr(os, 'sched_getaffinity'):
        value['affinity_cpus'] = len(os.sched_getaffinity(0))
    for name in ('cpu.max', 'cpu.stat', 'memory.max'):
        path = Path('/sys/fs/cgroup') / name
        if path.is_file():
            value['cgroup_' + name] = path.read_text().strip()
    return value


def generate(root, files, paragraphs):
    # Exact ordinary-prose fixture recipe from benchmark_checks.py.
    bodies = [[] for _ in range(files - 1)]
    for index in range(paragraphs):
        text = (f'Paragraph {index} describes a synthetic evaluation. '
                'The experiment records stable input evidence and separates a rule finding from an author decision. '
                'All measurements use a local generated document, with no external model calls. '
                'A reproducible benchmark does not establish scientific correctness.\n\n')
        bodies[index % len(bodies)].append(text)
    for index, body in enumerate(bodies):
        (root / f'part{index}.tex').write_bytes(''.join(body).encode())
    (root / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}\n' + ''.join(
        f'\\input{{part{index}}}\n' for index in range(len(bodies))) + '\\end{document}\n')
    return sum(p.stat().st_size for p in root.iterdir())


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_saved_revision(path, index, edit_kind):
    """Keep historical comment appends; optionally edit one visible prose word."""
    if edit_kind == 'comment':
        with path.open('a', encoding='utf-8') as stream:
            stream.write(f'% saved revision {index}\n')
        return None
    previous_word = 'synthetic' if index == 1 else f'syntheticrevision{index - 1}'
    before = f'Paragraph 0 describes a {previous_word} evaluation.'
    after = f'Paragraph 0 describes a syntheticrevision{index} evaluation.'
    raw = path.read_text(encoding='utf-8')
    if raw.count(before) != 1:
        raise RuntimeError('expected exactly one preceding visible paragraph revision')
    with path.open('w', encoding='utf-8') as stream:
        stream.write(raw.replace(before, after, 1))
    return {'before_text': before, 'after_text': after, 'changed_ordinary_word': True}


def visible_publication_proof(output, change, page_version, expected_paragraphs):
    """Proof from the actually published manifest, not source bytes alone."""
    content = read_json(output / 'annotation-content.json')
    if not content or content.get('version_id') != page_version:
        return None
    units = [unit for unit in content.get('units', []) if unit.get('kind') == 'text']
    matching = [unit for unit in units if change['after_text'] in unit.get('text', '')]
    if (len(units) != expected_paragraphs or len(matching) != 1
            or any(change['before_text'] in unit.get('text', '') for unit in units)):
        return None
    unit = matching[0]
    return {'verified_visible_text_change': True, 'manifest_version_id': content['version_id'],
            'published_text_units': len(units), 'unit_id': unit['id'], 'node_id': unit['node_id'],
            'unit_order': unit['order'], 'unit_text_sha256': digest(unit['text'].encode()), **change}


class Observer:
    def __init__(self, url, output, interval, timeout):
        self.url, self.output = url.rstrip('/'), output
        self.interval, self.timeout = interval, timeout
        self.opener = build_opener(ProxyHandler({}))
        self.token = self.paper_id = self.page_version = self.content_id = None

    def request(self, suffix):
        headers = {}
        if self.token and suffix != 'status':
            headers = {'X-LaTeX-Review-Token': self.token,
                       'X-LaTeX-Review-Paper-ID': self.paper_id,
                       'X-LaTeX-Review-Page-Version': self.page_version,
                       'X-LaTeX-Review-Checks-Content-ID': self.content_id}
        # Status is the documented unauthenticated token bootstrap. Never persist
        # its credential in the benchmark output.
        with self.opener.open(Request(self.url + '/api/checks/' + suffix, headers=headers), timeout=10) as response:
            return json.load(response)

    def settle(self, started, expected_file_hash, previous_input=None, previous_run=None,
               visible_change=None, expected_paragraphs=None):
        metrics = {'status_polls': 0, 'status_rtt_seconds': [], 'old_input_polls': 0,
                   'no_new_run_polls': 0, 'states_observed': [], 'observation_errors': [],
                   'load_at_start': host_sample()}
        deadline = time.monotonic() + self.timeout
        last = None
        while time.monotonic() < deadline:
            # The native watcher publishes an atomic directory; a transient gap
            # during directory rename is observed/retried, not treated as success.
            metadata = read_json(self.output / 'checks-page.json')
            live = read_json(self.output / 'live-status.json')
            now = time.perf_counter() - started
            if metadata and metadata.get('files', {}).get('part0.tex') == expected_file_hash:
                metrics.setdefault('first_matching_preview_publication_seconds', now)
                metrics['preview_content_id'] = metadata['content_id']
                if live:
                    metrics['live_revision'] = live.get('revision')
                    metrics['watcher_message'] = live.get('message')
            try:
                before = time.perf_counter()
                value = self.request('status')
                elapsed = time.perf_counter() - started
                metrics['status_rtt_seconds'].append(time.perf_counter() - before)
                metrics['status_polls'] += 1
                self.token, self.paper_id = value.get('token'), value.get('paper_id')
                self.page_version = value.get('page_version')
                self.content_id = metadata.get('content_id') if metadata else None
                run = value.get('current_run') or {}
                last = {key: value.get(key) for key in ('state', 'stale', 'current_input_id', 'current_run', 'available')}
                state = run.get('state')
                if not metrics['states_observed'] or metrics['states_observed'][-1]['state'] != state:
                    metrics['states_observed'].append({'seconds': elapsed, 'state': state})
                if run.get('run_id') and run['run_id'] != previous_run:
                    metrics.setdefault('first_new_run_observed_seconds', elapsed)
                    if state == 'running':
                        metrics.setdefault('first_running_observed_seconds', elapsed)
                else:
                    metrics['no_new_run_polls'] += 1
                if not value.get('current_input_id') or value['current_input_id'] == previous_input:
                    metrics['old_input_polls'] += 1
                if (state in ('completed', 'partial') and run.get('run_id') != previous_run
                        and value.get('current_input_id') and value['current_input_id'] != previous_input
                        and not value.get('stale')):
                    metrics.setdefault('first_completed_status_seconds', elapsed)
                    report = self.request('export')
                    if (metadata and report['input']['files'].get('part0.tex') == expected_file_hash
                            and report['input']['content_id'] == metadata['content_id']
                            and report['input_id'] == value['current_input_id']
                            and report['input_id'] == run.get('input_id')):
                        if visible_change is not None:
                            began = time.perf_counter()
                            proof = visible_publication_proof(self.output, visible_change,
                                                             self.page_version, expected_paragraphs)
                            latest = read_json(self.output / 'checks-page.json')
                            if (proof is None or latest is None
                                    or latest.get('page_version') != self.page_version
                                    or latest.get('content_id') != report['input']['content_id']):
                                raise RuntimeError('published visible edit proof is not current')
                            metrics['visible_change_proof'] = proof
                            metrics['visible_change_verification_seconds'] = time.perf_counter() - began
                        metrics.update({'save_to_verified_current_seconds': time.perf_counter() - started,
                            'input_id': report['input_id'], 'content_id': report['input']['content_id'],
                            'run_id': run['run_id'], 'status': report['status'], 'requested_scope': report['requested_scope'],
                            'evaluation_mode': report['evaluation_mode'], 'fallback_reason': report.get('fallback_reason'),
                            'expected_saved_file_sha256': expected_file_hash, 'authoritative_match': True,
                            'final_status': last, 'load_at_end': host_sample()})
                        return metrics
            except Exception as error:
                metrics['observation_errors'].append({'seconds': time.perf_counter() - started,
                                                      'type': type(error).__name__, 'message': str(error)})
            time.sleep(self.interval)
        metrics.update({'settled': False, 'final_status': last,
                        'bounded_wait_seconds': self.timeout, 'load_at_end': host_sample()})
        raise SettleTimeout(metrics)


class SettleTimeout(RuntimeError):
    def __init__(self, metrics):
        self.metrics = metrics
        super().__init__('saved notification did not produce a verified current report within bound')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=40)
    parser.add_argument('--paragraphs', type=int, default=5000)
    parser.add_argument('--saves', type=int, default=10)
    parser.add_argument('--edit-kind', choices=('comment', 'prose'), default='comment',
                        help='Historical invisible comment append, or one visible paragraph word revision.')
    parser.add_argument('--poll-seconds', type=float, default=.1)
    parser.add_argument('--settle-timeout', type=float, default=90)
    parser.add_argument('--concurrent-workload', default='Not independently established; inspect recorded host/cgroup samples.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not (2 <= args.files <= 40 and 1 <= args.paragraphs <= 5000 and args.saves >= 1
            and args.poll_seconds > 0 and args.settle_timeout > 0):
        parser.error('invalid bounded fixture or measurement parameters')
    args.output = args.output.resolve()
    result = {'schema_version': '1.0', 'complete': False, 'platform': platform.platform(),
              'python': platform.python_version(), 'source_boundary_begin': code_boundary(),
              'environment_begin': host_sample(), 'concurrent_workload_declared': args.concurrent_workload,
              'source_files': args.files, 'paragraphs': args.paragraphs, 'requested_saves': args.saves,
              'edit_kind': args.edit_kind,
              'edit_workload': ('Append an invisible TeX comment; historical recipe, not evidence of visible prose editing performance.'
                                if args.edit_kind == 'comment' else
                                'Change one ordinary word in paragraph 0 per save; fixed paragraph/file counts; published visible text proof included.'),
              'notifier_debounce_ms': 800, 'observer_poll_sleep_seconds': args.poll_seconds,
              'per_settle_timeout_seconds': args.settle_timeout, 'samples': [],
              'measurement': 'Real private entry ensure -> live worker native watcher/rebuild -> asynchronous saved notifier -> coordinator -> worker HTTP status/export.',
              'boundary_notes': ['Timer starts immediately before source write; write close included; no fsync durability claim.',
                  'Elapsed verified-current includes filesystem event detection, full preview rebuild/publication, default 800ms debounce, HTTP notify, coordinator queue and complete check, status observation and export verification.',
                  'Publication/queue/running timestamps are first-observed upper bounds, not isolated internal stage durations. Short queue/running states can be missed.',
                  'Polling sleeps plus recorded HTTP RTT introduce detection delay and observer load. No browser refresh or browser paint is measured.',
                  'Cold ensure/start and baseline settle are separate from sequential saved-edit samples.',
                  'Spec saved-hot p95 target is <=2s excluding TeX compilation and external network/model calls. This measurement includes rendering, debounce and local loopback transport; no external network/model calls or TeX compilation occur.',
                  'Source boundary hashes identify observed workspace, not a claim of exact final revision.']}
    def checkpoint():
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    checkpoint()
    failed = False
    with tempfile.TemporaryDirectory(prefix='writing-check-saved-benchmark-') as folder:
        private = canonical_private_root(folder)
        root, output = private / 'paper', private / 'report'
        root.mkdir()
        os.environ.update({'LATEX_REVIEW_STATE_HOME': str(private / 'entry-state'),
                           'XDG_CACHE_HOME': str(private / 'cache'), 'XDG_STATE_HOME': str(private / 'xdg-state')})
        # Import only after isolation. Child workers inherit this exact namespace.
        from latex_review import local_client
        from latex_review.live import prepare_request
        result['source_bytes_initial'] = generate(root, args.files, args.paragraphs)
        config = private / 'benchmark.toml'
        config.write_text('[compile]\nenabled=false\n')  # checks.on_save/default debounce deliberately omitted
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        entry = None
        startup_complete = False
        startup_returncode = None
        worker_pid = None
        with (private / 'entry-subprocess.log').open('wb') as log:
            try:
                launched = time.perf_counter()
                entry = subprocess.Popen([sys.executable, '-m', 'latex_review.local_service', 'run', '--port', str(port)],
                                         stdout=log, stderr=log, env=local_client.child_environment(), cwd='/')
                deadline = time.monotonic() + 20
                while True:
                    try:
                        endpoint = local_client.endpoint()
                        if endpoint['pid'] != entry.pid:
                            raise RuntimeError('isolated entry ownership mismatch')
                        break
                    except local_client.LocalError:
                        if entry.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('private entry startup failed')
                        time.sleep(.05)
                result['entry_start_seconds'] = time.perf_counter() - launched
                recipe = prepare_request(root / 'main.tex', root=root, output=output, config=config,
                                         cache=private / 'translation-cache')
                ensure_started = time.perf_counter()
                operation = local_client.ensure(recipe, timeout=180)
                result['ensure_to_worker_ready_seconds'] = time.perf_counter() - ensure_started
                # Fetch the worker's direct port through authenticated entry metadata.
                papers = local_client.request('/api/v1/papers', authenticated=True)['papers']
                paper = next(p for p in papers if p['paper_id'] == recipe['paper_id'])
                slot = paper['slots']['live']
                worker_pid = slot['pid']
                startup_complete = True
                observer = Observer(f"http://127.0.0.1:{slot['port']}", output, args.poll_seconds, args.settle_timeout)
                baseline_started = time.perf_counter()
                baseline = observer.settle(baseline_started, digest((root / 'part0.tex').read_bytes()))
                result['baseline_after_worker_ready'] = baseline
                checkpoint()
                previous = baseline
                for index in range(1, args.saves + 1):
                    started = time.perf_counter()
                    visible_change = write_saved_revision(root / 'part0.tex', index, args.edit_kind)
                    write_done = time.perf_counter()
                    try:
                        sample = observer.settle(started, digest((root / 'part0.tex').read_bytes()),
                                                 previous['input_id'], previous['run_id'],
                                                 visible_change=visible_change, expected_paragraphs=args.paragraphs)
                    except SettleTimeout as error:
                        result['failed_save'] = {'save': index, 'metrics': error.metrics}
                        raise
                    sample.update({'save': index, 'write_seconds': write_done - started})
                    publication = sample.get('first_matching_preview_publication_seconds')
                    new_run = sample.get('first_new_run_observed_seconds')
                    sample['observed_intervals_not_internal_stage_durations'] = {
                        'save_to_preview_publication_seconds': publication,
                        'publication_to_first_new_run_seconds': new_run - publication if publication is not None and new_run is not None else None,
                        'first_new_run_to_completed_status_seconds': sample['first_completed_status_seconds'] - new_run if new_run is not None else None,
                        'completed_status_to_export_verified_seconds': sample['save_to_verified_current_seconds'] - sample['first_completed_status_seconds']}
                    result['samples'].append(sample)
                    checkpoint()
                    print(json.dumps({'save': index, 'seconds': sample['save_to_verified_current_seconds'],
                                      'scope': sample['requested_scope'], 'mode': sample['evaluation_mode']}), flush=True)
                    previous = sample
                durations = sorted(s['save_to_verified_current_seconds'] for s in result['samples'])
                result.update({'complete': True, 'saved_median_seconds': statistics.median(durations),
                               'saved_p95_nearest_rank_seconds': durations[math.ceil(.95 * len(durations)) - 1],
                               'saved_min_seconds': min(durations), 'saved_max_seconds': max(durations),
                               'saved_hot_p95_target_seconds': 2,
                               'saved_hot_p95_target_met': durations[math.ceil(.95 * len(durations)) - 1] <= 2})
            except Exception as error:
                failed = True
                if not startup_complete:
                    startup_returncode = entry.poll() if entry is not None else None
                result['error'] = {'type': type(error).__name__, 'message': str(error)}
                if isinstance(error, SettleTimeout) and 'failed_save' not in result:
                    result['baseline_failure'] = error.metrics
            finally:
                # Never consult another namespace or terminate any resident user service.
                # Shutdown owned entry (closes every owned worker), then wait for process exit.
                if entry is not None:
                    try:
                        if entry.poll() is None:
                            local_client.stop()
                        entry.wait(timeout=15)
                        worker_exited = None
                        if worker_pid:
                            try:
                                os.kill(worker_pid, 0)  # Existence probe only, never a termination signal.
                                worker_exited = False
                            except ProcessLookupError:
                                worker_exited = True
                        result['cleanup'] = {'owned_entry_exit_code': entry.returncode, 'owned_entry_exited': True,
                                             'owned_worker_exited': worker_exited,
                                             'isolated_endpoint_removed': not (private / 'entry-state' / 'endpoint.json').exists()}
                        if worker_exited is False:
                            failed = True
                    except Exception as error:
                        entry.terminate()
                        try:
                            entry.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            entry.kill()
                            entry.wait()
                        result['cleanup'] = {'forced_owned_entry_cleanup': True, 'message': str(error),
                                             'owned_entry_exit_code': entry.returncode}
                        failed = True
                if not startup_complete:
                    result['startup_diagnostic'] = {
                        **entry_startup_diagnostic(private),
                        'phase': 'entry' if 'entry_start_seconds' not in result else 'worker_ready',
                        'entry_return_code_at_failure': startup_returncode,
                        'entry_return_code_after_cleanup': entry.returncode if entry is not None else None}
                result['source_boundary_end'] = code_boundary()
                first, last = result['source_boundary_begin']['files'], result['source_boundary_end']['files']
                result['source_paths_changed_during_measurement'] = [p for p in sorted(set(first) | set(last)) if first.get(p) != last.get(p)]
                result['environment_end'] = host_sample()
                checkpoint()
    print(json.dumps({key: value for key, value in result.items()
                      if key in ('complete', 'error', 'saved_median_seconds', 'saved_p95_nearest_rank_seconds',
                                 'cleanup', 'source_paths_changed_during_measurement', 'startup_diagnostic')}, indent=2), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
