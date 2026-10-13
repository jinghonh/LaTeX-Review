"""Real browser verification of the resident entry and stable paper links."""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from latex_review import local_client
from latex_review.live import prepare_request
from test_local_core import paper, diff_request


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
            expect(page.get_by_text('运行中', exact=True)).to_have_count(2)
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
            assert frame.evaluate("fetch('/api/report').then(r=>r.json()).then(v=>v.report_url)") == frame.url
            assert frame.evaluate("fetch('/api/v1/papers').then(r=>r.status)") == 404
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
