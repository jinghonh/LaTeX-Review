"""新稿阅读模式、内联对照及筛选的浏览器回归。"""
from pathlib import Path

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report


@pytest.fixture
def reading_report(tmp_path):
    fixture = Path(__file__).parent / 'fixtures' / 'e2e'
    with resolve_sources(entry='main.tex', old_dir=fixture / 'old', new_dir=fixture / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='', single_file=True)
    return report


@pytest.fixture
def browser_page():
    api = pytest.importorskip('playwright.sync_api')
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except api.Error as exc:
            pytest.skip(f'浏览器运行环境不可用：{exc}')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        # These interactions must also work when the optional math CDN is unavailable.
        page.route('**/*', lambda route: route.abort() if route.request.url.startswith('https://') else route.continue_())
        yield page
        browser.close()


def test_reading_inline_navigation_and_filters(reading_report, browser_page):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(reading_report.html.as_uri())
    assert page.locator('.preview-side[data-side="new"]').is_visible()
    assert page.locator('.preview-side[data-side="old"]').is_hidden()
    assert page.locator('#changes-side').is_hidden()
    assert page.locator('.inline-change[open]').count() == 0
    assert page.evaluate('scrollY') == 0
    assert page.locator('.inline-change').count() == len(reading_report.document.changes)
    assert page.evaluate('new Set([...document.querySelectorAll("[id]")].map(e => e.id)).size === document.querySelectorAll("[id]").length')

    page.locator('#next-change').click()
    first = page.locator('.inline-change').first
    assert first.get_attribute('open') is not None
    assert page.locator('#current-change').inner_text().startswith('第 1 /')
    assert page.evaluate("document.activeElement.tagName === 'SUMMARY'")
    page.keyboard.press('Alt+ArrowDown')
    assert page.locator('#current-change').inner_text().startswith('第 2 /')

    page.set_viewport_size({'width': 1600, 'height': 1000})
    page.locator('#toggle-changes').click()
    assert page.locator('#changes-side').bounding_box()['x'] == 0
    page.locator('#kind-filter').select_option('removed')
    removed = page.locator('.change-card:visible')
    assert removed.count() > 0
    card_id = removed.first.get_attribute('id')
    removed.first.locator('.change-jump').click()
    detail = page.locator('#inline-' + card_id)
    assert detail.get_attribute('open') is not None
    assert detail.locator('.old-excerpt').inner_text().strip()
    page.locator('#close-changes').click()
    assert page.locator('.inline-change:visible').count() == sum(c.kind == 'removed' for c in reading_report.document.changes)

    page.locator('#toggle-changes').click()
    page.locator('#kind-filter').select_option('all')
    page.locator('#category-filter').select_option('table')
    page.locator('.change-card:visible .change-jump').first.click()
    table_detail = page.locator('.inline-change[open]:visible').first
    table_detail.locator('.detail-jump').first.click()
    assert page.locator('.preview-side[data-side="new"] td.is-cell-highlighted').count() > 0
    assert table_detail.locator('.old-excerpt td.is-cell-highlighted').count() > 0
    page.locator('#category-filter').select_option('all')
    page.locator('#close-changes').click()
    page.locator('#layout-mode').select_option('compare')
    assert page.locator('.preview-side[data-side="old"]').is_visible()
    assert page.locator('.inline-change:visible').count() == 0
    page.locator('#layout-mode').select_option('reading')
    assert page.locator('.inline-change:visible').count() == len(reading_report.document.changes)
    page.locator('#reading-mode').select_option('context')
    page.locator('#next-change').click()
    assert page.locator('.inline-change[open]:visible').count() > 0
    page.locator('#reading-mode').select_option('full')
    assert not errors


def test_single_file_narrow_layout_and_math_failure(reading_report, browser_page):
    page = browser_page
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(reading_report.single_html.as_uri())
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.locator('.inline-change > summary').first.click()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.locator('.inline-change[open] .inline-close').first.click()
    assert page.locator('.inline-change[open]').count() == 0
    page.locator('#toggle-changes').click()
    assert page.locator('#changes-side').is_visible()
    page.keyboard.press('Escape')
    assert page.locator('#changes-side').is_hidden()
    assert page.locator('#toggle-changes').get_attribute('aria-expanded') == 'false'
    assert page.locator('#math-status').get_attribute('data-state') == 'failed'
