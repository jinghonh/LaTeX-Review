#!/usr/bin/env python3
"""Compare complete measurements without discarding slow or failed samples."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys


def outcome(run, nodeid):
    reports = [report for report in run['reports'] if report['nodeid'] == nodeid]
    if any(report['outcome'] == 'failed' for report in reports):
        return 'failed'
    if any(report['outcome'] == 'skipped' for report in reports):
        return 'skipped'
    return 'passed' if any(report['phase'] == 'call' for report in reports) else 'missing'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args()
    before, after = [json.loads(path.read_text()) for path in (args.before, args.after)]
    old_ids, new_ids = Counter(before['collected']), Counter(after['collected'])
    missing = sorted((old_ids - new_ids).elements())
    regressions = [nodeid for nodeid in sorted(old_ids.keys() & new_ids.keys())
                   if outcome(before, nodeid) == 'passed' and outcome(after, nodeid) != 'passed']
    comparable_fields = (
        'platform', 'machine', 'python', 'dependencies', 'require_browser',
        'browser_executable', 'browser_manifest', 'pytest_environment',
        'pytest_args', 'cpus', 'instrumentation_sha256', 'tools', 'custom_browser_sha256',
    )
    mismatches = {field: {'before': before.get(field), 'after': after.get(field)}
                  for field in comparable_fields if field not in before or field not in after
                  or before[field] != after[field]}
    modules = []
    for name in before['modules'].keys() | after['modules'].keys():
        old = before['modules'].get(name, {})
        new = after['modules'].get(name, {})
        modules.append({'module': name, 'before': old, 'after': new,
                        'before_total': sum(old.values()), 'after_total': sum(new.values())})
    valid = not (mismatches or missing or regressions or before['exitstatus'] or after['exitstatus'])
    raw_delta = 100 * (1 - after['wall_seconds'] / before['wall_seconds'])
    summary = {
        'comparison_valid': valid, 'raw_wall_delta_percent': raw_delta,
        'before_sha': before['sha'], 'after_sha': after['sha'],
        'before_wall_seconds': before['wall_seconds'], 'after_wall_seconds': after['wall_seconds'],
        'reduction_percent': raw_delta if valid else None,
        'environment_mismatches': mismatches,
        'browser_scope_override': {'before': before.get('browser_scope_override'),
                                   'after': after.get('browser_scope_override')},
        'before_exitstatus': before['exitstatus'], 'after_exitstatus': after['exitstatus'],
        'before_outcomes': before['outcomes'], 'after_outcomes': after['outcomes'],
        'before_phases': before['phases'], 'after_phases': after['phases'],
        'added': sorted((new_ids - old_ids).elements()), 'missing': missing, 'regressions': regressions,
        'modules': sorted(modules, key=lambda item: item['before_total'], reverse=True),
    }
    print(json.dumps(summary, indent=2))
    return int(not valid)


if __name__ == '__main__':
    sys.exit(main())
