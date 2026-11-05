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
    args = parser.parse_args()
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
        with (root / 'part0.tex').open('a') as stream:
            stream.write('% saved revision 1\n')
        config = private / 'benchmark.toml'
        config.write_text('[compile]\nenabled=false\n')
        source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)),
                               configuration=_config(config, True))
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
            result['top_cumulative'] = sorted(records, key=lambda r: r['cumulative_seconds'], reverse=True)[:40]
            result['top_self'] = sorted(records, key=lambda r: r['self_seconds'], reverse=True)[:40]
            result['source_boundary_end'] = code_boundary()
            first, last = result['source_boundary_begin']['files'], result['source_boundary_end']['files']
            result['source_paths_changed_during_profile'] = [p for p in sorted(set(first) | set(last)) if first.get(p) != last.get(p)]
            result['environment_end'] = host_sample()
            args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key in {
        'profiled_build_and_publication_elapsed_seconds', 'self_user_cpu_seconds', 'self_system_cpu_seconds',
        'source_paths_changed_during_profile', 'top_cumulative'}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
