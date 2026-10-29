"""Real browser verification of the resident entry and stable paper links."""
import os
from importlib.resources import files
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from latex_review import local_client
from latex_review.live import prepare_request
from test_local_core import paper, diff_request
from test_local_workbench import stopped


def test_dashboard_open_stop_start_and_narrow_layout(tmp_path):
    root, entry = paper(tmp_path)
    live = prepare_request(entry, root=root, poll_files=True)
    local_client.ensure(live)
    diff = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    diff_result = local_client.ensure(diff)
    endpoint = local_client.endpoint()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'), headless=True)
        try:
            page = browser.new_page(viewport={'width': 1150, 'height': 850}, color_scheme='light')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{endpoint['port']}/")
            page.wait_for_load_state('networkidle')
            expect(page.get_by_role('heading', name='本地论文', exact=True)).to_be_visible()
            expect(page.locator('.paper')).to_have_count(1)
            expect(page.locator('.slot').get_by_text('运行中', exact=True)).to_have_count(2)
            live_card = page.locator('.slot').nth(0)
            live_card.get_by_role('button', name='停止', exact=True).click()
            expect(live_card.get_by_text('已停止', exact=True)).to_be_visible(timeout=15000)
            live_card.get_by_role('button', name='启动', exact=True).click()
            expect(live_card.get_by_text('运行中', exact=True)).to_be_visible(timeout=15000)
            with page.expect_popup() as opened:
                page.locator('.slot').nth(1).get_by_role('link', name='打开', exact=True).click()
            report = opened.value
            report.wait_for_load_state('networkidle')
            assert report.url == diff_result['url']
            expect(report.frame_locator('iframe').get_by_role('heading', name='First report')).to_be_visible()
            frame = report.frames[1]
            assert frame.evaluate("fetch('api/report').then(r=>r.status)") == 200
            assert frame.evaluate("fetch('api/v1/papers').then(r=>r.status)") == 404
            (Path(diff['directory']) / 'report.html').write_text('<h1>Second baseline</h1>')
            updated = local_client.ensure(diff)
            assert updated['pid'] != diff_result['pid']
            expect(report.frame_locator('iframe').get_by_role('heading', name='Second baseline')).to_be_visible(timeout=15000)
            assert report.url == diff_result['url']
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            screenshot = Path('/tmp/latex-review-local-entry-20261013/implementation-browser.png')
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            assert not errors
        finally:
            browser.close()


def test_report_history_shows_background_import_progress():
    papers = [{'paper_id': str(index), 'root': f'/paper/{index}', 'entry': 'main.tex',
               'history_import': status, 'history_initialized': initialized, 'slots': {}}
              for index, (status, initialized) in enumerate((
                  ('pending', False), ('importing', False), ('complete', True),
                  ('pending', True), ('importing', True)))]
    reports = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'), headless=True)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def respond(route):
                path = route.request.url.removeprefix('http://workbench.test')
                if path == '/':
                    route.fulfill(content_type='text/html', body=files('latex_review').joinpath('local_dashboard.html').read_text())
                elif path == '/api/v1/papers':
                    route.fulfill(json={'papers': papers})
                elif path == '/api/v1/history/operations':
                    route.fulfill(json={'operations': []})
                elif path == '/api/v1/history/reports':
                    route.fulfill(json={'reports': reports, 'errors': []})
                else:
                    route.abort()

            page.route('http://workbench.test/**', respond)
            page.goto('http://workbench.test/')
            expect(page.locator('.paper')).to_have_count(5)
            page.locator('[data-view="reports"]').click()
            expect(page.locator('#view-caption')).to_contain_text('首次导入：等待 1 篇 · 正在导入 1 篇')
            expect(page.locator('#papers')).to_contain_text('正在导入现有报告')
            expect(page.locator('#papers')).to_contain_text('完成后自动显示')
            expect(page.locator('#papers')).not_to_contain_text('暂无历史报告')
            papers[0]['history_import'] = 'complete'
            papers[1]['history_import'] = 'complete'
            page.evaluate('refresh()')
            expect(page.locator('#view-caption')).not_to_contain_text('首次导入')
            expect(page.locator('#papers')).to_contain_text('暂无历史报告')
            reports.append({'paper_id': '0', 'report_id': 'saved-report', 'reason': '启用工作台时导入'})
            page.evaluate('refresh()')
            expect(page.locator('#view-caption')).not_to_contain_text('首次导入')
            expect(page.locator('#papers .record')).to_have_count(1)
            expect(page.locator('#papers')).not_to_contain_text('正在导入现有报告')
            assert not errors
        finally:
            browser.close()


def test_workbench_search_metadata_and_refresh_preserve_details(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    local_client.ensure(request)
    endpoint = local_client.endpoint()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'), headless=True)
        try:
            page = browser.new_page(viewport={'width': 1300, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{endpoint['port']}/")
            expect(page.locator('.paper')).to_have_count(1)
            page.get_by_role('button', name='main.tex', exact=True).click()
            diagnostic = page.locator('#detail details').filter(has=page.get_by_text('差异报告 · 诊断与日志', exact=True)).first
            diagnostic.locator('summary').first.click()
            # Refresh through the same function used by the five-second poll.
            page.locator('#search').fill('main.tex')
            page.evaluate('refresh()')
            expect(page.locator('#search')).to_be_focused()
            expect(page.locator('#search')).to_have_value('main.tex')
            expect(diagnostic).to_have_attribute('open', '')
            page.wait_for_function('!state.refreshing')
            page.locator('#refresh').focus()
            page.evaluate('refresh()')
            expect(page.locator('#refresh')).to_be_enabled()
            expect(page.locator('#refresh')).to_be_focused()
            expect(diagnostic).to_have_attribute('open', '')
            page.get_by_role('button', name='编辑别名', exact=True).click()
            page.locator('#alias-input').fill('正文测试')
            page.evaluate('refresh()')
            expect(page.locator('#alias-input')).to_have_value('正文测试')
            expect(page.locator('#alias-input')).to_be_focused()
            page.get_by_role('button', name='保存', exact=True).click()
            expect(page.locator('#alias-dialog')).not_to_be_visible()
            page.locator('#search').fill('正文测试')
            expect(page.locator('.paper')).to_have_count(1)
            page.get_by_role('button', name='置顶 正文测试', exact=True).click()
            expect(page.get_by_role('button', name='取消置顶 正文测试', exact=True)).to_have_attribute('aria-pressed', 'true')
            stopped(request['paper_id'])
            page.evaluate('refresh()')
            page.get_by_role('button', name='归档论文', exact=True).click()
            expect(page.locator('.paper')).to_have_count(0)
            page.locator('[data-view="archived"]').click()
            expect(page.locator('.paper')).to_have_count(1)
            expect(page.locator('.paper').get_by_role('button', name='启动', exact=True)).to_have_count(0)
            page.get_by_role('button', name='取消归档', exact=True).click()
            page.locator('[data-view="reports"]').click()
            expect(page.locator('.record').get_by_role('link', name='打开', exact=True)).to_have_count(1)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
        finally:
            browser.close()
