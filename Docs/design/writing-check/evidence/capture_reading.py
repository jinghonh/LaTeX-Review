"""Reproduce real live/diff reading captures without personal services or model calls.

.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase before
Use --phase after after implementation, with the identical fixture and browser settings.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
import io
import json
import os
import tarfile
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / 'tests/fixtures/writing_checks/paper'
VIEWPORT = {'width': 1440, 'height': 1000}


def run(command, env):
    start = time.perf_counter()
    result = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True)
    return {'command': command, 'exit_code': result.returncode,
            'wall_seconds': round(time.perf_counter() - start, 6),
            'stdout': result.stdout, 'stderr': result.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['before', 'after'], required=True)
    parser.add_argument('--validate-workspace', action='store_true', help='Also run real checks, inspect evidence and exercise keyboard/narrow layout (After only).')
    parser.add_argument('--validate-offline-panels', action='store_true', help='Also register synthetic knowledge and export/import fixed semantic candidates after baseline captures; no model invocation.')
    args = parser.parse_args()
    if args.validate_offline_panels and not args.validate_workspace:
        parser.error('--validate-offline-panels requires --validate-workspace')
    if args.validate_workspace and args.phase != 'after':
        parser.error('--validate-workspace requires --phase after')
    evidence = Path(__file__).resolve().parent / args.phase
    evidence.mkdir(parents=True, exist_ok=True)
    measurements = {
        'phase': args.phase, 'capture_complete': False,
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'platform': platform.platform(), 'python': sys.version,
        'logical_cpus': os.cpu_count(),
        'memory_bytes': os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES'),
        'fixture_file_count': sum(p.is_file() for p in FIXTURE.rglob('*')),
        'fixture_bytes': sum(p.stat().st_size for p in FIXTURE.rglob('*') if p.is_file()),
        'dependencies': {name: version(name) for name in ('latex-review', 'plasTeX', 'jsonschema', 'watchfiles', 'playwright', 'pytest')},
        'viewport': VIEWPORT, 'color_scheme': 'light', 'locale': 'en-US', 'timezone': 'UTC',
        'network_policy': 'Abort non-loopback requests; MathJax unavailable by design, no LLM calls',
        'fixture_sha256': {str(p.relative_to(FIXTURE)): sha256(p.read_bytes()).hexdigest()
                           for p in sorted(FIXTURE.rglob('*')) if p.is_file()},
        'tools': {name: shutil.which(name) for name in ('latexmk', 'pdflatex', 'xelatex', 'lualatex')},
    }
    # Every service command sees a unique private state root and a reserved loopback port.
    with tempfile.TemporaryDirectory(prefix='writing-check-capture-') as temporary:
        tmp = Path(temporary).resolve()
        baseline = 'fe82620af8bbbb028809d59ceeaaad7085427880'
        if args.phase == 'before':
            archive = subprocess.check_output(['git', 'archive', '--format=tar', baseline, 'src'], cwd=REPO)
            frozen = tmp / 'baseline-code'
            frozen.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                bundle.extractall(frozen, filter='data')
            source_path = frozen / 'src'
            measurements['application_revision'] = baseline
        else:
            source_path = REPO / 'src'
            production_status = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all', '--', 'src'], cwd=REPO, text=True)
            measurements['application_source_git_status_at_start'] = production_status
            measurements['application_revision'] = measurements['head'] if not production_status else 'working-tree'
        def source_hashes():
            return {str(path.relative_to(source_path)): sha256(path.read_bytes()).hexdigest()
                    for path in sorted(source_path.rglob('*'))
                    if path.is_file() and path.suffix in {'.py', '.js', '.css', '.html', '.json'}}
        measurements['application_source_sha256'] = source_hashes()
        sys.path.insert(0, str(source_path))
        browser_cache = os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'ms-playwright')
        env = {**os.environ, 'PLAYWRIGHT_BROWSERS_PATH': browser_cache,
               'PYTHONPATH': str(source_path), 'LATEX_REVIEW_STATE_HOME': str(tmp / 'state'),
               'XDG_CONFIG_HOME': str(tmp / 'config'), 'XDG_CACHE_HOME': str(tmp / 'cache'),
               'XDG_STATE_HOME': str(tmp / 'xdg-state')}
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            env['LATEX_REVIEW_LOCAL_PORT'] = str(probe.getsockname()[1])
        os.environ.update({key: value for key, value in env.items() if key in {
            'PLAYWRIGHT_BROWSERS_PATH', 'PYTHONPATH', 'LATEX_REVIEW_STATE_HOME', 'LATEX_REVIEW_LOCAL_PORT', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'}})
        current, old = tmp / 'paper', tmp / 'old'
        shutil.copytree(FIXTURE, current)
        shutil.copytree(FIXTURE, old)
        intro = old / 'sections/introduction.tex'
        text = intro.read_text(encoding='utf-8')
        text = text.replace('improves reproducibility', 'supports reproducibility')
        text = text.replace(' and the unregistered source \\cite{missing2026}', '')
        intro.write_text(text, encoding='utf-8')
        print(f'Capture {args.phase}: isolated root {tmp}; source {source_path}', flush=True)
        from latex_review import local_client
        from latex_review.contract import ComparisonSource
        from latex_review.identity import identity_hash, paper_identity
        from latex_review.live import prepare_request
        from latex_review.sources import ProjectSource
        from latex_review.structure import parse_project
        from latex_review.rules import check_rules
        source = ProjectSource('new', current, 'main.tex', ComparisonSource('directory', str(current)))
        # Expansion+parse timing is not a full writing-check measurement: baseline has no such feature.
        parse_runs = []
        for _ in range(5):
            start = time.perf_counter()
            parsed = parse_project(source.expand(), include_title=True)
            parse_runs.append(round(time.perf_counter() - start, 6))
        measurements['expand_parse_seconds'] = parse_runs
        measurements['expand_parse_median_seconds'] = statistics.median(parse_runs)
        measurements['parsed_node_count'] = len(parsed.nodes)
        measurements['parse_diagnostics'] = [asdict(d) for d in parsed.diagnostics]
        repeated = [asdict(d) for d in parsed.expanded.dependencies if d.file == 'shared/observation.tex']
        measurements['repeated_include_dependencies'] = repeated
        occurrences = [asdict(segment) for segment in parsed.expanded.source_map.segments
                       if segment.file == 'shared/observation.tex']
        assert len({segment['include_instance'] for segment in occurrences}) == 2
        measurements['repeated_include_source_segments'] = occurrences
        measurements['current_vs_self_rule_codes'] = dict(Counter(d.code for d in check_rules(parsed, parsed)))
        measurements['crlf_bytes'] = (current / 'shared/crlf.tex').read_bytes().count(b'\r\n')
        builds = []
        for _ in range(3):
            result = run([sys.executable, '-m', 'latex_review.cli', '--old-dir', str(old),
                          '--new-dir', str(current), '--entry', 'main.tex', '--output', str(tmp / 'diff'),
                          '--no-cache'], env)
            builds.append(result)
            if result['exit_code'] not in (0, 2):
                raise RuntimeError(result)
        measurements['diff_builds'] = builds
        measurements['diff_build_median_seconds'] = statistics.median(r['wall_seconds'] for r in builds)
        diff_data = json.loads((tmp / 'diff/diff.json').read_text())
        measurements['diff_summary'] = diff_data.get('summary')
        measurements['diff_diagnostics'] = json.loads((tmp / 'diff/diagnostics.json').read_text())
        try:
            start = time.perf_counter()
            live = local_client.ensure(prepare_request(current / 'main.tex', root=current, output=tmp / 'live'))
            measurements['live_service_ready_seconds'] = round(time.perf_counter() - start, 6)
            identity = paper_identity(current, current / 'main.tex')
            diff = local_client.ensure({**identity, 'kind': 'diff', 'directory': str(tmp / 'diff'), 'port': 0,
                                       'config': str(tmp / 'missing.toml'), 'config_explicit': False,
                                       'cache': str(tmp / 'translation-cache'),
                                       'configuration_fingerprint': identity_hash({})})
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'), headless=True)
                measurements['browser_version'] = browser.version
                measurements['pages'] = {}
                try:
                    for kind, service in [('live', live), ('diff', diff)]:
                        print(f'Capture {args.phase}: opening {kind} {service["url"]}', flush=True)
                        page = browser.new_page(viewport=VIEWPORT, color_scheme='light', locale='en-US', timezone_id='UTC', device_scale_factor=1)
                        errors, blocked = [], []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        def routing(route):
                            if route.request.url.startswith(('http://127.0.0.1:', 'http://localhost:')):
                                route.continue_()
                            else:
                                blocked.append(route.request.url)
                                route.abort()
                        page.route('**/*', routing)
                        start = time.perf_counter()
                        page.goto(service['url'])
                        frame = page.frame_locator('iframe')
                        frame.locator('.preview-side:visible').first.wait_for()
                        page.wait_for_load_state('networkidle')
                        frame.locator('.preview-side:visible').first.get_by_text('Introduction', exact=False).first.wait_for()
                        page.screenshot(path=str(evidence / f'{kind}-reading.png'), full_page=True)
                        measurements['pages'][kind] = {
                            'url': service['url'], 'ready_and_screenshot_seconds': round(time.perf_counter() - start, 6),
                            'page_errors': errors, 'blocked_external_requests': blocked,
                            'title': page.title(), 'preview_sides': frame.locator('.preview-side').count(),
                            'visible_text': frame.locator('body').inner_text(),
                        }
                        if args.validate_workspace:
                            from playwright.sync_api import expect
                            checks = frame.get_by_role('tab', name='问题检查', exact=True)
                            expect(checks).to_be_visible()
                            checks.click()
                            launch = frame.get_by_role('button', name='检查当前论文', exact=True)
                            try:
                                expect(launch).to_be_enabled(timeout=15000)
                            except Exception:
                                page.screenshot(path=str(evidence / f'{kind}-capability-failure.png'), full_page=True)
                                failure = {'application_source_changed_during_capture': source_hashes() != measurements['application_source_sha256'],
                                           'page_errors': errors, 'visible_text': frame.locator('body').inner_text(),
                                           'checks_status': frame.locator('body').evaluate("async () => { const d = await (await fetch('api/checks/status')).json(); delete d.token; return d; }")}
                                try:
                                    failure['direct_entry_status'] = local_client.checks_request(identity['paper_id'], 'status')
                                except Exception as diagnostic:
                                    failure['direct_entry_error'] = {'message': str(diagnostic), 'status': getattr(diagnostic, 'status', None),
                                                                     'details': getattr(diagnostic, 'details', None)}
                                (evidence / f'{kind}-capability-failure.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
                                print(json.dumps(failure, ensure_ascii=False), flush=True)
                                raise
                            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/api/checks/runs'), timeout=30000) as started:
                                launch.click()
                            task = started.value.json()
                            frozen_checks = frame.locator('#checks-data').evaluate('(node) => JSON.parse(node.textContent)')
                            binding = {'request_headers': {key: value for key, value in started.value.request.headers.items()
                                                           if not any(secret in key.lower() for secret in ('token', 'cookie', 'authorization'))},
                                       'request_body': started.value.request.post_data_json,
                                       'frozen_top_level_keys': sorted(frozen_checks),
                                       'frozen_content_id': (frozen_checks.get('input') or {}).get('content_id'),
                                       'page_snapshot': json.loads((tmp / kind / 'checks-page.json').read_text())}
                            measurements['pages'][kind]['post_binding'] = binding
                            if 'run_id' not in task:
                                failure = {'run_response': task, 'page_errors': errors, **binding,
                                           'direct_entry_status': local_client.checks_request(identity['paper_id'], 'status')}
                                (evidence / f'{kind}-run-failure.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
                                raise AssertionError(failure)
                            page.wait_for_function("""runId => {
                                const workspace = document.querySelector('iframe').contentWindow.reviewWorkspace;
                                const run = workspace?.capability.current_run;
                                return run?.run_id === runId && ['complete', 'completed', 'partial', 'succeeded'].includes(run.state || run.status)
                                    && workspace.report?.input_id === workspace.capability.current_input_id;
                            }""", arg=task['run_id'], timeout=60000)
                            expect(launch).to_be_enabled(timeout=30000)
                            try:
                                expect(frame.locator('.workspace-findings li').first).to_be_visible(timeout=30000)
                            except Exception:
                                page.screenshot(path=str(evidence / f'{kind}-validation-failure.png'), full_page=True)
                                failure = {'application_source_changed_during_capture': source_hashes() != measurements['application_source_sha256'],
                                           'page_errors': errors, 'visible_text': frame.locator('body').inner_text(),
                                           'checks_status': frame.locator('body').evaluate("async () => (await fetch('api/checks/status')).json()")}
                                failure['checks_status'].pop('token', None)
                                (evidence / f'{kind}-validation-failure.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
                                print(json.dumps(failure, ensure_ascii=False), flush=True)
                                raise
                            findings_count = frame.locator('.workspace-findings li').count()
                            page.screenshot(path=str(evidence / f'{kind}-checks.png'), full_page=True)
                            trigger = frame.locator('.workspace-findings li button').first
                            trigger.click()
                            drawer = frame.get_by_role('dialog', name='原文证据', exact=True)
                            expect(drawer).to_be_visible()
                            expect(drawer.locator('pre').first).not_to_be_empty()
                            evidence_text = drawer.inner_text()
                            page.screenshot(path=str(evidence / f'{kind}-evidence.png'), full_page=True)
                            drawer.press('Escape')
                            expect(drawer).to_be_hidden()
                            expect(trigger).to_be_focused()
                            checks.click()
                            checks.press('End')
                            expect(frame.get_by_role('tab', name='规则与覆盖', exact=True)).to_be_focused()
                            expect(frame.locator('#workspace-coverage')).to_be_visible()
                            frame.get_by_role('tab', name='阅读与变更', exact=True).click()
                            expect(frame.locator('.preview-side:visible').first).to_be_visible()
                            page.set_viewport_size({'width': 390, 'height': 844})
                            checks.click()
                            expect(frame.locator('#workspace-checks')).to_be_visible()
                            page.screenshot(path=str(evidence / f'{kind}-narrow-checks.png'), full_page=True)
                            current_report = frame.locator('body').evaluate('() => window.reviewWorkspace.report')
                            current_status = frame.locator('body').evaluate('() => { const d = {...window.reviewWorkspace.capability}; delete d.token; return d; }')
                            finding_codes = sorted(finding['rule_id'] for finding in current_report['findings'])
                            assert finding_codes == ['bibliography_key_unresolved', 'figure_unreferenced', 'table_unreferenced', 'unresolved_reference'], finding_codes
                            unified_issues = frame.locator('body').evaluate('() => window.reviewWorkspace.unifiedIssues.map(item => ({finding_key: item.finding_key, rule_id: item.rule_id, origin: item.origin, target: item.target, lifecycle: item.lifecycle}))')
                            validation = {'finding_rows': findings_count, 'finding_codes': finding_codes, 'unified_issues': unified_issues,
                                           'run_id': task['run_id'], 'checks_status': current_status, 'first_evidence': evidence_text,
                                          'escape_restores_focus': True, 'keyboard_end_opens_coverage': True,
                                          'return_to_reading': True, 'narrow_viewport': {'width': 390, 'height': 844},
                                          'page_errors': errors}
                            assert not errors, errors
                            measurements['pages'][kind]['workspace_validation'] = validation
                            if args.validate_offline_panels and kind == 'diff':
                                from validate_offline_panels import exercise
                                validation['offline_panels'] = exercise(page, frame, evidence, current)
                                assert not errors, errors
                        page.close()
                        measurements['application_source_sha256_at_last_checkpoint'] = source_hashes()
                        measurements['application_source_changed_during_capture'] = measurements['application_source_sha256_at_last_checkpoint'] != measurements['application_source_sha256']
                        (evidence / 'measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                finally:
                    browser.close()
        finally:
            # Never touch the user's default daemon, only the explicit temporary state.
            if (tmp / 'state/endpoint.json').exists():
                local_client.stop()
        measurements['capture_complete'] = True
        measurements['application_source_sha256_at_finish'] = source_hashes()
        measurements['application_source_changed_during_capture'] = measurements['application_source_sha256_at_finish'] != measurements['application_source_sha256']
        (evidence / 'measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: measurements[k] for k in ('phase', 'head', 'browser_version', 'diff_build_median_seconds', 'expand_parse_median_seconds')}, indent=2))


if __name__ == '__main__':
    main()
