#!/usr/bin/env python3
"""Run pytest once, preserving its result and complete timing/collection evidence.

Can be invoked by absolute path from an older checkout to measure an unchanged
baseline with exactly the same instrumentation. No performance benchmarks run.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict
from contextlib import redirect_stderr, redirect_stdout
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
from threading import Event, Thread
import time
import traceback

import psutil


class OwnedProcesses:
    """Observe only this runner's descendants; never signal another process."""
    def __init__(self):
        self.parent = psutil.Process()
        self.observed = {}
        self.errors = []
        self.stop = Event()
        self.thread = Thread(target=self.observe, daemon=True)

    def sample(self):
        try:
            children = self.parent.children(recursive=True)
        except psutil.Error as error:
            self.errors.append(str(error))
            return
        for process in children:
            try:
                self.observed[(process.pid, process.create_time())] = process.name()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                self.errors.append(str(error))

    def observe(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(.2)

    def finish(self):
        self.sample()
        self.stop.set()
        self.thread.join()
        survivors, interpreter_helpers = [], []
        for (pid, created), name in self.observed.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                command = process.cmdline()
                entry = {'pid': pid, 'created': created, 'name': name, 'command': command}
                # multiprocessing spawn leaves this CPython helper alive until
                # the parent interpreter exits, even after its pools join.
                if len(command) == 3 and command[1] == '-c' and re.fullmatch(
                        r'from multiprocessing\.resource_tracker import main;main\(\d+\)', command[2]):
                    interpreter_helpers.append(entry)
                else:
                    survivors.append(entry)
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                self.errors.append(str(error))
        return {'observed_count': len(self.observed), 'alive_after_pytest': survivors,
                'audit_errors': self.errors, 'complete': not self.errors,
                'interpreter_helpers_until_exit': interpreter_helpers,
                'sampling_seconds': .2,
                'limitation': 'Short-lived processes that detach between samples may not be observed.'}


class Tee:
    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        self.log.write(text)
        self.log.flush()
        return self.stream.write(text)

    def flush(self):
        self.log.flush()
        self.stream.flush()

    def isatty(self):
        return False


class Timings:
    def __init__(self):
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({
            'nodeid': report.nodeid, 'phase': report.when,
            'outcome': report.outcome, 'seconds': report.duration,
            'wasxfail': getattr(report, 'wasxfail', None),
            'detail': str(report.longrepr) if report.outcome != 'passed' else None,
        })

    def summary(self):
        phases = defaultdict(float)
        modules = defaultdict(lambda: defaultdict(float))
        for report in self.reports:
            phases[report['phase']] += report['seconds']
            modules[report['nodeid'].split('::')[0]][report['phase']] += report['seconds']
        return {
            'phases': dict(phases), 'modules': dict(modules),
            'outcomes': dict(Counter(
                report['outcome'] for report in self.reports
                if report['phase'] == 'call' or report['outcome'] != 'passed')),
            'collected': self.collected, 'reports': self.reports,
        }


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory (never overwrites an earlier run)')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER,
                        help='Arguments after -- go to pytest; no filtering by default')
    args = parser.parse_args()
    if os.environ.get('LATEX_REVIEW_TEST_SHARDS') == 'auto':
        command = [sys.executable, str(Path(__file__).with_name('run-test-shards.py')),
                   '--output', str(args.output.resolve()), '--workers', 'auto']
        result = subprocess.run(command, cwd=Path.cwd(), env=os.environ.copy())
        return result.returncode
    pytest_args = args.pytest_args
    if pytest_args[:1] == ['--']:
        pytest_args = pytest_args[1:]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Measure the checkout in cwd, including its subprocesses, even when this
    # runner lives in a different revision. Dependencies stay in the same venv.
    source = str(Path.cwd() / 'src')
    sys.path.insert(0, source)
    os.environ['PYTHONPATH'] = os.pathsep.join(filter(None, [source, os.environ.get('PYTHONPATH')]))
    import pytest
    timings = Timings()
    metadata = {
        'sha': git('rev-parse', 'HEAD'), 'working_tree': git('status', '--porcelain'),
        'platform': platform.platform(), 'machine': platform.machine(),
        'python': sys.version, 'executable': sys.executable,
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ('pytest', 'playwright', 'plasTeX', 'watchfiles', 'psutil')},
        'instrumentation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'tools': {name: shutil.which(name) for name in (
            'latexmk', 'pdflatex', 'pdftoppm', 'pdfinfo', 'bibtex', 'sandbox-exec', 'node', 'synctex')},
        'command': shlex.join([sys.executable, *sys.argv]),
        'pytest_args': pytest_args,
        'require_browser': os.environ.get('LATEX_REVIEW_REQUIRE_BROWSER'),
        'browser_executable': os.environ.get('LATEX_REVIEW_BROWSER'),
        'browser_manifest': json.loads(importlib.resources.files('playwright').joinpath(
            'driver/package/browsers.json').read_text()),
        'pytest_environment': {name: os.environ.get(name) for name in (
            'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD')},
        'browser_scope_override': os.environ.get('LATEX_REVIEW_BROWSER_SCOPE'),
    }
    custom_browser = os.environ.get('LATEX_REVIEW_BROWSER')
    metadata['custom_browser_sha256'] = None
    if custom_browser:
        with Path(custom_browser).open('rb') as executable:
            metadata['custom_browser_sha256'] = hashlib.file_digest(executable, 'sha256').hexdigest()
    metadata['load_before'] = os.getloadavg()
    metadata['cpus'] = os.cpu_count()
    owned = OwnedProcesses()
    started = time.perf_counter()
    owned.thread.start()
    try:
        with (output / 'console.log').open('w') as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                status = pytest.main([
                    '-q', '-ra', f'--junitxml={output / "junit.xml"}', *pytest_args,
                ], plugins=[timings])
                metadata['pytest_wall_seconds'] = time.perf_counter() - started
                metadata['pytest_exitstatus'] = int(status)
                metadata['cli_smoke'] = []
                if os.environ.get('LATEX_REVIEW_SKIP_CLI_SMOKE') != '1':
                    for name, options in (
                        ('latex-review', ['--help']), ('latex-review', ['--version']),
                        ('latex-review-check', ['--help']),
                        ('latex-review-check', ['query', '--help']),
                        ('latex-review-check', ['semantic', 'export', '--help']),
                    ):
                        command = [str(Path(sys.executable).with_name(name)), *options]
                        smoke_started = time.perf_counter()
                        result = subprocess.run(command, capture_output=True, text=True)
                        metadata['cli_smoke'].append({
                            'command': command, 'exitstatus': result.returncode,
                            'seconds': time.perf_counter() - smoke_started,
                            'stdout': result.stdout, 'stderr': result.stderr,
                        })
                        if result.returncode:
                            print(result.stdout, result.stderr, file=sys.stderr)
                            status = status or 1
    except BaseException as error:
        status = 130 if isinstance(error, KeyboardInterrupt) else 3
        metadata['runner_error'] = f'{type(error).__name__}: {error}'
        traceback.print_exc()
        with (output / 'console.log').open('a') as log:
            traceback.print_exc(file=log)
    finally:
        metadata['process_audit'] = owned.finish()
    metadata.update(timings.summary())
    metadata.update(wall_seconds=time.perf_counter() - started, load_after=os.getloadavg())
    if metadata['process_audit']['alive_after_pytest'] or not metadata['process_audit']['complete']:
        metadata['process_cleanup_failed'] = True
        status = status or 1
    metadata['exitstatus'] = int(status)
    (output / 'timings.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Evidence: {output}; wall: {metadata["wall_seconds"]:.3f}s; exit: {int(status)}')
    return int(status)


if __name__ == '__main__':
    sys.exit(main())
