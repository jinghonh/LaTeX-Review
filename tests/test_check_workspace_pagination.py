"""Actual Chromium, frozen synthetic 500-finding data; no service/provider backend."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from latex_review.checks import build_check_document, capture_snapshot, evaluate_current
from latex_review.checks.model import make_finding, summarize, validate_report


ROOT = Path(__file__).parents[1]
ASSETS = ROOT / 'src/latex_review'


def _report(tmp_path):
    path = tmp_path / 'main.tex'
    path.write_text(r'\documentclass{article}\begin{document}Synthetic prose \ref{missing}.\end{document}', encoding='utf-8')
    report = evaluate_current(build_check_document(capture_snapshot(path)))
    original = report['findings'][0]
    findings = []
    for index in range(500):
        finding = make_finding(original['rule_id'], f'pagination-fixture-{index:03d}',
                               deepcopy(original['evidence']),
                               severity='warning' if index % 2 == 0 else 'info',
                               message=f'Batch {"A" if index < 125 else "B"} synthetic {index:03d}',
                               related_occurrences=deepcopy(original['related_occurrences']))
        finding['review_state'] = 'confirmed' if index % 5 == 0 else 'unreviewed'
        findings.append(finding)
    report['findings'] = findings
    report['summary'] = summarize(report)
    return validate_report(report)


def _statistics(samples):
    values = [sample['two_animation_frames_ms'] for sample in samples]
    return {'samples': samples, 'count': len(values), 'median_ms': statistics.median(values),
            'p95_ms': sorted(values)[math.ceil(len(values) * .95) - 1], 'maximum_ms': max(values)}


def test_frozen_500_finding_pagination_filters_and_panel_responsiveness(tmp_path):
    report = _report(tmp_path)
    keys = [finding['finding_key'] for finding in report['findings']]
    serialized = json.dumps(report, ensure_ascii=False)
    html = ('<!doctype html><meta charset="utf-8"><meta name="latex-review-annotation-version" content="pagination-fixture">'
            '<style>' + (ASSETS / 'review_workspace.css').read_text(encoding='utf-8') + '</style>'
            '<main id="paper-original"><p>Untouched synthetic source reading α😀.</p></main>'
            '<script id="checks-data" type="application/json">' + serialized.replace('<', '\\u003c') + '</script>')
    requests, unexpected, errors = [], [], []
    timing = {'next_page': [], 'previous_page': [], 'panel_switch': [], 'filter': []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            request = route.request
            path = urlsplit(request.url).path
            requests.append({'method': request.method, 'path': path})
            if path == '/pagination-test':
                route.fulfill(body=html, content_type='text/html')
            elif path == '/api/checks/status' and request.method == 'GET':
                route.fulfill(body=json.dumps({'available': False, 'capabilities': {}, 'current_run': None,
                                              'message': 'Offline frozen synthetic pagination fixture'}), content_type='application/json')
            elif path == '/favicon.ico':
                route.fulfill(status=204)
            else:
                unexpected.append({'url': request.url, 'method': request.method})
                route.abort()

        page.route('**/*', serve)
        load_start = time.perf_counter()
        page.goto('http://localhost/pagination-test?panel=checks')
        page.evaluate("""() => {
          window.audit = {reportParses: 0, renders: 0, ready: false};
          window.originalPaper = document.getElementById('paper-original');
          const original = JSON.parse, frozen = document.getElementById('checks-data').textContent;
          JSON.parse = function(value, ...rest) {
            if (value === frozen) window.audit.reportParses++;
            return original.call(this, value, ...rest);
          };
          window.addEventListener('review-checks-updated', () => window.audit.renders++);
          window.addEventListener('review-workspace-capabilities', () => window.audit.ready = true);
          window.measureAction = async (kind, selector, value) => {
            const start = performance.now(), target = document.querySelector(selector);
            if (kind === 'input' || kind === 'change') {
              target.value = value;
              target.dispatchEvent(new Event(kind, {bubbles: true}));
            } else target.click();
            const handler = performance.now() - start;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {handler_ms: handler, two_animation_frames_ms: performance.now() - start};
          };
        }""")
        for filename in ('review_workspace.js', 'review_knowledge.js', 'review_semantic.js'):
            page.add_script_tag(path=str(ASSETS / filename))
        page.wait_for_function('window.audit.ready')
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        load_ms = (time.perf_counter() - load_start) * 1000

        def visible_keys():
            return page.locator('#workspace-checks [data-finding-key]').evaluate_all('(nodes) => nodes.map(node => node.dataset.findingKey)')

        def action(category, kind, selector, value=None):
            result = page.evaluate('args => window.measureAction(...args)', [kind, selector, value])
            result.update({'selector': selector, 'value': value})
            timing[category].append(result)

        def paging(category, label):
            selector = '#workspace-checks .workspace-actions button'
            # Address the actual paging button without reproducing production JS.
            page.get_by_role('button', name=label, exact=True).evaluate('(node) => node.id = "fixture-page-control"')
            action(category, 'click', '#fixture-page-control')

        assert visible_keys() == keys[:50]
        page.get_by_text('问题条目数 500 · 命中位置数 500', exact=True).wait_for()
        assert page.get_by_role('button', name='上一页', exact=True).count() == 0
        visited, forward_counts = [], []
        for index in range(10):
            rows = visible_keys()
            assert rows == keys[index * 50:(index + 1) * 50]
            visited.extend(rows)
            forward_counts.append(len(rows))
            if index < 9:
                paging('next_page', '下一页')
        assert len(visited) == len(set(visited)) == 500
        assert visited == keys
        assert page.get_by_role('button', name='下一页', exact=True).count() == 0
        for index in reversed(range(9)):
            paging('previous_page', '上一页')
            assert visible_keys() == keys[index * 50:(index + 1) * 50]
        assert page.get_by_role('button', name='上一页', exact=True).count() == 0

        action('filter', 'input', 'input[aria-label="筛选检查问题"]', 'Batch A')
        group_a = keys[:125]
        assert visible_keys() == group_a[:50]
        page.get_by_text('问题条目数 125 · 命中位置数 125', exact=True).wait_for()
        action('filter', 'change', 'select[aria-label="严重度"]', 'warning')
        filtered = [keys[index] for index in range(125) if index % 2 == 0]
        assert len(filtered) == 63 and visible_keys() == filtered[:50]
        page.get_by_text('问题条目数 63 · 命中位置数 63', exact=True).wait_for()
        paging('next_page', '下一页')
        assert visible_keys() == filtered[50:] and len(visible_keys()) == 13
        paging('previous_page', '上一页')
        assert visible_keys() == filtered[:50]
        action('filter', 'input', 'input[aria-label="筛选检查问题"]', '')
        assert visible_keys() == keys[::2][:50]
        page.get_by_text('问题条目数 250 · 命中位置数 250', exact=True).wait_for()
        action('filter', 'change', 'select[aria-label="严重度"]', '')
        action('filter', 'change', 'select[aria-label="人工状态"]', 'confirmed')
        confirmed = keys[::5]
        assert len(confirmed) == 100 and visible_keys() == confirmed[:50]
        page.get_by_text('问题条目数 100 · 命中位置数 100', exact=True).wait_for()
        paging('next_page', '下一页')
        assert visible_keys() == confirmed[50:]
        assert page.get_by_role('button', name='下一页', exact=True).count() == 0
        action('filter', 'change', 'select[aria-label="人工状态"]', '')
        assert visible_keys() == keys[:50]

        # Panel switching must preserve DOM nodes and must not rerender checks,
        # reparse the frozen report, issue requests, or initiate provider work.
        before_requests = list(requests)
        before_audit = page.evaluate('window.audit')
        page.evaluate('window.firstFinding = document.querySelector("[data-finding-key]")')
        for _ in range(5):
            for panel in ('reading', 'coverage', 'knowledge', 'semantic', 'checks'):
                action('panel_switch', 'click', '#workspace-tab-' + panel)
                assert page.locator('#workspace-' + panel).is_visible()
                assert page.locator('[role="tabpanel"]:visible').count() == 1
        assert requests == before_requests
        assert page.evaluate('window.audit') == before_audit
        assert page.evaluate('window.firstFinding === document.querySelector("[data-finding-key]")')
        assert page.evaluate('window.originalPaper === document.getElementById("paper-original")')
        assert visible_keys() == keys[:50]
        assert json.loads(page.locator('#checks-data').text_content()) == report
        assert not unexpected and not errors
        assert [request for request in requests if request['path'].startswith('/api/')] == [{'method': 'GET', 'path': '/api/checks/status'}]

        evidence = {
            'schema_version': 1,
            'scenario': 'Chromium offline frozen synthetic 500-finding pagination and panel switching',
            'scope': 'Actual production workspace/knowledge/semantic JavaScript and workspace CSS; intercepted static HTML/status only, no running backend or provider.',
            'limitations': ['Synthetic cloned findings share exact fixture source evidence; not a real-paper correctness or throughput claim.',
                            'No UI latency threshold was specified; timings are observed, not a pass against an invented threshold.',
                            'Two-animation-frame latency includes browser scheduling; load also includes Playwright script injection/driver roundtrips.'],
            'machine': {'platform': platform.platform(), 'architecture': platform.machine(), 'logical_cpus': os.cpu_count()},
            'browser': {'engine': 'Chromium', 'version': browser.version, 'headless': True,
                        'executable_override': os.environ.get('LATEX_REVIEW_BROWSER'), 'viewport': page.viewport_size},
            'production_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'dataset': {'findings': 500, 'page_size': 50, 'forward_page_row_counts': forward_counts,
                        'serialized_report_bytes': len(serialized.encode('utf-8')),
                        'ordered_finding_keys_sha256': sha256('\n'.join(keys).encode()).hexdigest(),
                        'combined_filter_matches': 63, 'combined_filter_last_page_rows': 13, 'confirmed_matches': 100},
            'load_ms': load_ms,
            'timings': {name: _statistics(samples) for name, samples in timing.items()},
            'checks': {'forward_exact_keys': True, 'reverse_exact_keys': True, 'no_duplicates_or_missing_keys': True,
                       'filter_counts_and_exact_keys': True, 'panels_preserve_check_rows_and_source_dom': True,
                       'panel_switch_additional_requests': 0, 'panel_switch_additional_check_renders': 0,
                       'panel_switch_additional_frozen_report_parses': 0, 'provider_calls': 0,
                       'frozen_report_unchanged': True, 'browser_errors': errors, 'unexpected_requests': unexpected},
            'requests': requests,
        }
        browser.close()
    destination = os.environ.get('LATEX_REVIEW_PAGINATION_EVIDENCE')
    if destination:
        Path(destination).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (tmp_path / 'pagination-500.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
