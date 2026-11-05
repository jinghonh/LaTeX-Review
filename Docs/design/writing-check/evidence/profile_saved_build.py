#!/usr/bin/env python3
"""Profile the exact full live.build + publication used by a saved rebuild.

Diagnostic only: cProfile adds overhead; this is NOT end-to-end save latency.
No services, HTTP observer, notifier, coordinator, model, or TeX compiler run.
"""
import argparse
import cProfile
import json
import os
from pathlib import Path
import pstats
import resource
import tempfile
import time

from benchmark_saved_checks import code_boundary, digest, generate, host_sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--manifest-observations', type=int, default=0,
                        help='Time full and byte-keyed manifest validation after profiling (no HTTP).')
    parser.add_argument('--warmup-builds', type=int, default=0,
                        help='Build the unedited fixture before the profiled saved edit (0..5).')
    args = parser.parse_args()
    if not 0 <= args.warmup_builds <= 5:
        parser.error('warmup-builds must be between zero and five')
    result = {'measurement': __doc__, 'source_boundary_begin': code_boundary(),
              'profiler_script_sha256': digest(Path(__file__).read_bytes()),
              'environment_begin': host_sample(), 'files': 40, 'paragraphs': 5000}
    with tempfile.TemporaryDirectory(prefix='writing-check-build-profile-') as folder:
        private = Path(folder)
        os.environ.update({'LATEX_REVIEW_STATE_HOME': str(private / 'entry-state'),
                           'XDG_CACHE_HOME': str(private / 'cache'), 'XDG_STATE_HOME': str(private / 'state')})
        from latex_review.cli import _config
        from latex_review.contract import ComparisonSource
        from latex_review.live import build
        from latex_review.report_directory import report_directory
        from latex_review.report_rebuild import rebuild_lock
        from latex_review.sources import ProjectSource
        root, output = private / 'paper', private / 'report'
        root.mkdir()
        result['source_bytes_initial'] = generate(root, 40, 5000)
        config = private / 'benchmark.toml'
        config.write_text('[compile]\nenabled=false\n')
        source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)),
                               configuration=_config(config, True))
        result['unprofiled_warmup_build_seconds'] = []
        for index in range(args.warmup_builds):
            began = time.perf_counter()
            with rebuild_lock(output), report_directory(output) as candidate:
                build(source, candidate.directory, revision=index + 1)
            result['unprofiled_warmup_build_seconds'].append(time.perf_counter() - began)
        with (root / 'part0.tex').open('a') as stream:
            stream.write('% saved revision 1\n')
        profiler = cProfile.Profile()
        before = resource.getrusage(resource.RUSAGE_SELF)
        started = time.perf_counter()
        profiler.enable()
        try:
            with rebuild_lock(output), report_directory(output) as candidate:
                build(source, candidate.directory, revision=2)
        finally:
            profiler.disable()
            result['profiled_build_and_publication_elapsed_seconds'] = time.perf_counter() - started
            after = resource.getrusage(resource.RUSAGE_SELF)
            result['self_user_cpu_seconds'] = after.ru_utime - before.ru_utime
            result['self_system_cpu_seconds'] = after.ru_stime - before.ru_stime
            profiler.dump_stats(str(args.profile))
            stats = pstats.Stats(profiler)
            records = []
            for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
                records.append({'file': filename, 'line': line, 'function': name,
                                'primitive_calls': primitive, 'calls': calls,
                                'self_seconds': own, 'cumulative_seconds': cumulative})
            result['memoization_profile_calls'] = {
                name: sum(record['calls'] for record in records if record['function'] == name)
                for name in ('_extract_html_units', '_html_units', '_split_sentences', 'split_sentences')}
            result['top_cumulative'] = sorted(records, key=lambda r: r['cumulative_seconds'], reverse=True)[:40]
            result['top_self'] = sorted(records, key=lambda r: r['self_seconds'], reverse=True)[:40]
            result['source_boundary_end'] = code_boundary()
            first, last = result['source_boundary_begin']['files'], result['source_boundary_end']['files']
            result['source_paths_changed_during_profile'] = [p for p in sorted(set(first) | set(last)) if first.get(p) != last.get(p)]
            result['environment_end'] = host_sample()
            args.output.write_text(json.dumps(result, indent=2) + '\n')
        if args.manifest_observations:
            from types import SimpleNamespace
            from latex_review.annotations import load_content, MANIFEST
            from latex_review.checks.http import _status_content_identity
            handler = SimpleNamespace(directory=output, server=SimpleNamespace())
            diagnostics = {'description': 'Actual generated manifest; local validation only, profiler disabled; not HTTP or saved latency.',
                           'manifest_bytes': (output / MANIFEST).stat().st_size,
                           'units': len(load_content(output)['units']), 'full_validation_seconds': [],
                           'first_byte_keyed_validation_seconds': [], 'same_bytes_identity_seconds': []}
            for label, operation in (
                    ('full_validation_seconds', lambda: load_content(output)),
                    ('first_byte_keyed_validation_seconds', lambda: _status_content_identity(
                        SimpleNamespace(directory=output, server=SimpleNamespace()))),
                    ('same_bytes_identity_seconds', lambda: _status_content_identity(handler))):
                if label == 'same_bytes_identity_seconds':
                    _status_content_identity(handler)
                for _ in range(args.manifest_observations):
                    began = time.perf_counter()
                    operation()
                    diagnostics[label].append(time.perf_counter() - began)
            result['manifest_validation_diagnostic'] = diagnostics
            args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key in {
        'profiled_build_and_publication_elapsed_seconds', 'self_user_cpu_seconds', 'self_system_cpu_seconds',
        'source_paths_changed_during_profile', 'top_cumulative'}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
