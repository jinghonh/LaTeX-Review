"""章节目录的层级、旧稿定位和审阅计数验收。"""
import pytest
expect = pytest.importorskip('playwright.sync_api').expect
from latex_review import compare_projects, parse_project, resolve_sources, write_report
from test_report_reading import browser_page, sentence_report


def test_live_section_click_keeps_clicked_heading_active(tmp_path, browser_page):
    from latex_review.live import build
    from latex_review.sources import ProjectSource
    from latex_review.contract import ComparisonSource
    text = '\\documentclass{article}\n\\begin{document}\n'
    for name in ('First', 'Second', 'Third'):
        text += '\\section{' + name + '}\n' + ('A long paragraph for scrolling. ' * 30 + '\n\n') * 8
    (tmp_path / 'main.tex').write_text(text + '\\end{document}')
    output = tmp_path / 'report'
    output.mkdir()
    build(ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path))), output)
    page = browser_page
    page.set_viewport_size({'width': 2048, 'height': 1157})
    page.goto((output / 'report.html').as_uri())
    page.evaluate("document.getElementById('translation-status').hidden = true")
    button = page.get_by_role('button', name='2 Second', exact=True)
    button.click()
    # Let the scroll handler run after the click's immediate highlight.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    expect(button).to_have_attribute('aria-current', 'location')


def test_sections_counts_and_panel_restore(sentence_report, browser_page):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(sentence_report.html.as_uri())
    expect(page.locator('#sections-side')).to_be_visible()
    expect(page.locator('.section-jump')).to_have_count(1)
    expect(page.locator('.section-count')).to_have_text('2 · 2')
    page.locator('.section-jump').click()
    expect(page.locator('.section-jump')).to_have_attribute('aria-current', 'location')
    page.locator('#next-change').click()
    page.locator('#reader-tools > summary').click()
    page.locator('#review-state').select_option('pending')
    expect(page.locator('.section-count')).to_have_text('2 · 1')
    page.locator('#toggle-changes').click()
    expect(page.locator('#sections-side')).to_be_hidden()
    page.locator('#close-changes').click()
    page.locator('#toggle-sections').click()
    page.reload()
    expect(page.locator('#sections-side')).to_be_visible()
    expect(page.locator('.section-count')).to_have_text('2 · 1')
    assert not errors


def test_sections_hierarchy_deleted_and_mobile(tmp_path, browser_page):
    for side in ('old', 'new'):
        directory = tmp_path / side
        directory.mkdir()
        deleted = '\\section{Deleted}\nRemoved text.\n' if side == 'old' else ''
        text = ('\\documentclass{article}\n\\begin{document}\n'
                '\\section{First}\nFirst text.\n' + deleted +
                '\\section{Last}\n\\subsection{Same}\nText.\n'
                '\\subsubsection{Deep}\nDeep text.\n\\subsection{Same}\nMore text.\n\\end{document}')
        (directory / 'main.tex').write_text(text)
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='', single_file=True)
    page = browser_page
    page.goto(report.html.as_uri())
    buttons = page.locator('.section-jump')
    assert buttons.count() == 6
    assert ['1 First', '2 Deleted', '2 Last'] == [buttons.nth(i).inner_text().split(' · ')[0].split('\n')[0] for i in range(3)]
    expect(page.get_by_role('button', name='Deep', exact=False)).to_be_hidden()
    summary = page.locator('#section-tree details > summary').nth(1)
    summary.click(position={'x': 3, 'y': 10})
    expect(page.get_by_role('button', name='Deep', exact=False)).to_be_visible()
    page.reload()
    expect(page.get_by_role('button', name='Deep', exact=False)).to_be_visible()
    page.get_by_role('button', name='Deleted', exact=False).click()
    expect(page.locator('#layout-mode')).to_have_value('compare')
    expect(page.locator('.search-target')).to_be_visible()
    page.get_by_role('button', name='Last', exact=False).click()
    for side in ('old', 'new'):
        panel = page.locator(f'.preview-side[data-side="{side}"]')
        heading = panel.locator('h2').filter(has_text='Last').first
        assert heading.bounding_box()['y'] >= panel.bounding_box()['y']
    page.locator('#layout-mode').select_option('reading')
    page.evaluate('''() => {
      const headings = [...document.querySelectorAll('.preview-side[data-side="new"] h2')];
      headings.find(node => node.textContent.trim().endsWith('Last')).scrollIntoView({block: 'start'});
    }''')
    expect(page.get_by_role('button', name='Last', exact=False)).to_have_attribute('aria-current', 'location')
    page.set_viewport_size({'width': 600, 'height': 800})
    page.get_by_role('button', name='First', exact=False).click()
    expect(page.locator('#sections-side')).to_be_hidden()
