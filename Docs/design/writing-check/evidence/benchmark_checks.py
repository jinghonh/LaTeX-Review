#!/usr/bin/env python3
"""Reproducible synthetic NFR check; no private paper or model/network required."""
import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from latex_review.checks.document import build_check_document
from latex_review.checks.engine import evaluate_current
from latex_review.checks.registry import empty_registry
from latex_review.checks.snapshot import capture_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--paragraphs', type=int, default=5000)
    parser.add_argument('--files', type=int, default=40)
    parser.add_argument('--hot-runs', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.files <= 40 or not 1 <= args.paragraphs <= 5000 or args.hot_runs < 1:
        parser.error('bounded NFR fixture requires 2..40 files, 1..5000 paragraphs, positive repeats')
    with tempfile.TemporaryDirectory(prefix='writing-check-benchmark-') as folder:
        root = Path(folder)
        bodies = [[] for _ in range(args.files - 1)]
        for index in range(args.paragraphs):
            # Distinct ordinary prose, no deliberate unsupported commands, no
            # plagiarism/private fixture. Around 1.7MiB at the reference size.
            text = (f'Paragraph {index} describes a synthetic evaluation. '
                    'The experiment records stable input evidence and separates a rule finding from an author decision. '
                    'All measurements use a local generated document, with no external model calls. '
                    'A reproducible benchmark does not establish scientific correctness.\n\n')
            bodies[index % len(bodies)].append(text)
        for index, body in enumerate(bodies):
            (root / f'part{index}.tex').write_bytes(''.join(body).encode())
        (root / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}\n' + ''.join(
            f'\\input{{part{index}}}\n' for index in range(len(bodies))) + '\\end{document}\n', encoding='utf-8')
        size = sum(path.stat().st_size for path in root.iterdir())
        if size > 2 * 1024 * 1024:
            raise ValueError('benchmark exceeded 2MiB limit')
        timings, stages, counts, previous = [], [], [], None
        for run in range(args.hot_runs + 1):
            if run:
                # A real saved edit invalidates the snapshot. Identical-file
                # memoization cannot be mistaken for save-to-result latency.
                with (root / 'part0.tex').open('a', encoding='utf-8') as stream:
                    stream.write(f'% saved revision {run}\n')
            started = time.perf_counter()
            snapshot = capture_snapshot('main.tex', root=root)
            captured = time.perf_counter()
            document = build_check_document(snapshot)
            report = evaluate_current(document, empty_registry(), previous=previous, scope='changed' if run else 'all')
            ended = time.perf_counter()
            timings.append(ended - started)
            stages.append({'capture_parse_seconds': captured-started, 'adapt_rules_seconds': ended-captured})
            counts.append(len(document.units))
            previous = report
            progress = {'complete': False, 'paragraphs': args.paragraphs, 'source_files': args.files,
                        'source_bytes_initial': size, 'timings_so_far': timings, 'stages': stages,
                        'units_each_run': counts}
            args.output.write_text(json.dumps(progress, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({'run': run, **stages[-1], 'seconds': timings[-1], 'units': counts[-1]}), flush=True)
        hot = sorted(timings[1:])
        # Nearest-rank empirical p95; no interpolation can hide a slow sample.
        p95 = hot[max(0, __import__('math').ceil(.95 * len(hot)) - 1)]
        result = {'schema_version': '1.0', 'platform': platform.platform(), 'python': platform.python_version(),
                  'logical_cpus': __import__('os').cpu_count(), 'source_bytes_initial': size,
                  'source_files': args.files, 'paragraphs': args.paragraphs, 'units_each_run': counts,
                  'cold_seconds': timings[0], 'hot_seconds': timings[1:], 'hot_median_seconds': statistics.median(hot),
                  'hot_p95_seconds': p95, 'stages': stages, 'cold_target_met': timings[0] <= 10,
                  'hot_target_met': p95 <= 2, 'cold_target_seconds': 10, 'hot_p95_target_seconds': 2,
                  'evaluation_mode': report['evaluation_mode'], 'status': report['status'],
                  'note': 'Single Linux synthetic reference run; excludes compilation/network. Not macOS or real-paper acceptance.'}
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
