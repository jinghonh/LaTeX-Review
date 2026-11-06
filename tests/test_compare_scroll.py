"""双稿在固定阅读区切换共用滚动与独立滚动。"""
import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from test_report_reading import browser_page


@pytest.fixture
def long_comparison(tmp_path):
    for side in ('old', 'new'):
        source = tmp_path / side
        source.mkdir()
        sections = []
        for section in ('Introduction', 'Results', 'Conclusion'):
            paragraphs = [f'The {side} {section} paragraph {index}. ' +
                          'The model predicts the boundary from its observation history. ' * 20
                          for index in range(4)]
            if side == 'old':
                paragraphs.insert(0, 'Additional context in the old paper. ' * 30)
            sections.append('\\section{' + section + '}\n' + '\n\n'.join(paragraphs))
        (source / 'main.tex').write_text(
            '\\documentclass{article}\n\\begin{document}\n' + '\n'.join(sections) +
            '\n\\end{document}', encoding='utf-8')
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        return write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='', single_file=True)


def settle(page):
    page.wait_for_timeout(200)


def position(page):
    return page.evaluate('''() => ({window: scrollY, shared: document.querySelector('main').scrollTop,
      panels: [...document.querySelectorAll('.preview-side')]
      .map(p => ({scroll: p.scrollTop, top: p.getBoundingClientRect().top}))})''')


@pytest.mark.parametrize('pointer', ['old', 'gap', 'new'])
def test_sync_wheel_scrolls_reading_area_everywhere(long_comparison, browser_page, pointer):
    page = browser_page
    page.goto(long_comparison.html.as_uri())
    page.locator('#layout-mode').select_option('compare')
    page.locator('#sync-scroll').check()
    settle(page)
    assert page.locator('main').evaluate('p => getComputedStyle(p).overflowY') == 'auto'
    assert page.locator('.report-header').is_hidden()
    assert all(not item.is_visible() for item in page.locator('.report-supplement').all())
    old = page.locator('.preview-side[data-side="old"]').bounding_box()
    new = page.locator('.preview-side[data-side="new"]').bounding_box()
    x = {'old': old['x'] + old['width'] / 2,
         'gap': (old['x'] + old['width'] + new['x']) / 2,
         'new': new['x'] + new['width'] / 2}[pointer]
    before = position(page)
    page.mouse.move(x, 400)
    page.mouse.wheel(0, 240)
    page.wait_for_function('(y) => document.querySelector("main").scrollTop > y + 100', arg=before['shared'])
    settle(page)
    after = position(page)
    delta = after['shared'] - before['shared']
    assert after['window'] == before['window'] == 0
    for index in range(2):
        assert after['panels'][index]['scroll'] == 0
        assert abs(before['panels'][index]['top'] - after['panels'][index]['top'] - delta) < 2

    page.locator('#sync-scroll').uncheck()
    page.locator('.preview-side[data-side="new"]').evaluate('p => p.scrollTop = 300')
    settle(page)
    before = position(page)
    new = page.locator('.preview-side[data-side="new"]').bounding_box()
    toolbar = page.locator('.report-controls').bounding_box()
    page.mouse.move(new['x'] + new['width'] / 2, max(new['y'], toolbar['y'] + toolbar['height']) + 80)
    page.mouse.wheel(0, 180)
    page.wait_for_function('''(value) => document.querySelector('.preview-side[data-side="new"]').scrollTop > value + 50''',
                           arg=before['panels'][1]['scroll'])
    after = position(page)
    assert abs(after['window'] - before['window']) < 2
    assert after['panels'][0]['scroll'] == before['panels'][0]['scroll']
    assert page.locator('.report-header').is_hidden()


def assert_visible_below_toolbar(page, locator):
    bounds = locator.bounding_box()
    bar = page.locator('.report-controls').bounding_box()
    assert bounds['y'] >= bar['y'] + bar['height'] - 2
    assert bounds['y'] < 850


def test_sync_navigation_and_reload_use_shared_scroll(long_comparison, browser_page):
    page = browser_page
    page.goto(long_comparison.html.as_uri())
    page.locator('#layout-mode').select_option('compare')
    page.locator('#sync-scroll').check()
    settle(page)
    page.locator('#toggle-changes').click()
    page.locator('.change-card .change-jump[data-new]:not([data-new=""])').last.click()
    settle(page)
    assert_visible_below_toolbar(page, page.locator('.preview-side[data-side="new"] .is-highlighted').first)
    page.locator('#toggle-sections').click()
    page.locator('.section-jump').filter(has_text='Results').click()
    settle(page)
    assert_visible_below_toolbar(page, page.locator('.preview-side[data-side="new"] h2').filter(has_text='Results'))
    assert all(p['scroll'] == 0 for p in position(page)['panels'])
    page.locator('.preview-side[data-side="new"] h2').filter(has_text='Conclusion').evaluate(
        'heading => heading.scrollIntoView({block: "start"})')
    settle(page)
    assert page.locator('.section-jump[aria-current="location"]').inner_text().startswith('3 Conclusion')
    page.evaluate('document.querySelector("main").scrollTop += 240')
    page.wait_for_timeout(350)
    before = position(page)
    page.reload()
    settle(page)
    assert page.locator('#sync-scroll').is_checked()
    assert abs(position(page)['shared'] - before['shared']) < 3
    assert position(page)['window'] == 0
    assert page.locator('.preview-side[data-side="new"]').evaluate('p => getComputedStyle(p).overflowY') == 'visible'
    page.locator('main').evaluate('p => p.scrollTop = p.scrollHeight')
    settle(page)
    page.mouse.move(800, 500)
    page.mouse.wheel(0, 500)
    settle(page)
    assert position(page)['window'] == 0
    assert all(not item.is_visible() for item in page.locator('.report-supplement').all())
    page.locator('#layout-mode').select_option('reading')
    assert page.locator('.report-header').is_visible()
    assert page.locator('.report-supplement').first.is_visible()


def test_shared_scroll_resize_keeps_reading_position(long_comparison, browser_page):
    page = browser_page
    page.goto(long_comparison.html.as_uri())
    page.locator('#layout-mode').select_option('compare')
    page.locator('#sync-scroll').check()
    settle(page)
    page.locator('.preview-side[data-side="new"] .node-paragraph').nth(5).evaluate(
        'p => p.scrollIntoView({block: "center"})')
    settle(page)
    # Capture the same character used by the reading-position helper.
    page.evaluate('''() => {
      const main = document.querySelector('main'), paper = document.querySelector('.preview-side[data-side="new"]');
      const r = main.getBoundingClientRect(), p = paper.getBoundingClientRect();
      const y = r.top + r.height * .35, x = p.left + p.width * .4;
      const caret = document.caretPositionFromPoint(x, y);
      const range = document.createRange(); range.setStart(caret.offsetNode, caret.offset);
      range.setEnd(caret.offsetNode, caret.offset + 1);
      window.testRange = range;
      window.testRatio = (range.getBoundingClientRect().top - r.top) / r.height;
    }''')
    for width in (1500, 1024):
        page.set_viewport_size({'width': width, 'height': 900})
        settle(page)
        error = page.evaluate('''() => {
          const r = document.querySelector('main').getBoundingClientRect();
          return Math.abs(testRange.getBoundingClientRect().top - r.top - r.height * testRatio);
        }''')
        # Reflow must keep the same text line, allowing sub-line rounding.
        line_height = page.evaluate('parseFloat(getComputedStyle(testRange.startContainer.parentElement).lineHeight)')
        assert error < line_height / 2
    page.locator('#layout-mode').select_option('reading')
    assert page.locator('.report-header').is_visible()
