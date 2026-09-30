"""新稿阅读模式、页边对照及筛选的浏览器回归。"""
from pathlib import Path
import os

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
def sentence_report(tmp_path):
    versions = {
        'old': '审阅过程以完整段落为基本单位。颜色用于提示已经改变的句子，正文的层级、间距与章节关系保持连续。\n\n'
               '我们以新稿作为主要阅读对象，将旧文与变更说明放在对应段落附近。连续阅读时呈现必要的标记，需要检查细节时再展开完整对照。',
        'new': '审阅过程以完整段落为基本单位。细线用于提示已经改变的句子，正文的层级、间距与章节关系保持连续。\n\n'
               '我们以新稿作为主要阅读对象，将旧文与变更说明放在对应段落右侧。连续阅读时呈现必要的标记，需要检查细节时再展开完整对照。',
    }
    for side, text in versions.items():
        directory = tmp_path / side
        directory.mkdir()
        (directory / 'main.tex').write_text(
            '\\documentclass{article}\n\\begin{document}\n\\section{阅读与定位}\n' + text + '\n\\end{document}\n',
            encoding='utf-8',
        )
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        return write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='', single_file=True)


@pytest.fixture
def browser_page():
    api = pytest.importorskip('playwright.sync_api')
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("LATEX_REVIEW_BROWSER"))
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
    assert first.get_attribute('open') is None
    assert page.locator('.inline-change[open]').count() == 1

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
    assert detail.locator('.old-excerpt').is_hidden()
    detail.locator('.inline-full-context > summary').click()
    assert detail.locator('.old-excerpt').inner_text().strip()
    assert detail.locator('.new-excerpt').count() == 0
    assert page.locator('.deletion-marker.is-active-change').count() == 1
    page.locator('#close-changes').click()
    assert page.locator('.inline-change:visible').count() == sum(c.kind == 'removed' for c in reading_report.document.changes)
    detail.locator('.inline-close').click()
    page.locator('.deletion-marker').first.click()
    assert detail.get_attribute('open') is not None

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


def assert_margin_notes_do_not_overlap(page):
    boxes = page.locator('.inline-change:visible').evaluate_all(
        '(notes) => notes.map(note => { const rect = note.getBoundingClientRect(); '
        'return {top: rect.top, bottom: rect.bottom, left: rect.left}; })'
    )
    paper = page.locator('.preview-side[data-side="new"]').bounding_box()
    for box in boxes:
        assert box['left'] >= paper['x'] + paper['width'] + 20
    for previous, following in zip(boxes, boxes[1:]):
        assert following['top'] >= previous['bottom'] + 10
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')


def test_underlined_sentence_opens_matching_margin_comparison(sentence_report, browser_page):
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(sentence_report.html.as_uri())
    assert 'margin-notes' in page.locator('body').get_attribute('class')
    assert_margin_notes_do_not_overlap(page)
    sentences = page.locator('.change-trigger')
    assert sentences.count() > 1
    change, sentence_detail = next(
        (change, detail) for change in sentence_report.document.changes for detail in change.details
        if change.kind == 'modified' and detail.old_sentences and detail.new_sentences
    )
    first = page.locator(f'#new-{change.new_node_id}-sentence-{sentence_detail.new_sentences[0]}')
    detail_id = first.get_attribute('aria-controls')
    detail = page.locator('#' + detail_id)
    first.click()
    assert detail.get_attribute('open') is not None
    assert detail.get_attribute('aria-current') == 'true'
    assert first.get_attribute('aria-expanded') == 'true'
    assert 'is-highlighted' in first.get_attribute('class')
    assert detail.locator('.old-excerpt').is_hidden()
    assert detail.locator('.new-excerpt').is_hidden()
    assert detail.locator('.compact-diff').is_visible()
    assert detail.locator('.detail-jump.is-selected-detail').count() == 1
    assert detail.locator('.diff-removed').inner_text() == '颜色'
    assert detail.locator('.diff-added').inner_text() == '细线'
    assert '审阅过程以完整段落为基本单位' not in detail.locator('.compact-diff').inner_text()
    assert detail.locator('.old-excerpt .is-highlighted').count() > 0
    assert detail.locator('.new-excerpt .is-highlighted').count() > 0
    summary = detail.locator(':scope > summary').bounding_box()
    toolbar = page.locator('.report-controls').bounding_box()
    assert toolbar['y'] + toolbar['height'] <= summary['y'] < page.viewport_size['height']
    detail.locator('.inline-full-context > summary').click()
    assert detail.locator('.old-excerpt').is_visible()
    assert detail.locator('.new-excerpt').is_visible()
    detail.locator('.inline-full-context > summary').click()
    assert detail.locator('.old-excerpt').is_hidden()
    assert_margin_notes_do_not_overlap(page)

    page.locator('#layout-mode').select_option('compare')
    assert first.get_attribute('tabindex') == '-1'
    assert first.get_attribute('role') is None
    page.locator('#layout-mode').select_option('reading')
    assert first.get_attribute('tabindex') == '0'
    assert first.get_attribute('role') == 'button'

    other = page.locator('.change-trigger:not([aria-controls="' + detail_id + '"])').first
    other.focus()
    page.keyboard.press('Enter')
    assert detail.get_attribute('open') is None
    assert first.get_attribute('aria-expanded') == 'false'
    assert other.get_attribute('aria-expanded') == 'true'
    assert page.locator('.inline-change[open]').count() == 1
    opened = page.locator('.inline-change[open]')
    opened.locator('.inline-close').click()
    assert other.get_attribute('aria-expanded') == 'false'
    other.focus()
    page.keyboard.press('Space')
    assert page.locator('.inline-change[open]').count() == 1
    assert_margin_notes_do_not_overlap(page)

    page.set_viewport_size({'width': 390, 'height': 844})
    page.wait_for_function('!document.body.classList.contains("margin-notes")')
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    assert page.locator('.inline-change[open]').evaluate('note => getComputedStyle(note).position') == 'static'
    assert not errors


def test_sentence_reveals_filtered_card_and_print_restores_flow(sentence_report, browser_page):
    page = browser_page
    page.goto(sentence_report.single_html.as_uri())
    first = page.locator('.change-trigger').first
    detail = page.locator('#' + first.get_attribute('aria-controls'))
    page.locator('#toggle-changes').click()
    page.locator('#kind-filter').select_option('removed')
    assert detail.is_hidden()
    page.locator('#close-changes').click()
    first.click()
    assert page.locator('#kind-filter').input_value() == 'all'
    assert detail.is_visible()
    assert detail.get_attribute('open') is not None
    assert_margin_notes_do_not_overlap(page)
    page.emulate_media(media='print')
    assert detail.evaluate('note => getComputedStyle(note).position') == 'static'
    assert detail.bounding_box()['width'] <= page.locator('.preview-side[data-side="new"]').bounding_box()['width']


@pytest.mark.parametrize('before,after,removed,added,groups', [
    ('A shared opening has many ordinary words before we find a small gain using '
     'the same sequence of observations across all experiments with an unchanged '
     'protocol and a stable result followed by many more ordinary words at the end.',
     'A shared opening has many ordinary words before we find a large gain using '
     'the same sequence of observations across all experiments with an unchanged '
     'protocol and a robust result followed by many more ordinary words at the end.',
     ['small', 'stable'], ['large', 'robust'], 2),
    ('We repeat the same same small finding.', 'We repeat the same same large finding.',
     ['small'], ['large'], 1),
    ('Keep this. End here.', 'Keep this. Add this. End here.', [], ['Add this.'], 1),
    ('Keep this. Remove this. End here.', 'Keep this. End here.', ['Remove this.'], [], 1),
    (r'We use $x_1$ and cite \cite{alpha}.', r'We use $x_2$ and cite \cite{beta}.',
     None, None, 1),
    (' '.join(['shared'] * 600) + ' small result.', ' '.join(['shared'] * 600) + ' large result.',
     ['small'], ['large'], 1),
])
def test_compact_comparison_preserves_changes_and_limits_context(tmp_path, browser_page,
                                                               before, after, removed, added, groups):
    for side, text in (('old', before), ('new', after)):
        folder = tmp_path / side
        folder.mkdir()
        (folder / 'main.tex').write_text('\\begin{document}\n' + text + '\n\\end{document}')
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='')
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(report.html.as_uri())
    page.locator('#next-change').click()
    note = page.locator('.inline-change[open]')
    assert note.locator('.diff-group').count() == groups
    assert note.locator('.inline-full-context').get_attribute('open') is None
    if removed is not None:
        assert note.locator('.diff-removed').all_text_contents() == removed
        assert note.locator('.diff-added').all_text_contents() == added
    else:
        assert note.locator('.diff-removed .math-tex').count() == 1
        assert note.locator('.diff-added .math-tex').count() == 1
        assert note.locator('.diff-removed .citation').inner_text() == '[alpha]'
        assert note.locator('.diff-added .citation').inner_text() == '[beta]'
    if groups == 2:
        text = note.locator('.compact-diff').inner_text()
        assert 'A shared opening' not in text and 'at the end' not in text
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    note.locator('.inline-full-context > summary').click()
    assert note.locator('.old-excerpt').is_visible()
    assert note.locator('.new-excerpt').is_visible()
    assert not errors
