"""审阅记录隔离、备份冲突及全文搜索的浏览器验收。"""
import json
import shutil
from pathlib import Path

import pytest

expect = pytest.importorskip('playwright.sync_api').expect

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from test_report_reading import browser_page, reading_report, sentence_report


def open_tools(page, report):
    page.goto(report.html.as_uri())
    page.locator('#reader-tools > summary').click()


def test_review_restore_and_backup(sentence_report, browser_page, tmp_path):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    open_tools(page, sentence_report)
    page.locator('#next-change').click()
    page.locator('#review-state').select_option('reviewed')
    page.locator('#review-note').fill('核对完成')
    expect(page.locator('#review-progress')).to_contain_text('已审阅 1 /')
    with page.expect_download() as download:
        page.locator('#review-export').click()
    backup = tmp_path / 'backup.json'
    download.value.save_as(backup)
    saved = json.loads(backup.read_text())
    assert saved['records']['change-000001'] == {'state': 'reviewed', 'note': '核对完成'}
    page.reload()
    expect(page.locator('#reader-tools')).to_have_attribute('open', '')
    expect(page.locator('#review-state')).to_have_value('reviewed')
    expect(page.locator('#review-note')).to_have_value('核对完成')
    page.locator('#review-note').fill('本地备注优先')
    saved['records']['change-000002'] = {'state': 'pending', 'note': '待核查'}
    backup.write_text(json.dumps(saved))
    page.locator('#review-import').set_input_files(backup)
    expect(page.locator('#review-storage-status')).to_contain_text('补充 1 项，保留本地 1 项')
    expect(page.locator('#review-note')).to_have_value('本地备注优先')
    page.locator('#review-filter').select_option('pending')
    assert page.locator('.change-card:not([hidden])').count() == 1
    page.locator('#next-change').click()
    expect(page.locator('#review-note')).to_have_value('待核查')
    saved['fingerprint'] = 'wrong-report'
    backup.write_text(json.dumps(saved))
    page.locator('#review-import').set_input_files(backup)
    expect(page.locator('#review-storage-status')).to_contain_text('备份不属于当前报告')
    assert not errors


def test_search_keeps_filters_and_updates_translation(sentence_report, browser_page):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    open_tools(page, sentence_report)
    page.locator('#toggle-changes').click()
    page.locator('#kind-filter').select_option('added')
    page.locator('#close-changes').click()
    page.locator('#reading-mode').select_option('context')
    page.locator('#report-search').fill('颜色用于提示')
    expect(page.locator('#search-results button')).to_have_count(1)
    page.locator('#search-results button').click()
    expect(page.locator('#layout-mode')).to_have_value('compare')
    expect(page.locator('#kind-filter')).to_have_value('added')
    assert page.locator('.search-target').is_visible()
    target_id = page.locator('.search-target').get_attribute('data-node-id')
    page.locator('#report-search').fill('新到达译文')
    expect(page.locator('#search-status')).to_have_text('没有匹配内容。')
    page.evaluate('''id => {
      const node = document.getElementById('old-' + id);
      const block = document.createElement('div'); block.className = 'translation-block';
      block.textContent = '新到达译文'; node.append(block);
      document.dispatchEvent(new Event('review-translations-updated'));
    }''', target_id)
    expect(page.locator('#search-results button')).to_have_count(1)
    page.locator('#search-results button').click()
    expect(page.locator('#translation-mode')).to_have_value('bilingual')
    page.locator('#report-search').fill('')
    section = page.locator('#section-filter option').nth(1).get_attribute('value')
    page.locator('#section-filter').select_option(section)
    assert page.locator('#search-results button').count() > 0
    page.reload()
    expect(page.locator('#section-filter')).to_have_value(section)
    expect(page.locator('#kind-filter')).to_have_value('added')
    assert not errors


def test_fingerprint_same_content_and_changed_dependency(tmp_path):
    fixture = Path(__file__).parent / 'fixtures/e2e'
    for side in ('old', 'new'):
        shutil.copytree(fixture / side, tmp_path / side)
    def generate(output):
        with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
            old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
            report = write_report(old, new, compare_projects(old, new), output, pdf_converter='')
        import re
        return json.loads(re.search(r'id="reader-data">(.*?)</script>', report.html.read_text(), re.S)[1])
    first = generate(tmp_path / 'one')
    second = generate(tmp_path / 'two')
    assert first['fingerprint'] == second['fingerprint']
    assert any(node['citations'] for node in first['nodes'])
    bibliography = tmp_path / 'new/references.bib'
    bibliography.write_text(bibliography.read_text() + '\n% dependency changed\n')
    assert generate(tmp_path / 'two')['fingerprint'] != first['fingerprint']


def test_single_file_search_and_storage_unavailable(reading_report, browser_page, tmp_path):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("Storage.prototype.setItem = function () { throw new Error('disabled'); }")
    page.goto(reading_report.single_html.as_uri())
    page.locator('#reader-tools > summary').click()
    page.locator('#report-search').fill('beta')
    assert page.locator('#search-results button').count() >= 1
    page.locator('#search-results button').first.click()
    assert page.locator('.search-target').is_visible()
    page.locator('#next-change').click()
    editor = page.locator('.inline-change[open] .change-review-editor')
    editor.locator('select').select_option('pending')
    editor.locator('textarea').fill('无法保存也能导出')
    expect(page.locator('#review-storage-status')).to_contain_text('浏览器无法保存记录')
    with page.expect_download() as download:
        page.locator('#review-export').click()
    backup = tmp_path / 'unavailable.json'
    download.value.save_as(backup)
    assert any(record['note'] == '无法保存也能导出' for record in json.loads(backup.read_text())['records'].values())
    page.set_viewport_size({'width':390, 'height':844})
    page.wait_for_function('document.documentElement.scrollWidth <= innerWidth')
    assert not errors
