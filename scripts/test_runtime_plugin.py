"""Pytest plugin used by the explicit test-shard runner."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path


class TimingPlugin:
    def __init__(self):
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({
            'nodeid': report.nodeid,
            'phase': report.when,
            'outcome': report.outcome,
            'seconds': report.duration,
            'wasxfail': getattr(report, 'wasxfail', None),
            'detail': str(report.longrepr) if report.outcome != 'passed' else None,
        })

    def pytest_sessionfinish(self, session, exitstatus):
        output = os.environ.get('TEST_RUNTIME_COLLECTION_OUTPUT')
        if not output:
            return
        phases = defaultdict(float)
        modules = defaultdict(lambda: defaultdict(float))
        for report in self.reports:
            phases[report['phase']] += report['seconds']
            modules[report['nodeid'].split('::')[0]][report['phase']] += report['seconds']
        payload = {
            'exitstatus': int(exitstatus),
            'collected': self.collected,
            'reports': self.reports,
            'phases': dict(phases),
            'modules': dict(modules),
            'outcomes': dict(Counter(
                report['outcome'] for report in self.reports
                if report['phase'] == 'call' or report['outcome'] != 'passed')),
        }
        Path(output).write_text(json.dumps(payload, indent=2) + '\n')


def pytest_configure(config):
    plugin = TimingPlugin()
    config.pluginmanager.register(plugin, 'test-runtime-timing')
