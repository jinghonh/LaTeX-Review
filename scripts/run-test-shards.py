#!/usr/bin/env python3
"""Run the full test inventory with one serial lane and one safe pure lane.

The default is ``auto``: one process on hosts with fewer than four CPUs and,
only on larger hosts, a serial lane plus a deterministic private-files lane.
The explicit collection and Counter check make a shard run unable to silently
lose or duplicate a parametrized node.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time

# Conservative allowlist: files use private tmp_path state and do not launch a
# browser, native watcher, resident service, fixed external artifact, or a
# real-time observation window. New files stay in the serial lane by default.
PURE_FILES = frozenset({
    'test_advanced_diff.py', 'test_annotation_batch.py', 'test_annotation_batch_cli.py',
    'test_annotation_cli.py', 'test_annotation_delivery.py', 'test_annotation_efficiency.py',
    'test_annotation_jobs.py', 'test_annotation_sources.py', 'test_annotation_suggestions.py',
    'test_check_acceptance_edges.py', 'test_check_cli.py', 'test_check_compiled_projection.py',
    'test_check_configuration.py', 'test_check_diagnostics.py', 'test_check_document_units.py',
    'test_check_knowledge_rules.py', 'test_check_rules.py', 'test_check_snapshot.py',
    'test_check_snapshot_identity.py', 'test_check_status_identity.py', 'test_check_store.py',
    'test_check_workspace.py', 'test_comparison.py', 'test_contract.py', 'test_delivery.py',
    'test_incremental_upload.py', 'test_issue40.py', 'test_issue41.py', 'test_issue43.py',
    'test_preview_literal.py', 'test_preview_numbering.py', 'test_preview_parser.py',
    'test_preview_whitespace.py', 'test_project_profile.py', 'test_report_directory.py',
    'test_report_discovery.py', 'test_report_rebuild.py', 'test_report_storage.py',
    'test_rule_mask_scanning.py', 'test_saved_benchmark_edits.py',
    'test_saved_check_notifier.py', 'test_sentence_memo.py', 'test_skill_links.py',
    'test_source_map_binary.py', 'test_structure_preview.py', 'test_test_runtime_tools.py',
    'test_text_scanner_fastpaths.py', 'test_translation_import_paths.py',
    'test_translation_manifest.py', 'test_translation_plain_fastpath.py', 'test_upload.py',
    'test_version_storage.py', 'test_web_login.py', 'test_writing_fixture_baseline.py',
})


def command_smoke():
    return (
        ('latex-review', ['--help']), ('latex-review', ['--version']),
        ('latex-review-check', ['--help']), ('latex-review-check', ['query', '--help']),
        ('latex-review-check', ['semantic', 'export', '--help']),
    )


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def run_smoke():
    results = []
    for name, options in command_smoke():
        command = [str(Path(sys.executable).with_name(name)), *options]
        started = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True)
        results.append({
            'command': command, 'exitstatus': result.returncode,
            'seconds': time.perf_counter() - started,
            'stdout': result.stdout, 'stderr': result.stderr,
        })
    return results


def collect(repo, files, output, env):
    collection = output / 'collection.json'
    collect_env = dict(env, TEST_RUNTIME_COLLECTION_OUTPUT=str(collection))
    command = [sys.executable, '-m', 'pytest', '--collect-only', '-q',
               '-p', 'scripts.test_runtime_plugin', *files]
    result = subprocess.run(command, cwd=repo, env=collect_env,
                            capture_output=True, text=True)
    if result.returncode or not collection.is_file():
        raise RuntimeError(f'collection failed ({result.returncode}):\n{result.stdout}\n{result.stderr}')
    return json.loads(collection.read_text())


def merge(shards, inventory, started, smoke):
    collected = []
    reports = []
    phases = defaultdict(float)
    modules = defaultdict(lambda: defaultdict(float))
    outcomes = Counter()
    shard_results = {}
    exitstatus = 0
    for name, evidence in shards.items():
        result = json.loads((evidence / 'timings.json').read_text())
        shard_results[name] = {
            'sha': result.get('sha'), 'exitstatus': result.get('exitstatus'),
            'collected': len(result.get('collected', [])),
            'pytest_wall_seconds': result.get('pytest_wall_seconds'),
            'wall_seconds': result.get('wall_seconds'),
            'process_audit': result.get('process_audit'),
        }
        collected.extend(result.get('collected', []))
        reports.extend(result.get('reports', []))
        exitstatus = max(exitstatus, int(result.get('exitstatus', 1)))
        for phase, seconds in result.get('phases', {}).items():
            phases[phase] += seconds
        for module, values in result.get('modules', {}).items():
            for phase, seconds in values.items():
                modules[module][phase] += seconds
        outcomes.update(result.get('outcomes', {}))
    expected = Counter(inventory['collected'])
    actual = Counter(collected)
    mismatch = {
        'missing': sorted((expected - actual).elements()),
        'unexpected': sorted((actual - expected).elements()),
    }
    if mismatch['missing'] or mismatch['unexpected']:
        exitstatus = max(exitstatus, 2)
    audits = [value['process_audit'] for value in shard_results.values()]
    audit_errors = [error for audit in audits for error in (audit or {}).get('audit_errors', [])]
    survivors = [item for audit in audits for item in (audit or {}).get('alive_after_pytest', [])]
    if audit_errors or survivors or any(not (audit or {}).get('complete', False) for audit in audits):
        exitstatus = max(exitstatus, 3)
    template = json.loads((next(iter(shards.values())) / 'timings.json').read_text())
    template.update({
        'command': shlex_join([sys.executable, *sys.argv]),
        'pytest_args': ['--explicit-shards', 'serial+pure'],
        'collected': collected, 'reports': reports,
        'phases': dict(phases), 'modules': dict(modules), 'outcomes': dict(outcomes),
        'pytest_wall_seconds': max((item['pytest_wall_seconds'] or 0) for item in shard_results.values()),
        'wall_seconds': time.perf_counter() - started,
        'pytest_exitstatus': exitstatus, 'exitstatus': exitstatus,
        'cli_smoke': smoke, 'shards': shard_results,
        'collection_inventory': {'count': len(inventory['collected']), 'mismatch': mismatch},
        'process_audit': {
            'complete': not audit_errors and not survivors,
            'audit_errors': audit_errors, 'alive_after_pytest': survivors,
            'shards': {name: value['process_audit'] for name, value in shard_results.items()},
        },
    })
    return template, exitstatus


def shlex_join(values):
    import shlex
    return shlex.join(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', choices=('auto', '1', '2'), default='auto')
    parser.add_argument('--runner', type=Path,
                        default=Path(__file__).with_name('measure-tests.py'))
    parser.add_argument('--test-file', action='append', dest='selected_files',
                        help='Restrict a pilot to named tests/test_*.py files')
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    output = args.output.resolve()
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    files = sorted(path.name for path in (repo / 'tests').glob('test_*.py'))
    if args.selected_files:
        files = [Path(value).name for value in args.selected_files]
        if any(name not in {path.name for path in (repo / 'tests').glob('test_*.py')} for name in files):
            raise SystemExit('every --test-file must name an existing tests/test_*.py file')
    if args.workers == 'auto':
        workers = 2 if (os.cpu_count() or 1) >= 4 else 1
    else:
        workers = int(args.workers)
    if workers == 1:
        output.rmdir()
        serial_env = dict(os.environ, LATEX_REVIEW_TEST_SHARDS='0')
        command = [sys.executable, str(args.runner.resolve()), '--output', str(output)]
        if args.selected_files:
            command.extend(['--', *(f'tests/{name}' for name in files)])
        result = subprocess.run(command, cwd=repo, env=serial_env)
        return result.returncode
    pure = [f'tests/{name}' for name in files if name in PURE_FILES]
    serial = [f'tests/{name}' for name in files if name not in PURE_FILES]
    if not pure or not serial:
        raise SystemExit('explicit shard manifests unexpectedly empty')
    env = os.environ.copy()
    env['LATEX_REVIEW_TEST_SHARDS'] = '0'
    env['LATEX_REVIEW_SKIP_CLI_SMOKE'] = '1'
    runner_root = Path(__file__).resolve().parent.parent
    inventory_env = dict(env, PYTHONPATH=os.pathsep.join(filter(
        None, [str(runner_root), str(repo), env.get('PYTHONPATH', '')])))
    inventory = collect(repo, [f'tests/{name}' for name in files], output, inventory_env)
    shards = {'serial': output / 'shards' / 'serial', 'pure': output / 'shards' / 'pure'}
    (output / 'shards').mkdir()
    processes = []
    for name, selected in (('serial', serial), ('pure', pure)):
        shard_env = dict(env)
        shard_env.update(
            XDG_CONFIG_HOME=str(shards[name] / 'config'),
            LATEX_REVIEW_STATE_HOME=str(shards[name] / 'state'),
            LATEX_REVIEW_LOCAL_PORT=str(free_port()),
        )
        command = [sys.executable, str(args.runner.resolve()), '--output', str(shards[name]), '--', *selected]
        processes.append((name, subprocess.Popen(command, cwd=repo, env=shard_env)))
    exitstatus = 0
    for name, process in processes:
        exitstatus = max(exitstatus, process.wait())
    smoke = [] if os.environ.get('LATEX_REVIEW_SKIP_CLI_SMOKE') == '1' else run_smoke()
    if any(item['exitstatus'] for item in smoke):
        exitstatus = max(exitstatus, 4)
    merged, merge_status = merge(shards, inventory, started, smoke)
    exitstatus = max(exitstatus, merge_status)
    (output / 'timings.json').write_text(json.dumps(merged, indent=2) + '\n')
    (output / 'collection.json').write_text(json.dumps(inventory, indent=2) + '\n')
    return exitstatus


if __name__ == '__main__':
    sys.exit(main())
