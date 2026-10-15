"""Resize keeps the same text line in live, report and independently scrolled panels."""
import pytest

from latex_review import compare_projects, parse_project, render_preview, resolve_sources, write_report
from latex_review.contract import ComparisonSource
from latex_review.live import build
from latex_review.sources import ProjectSource
from test_report_reading import browser_page


@pytest.fixture(params=['live', 'report', 'standalone'])
def long_preview(request, tmp_path):
    paragraphs = [' '.join(f'paragraph{index}word{word}' for word in range(150)) + '.' for index in range(20)]
    paragraphs.insert(10, ' '.join(f'longword{word}' for word in range(2400)) + '.')
    source = '\\documentclass{article}\n\\begin{document}\n\\section{Reading}\n' + '\n\n'.join(paragraphs) + '\n\\end{document}'
    for side in ('old', 'new'):
        directory = tmp_path / side
        directory.mkdir()
        (directory / 'main.tex').write_text(source)
    output = tmp_path / 'report'
    output.mkdir()
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        if request.param == 'report':
            write_report(old, new, compare_projects(old, new), output, pdf_converter='', single_file=True)
        elif request.param == 'standalone':
            (output / 'report.html').write_text(render_preview(None, new).html)
        else:
            build(ProjectSource('new', tmp_path / 'new', 'main.tex', ComparisonSource('directory', str(tmp_path / 'new'))), output)
    return request.param, output / 'report.html'


# Save the same visible character that a reader sees, before any width change.
CAPTURE = '''(side) => {
  const p = document.querySelector('.preview-side[data-side="' + side + '"]');
  const long = [...p.querySelectorAll('.node-paragraph p')].find(e => e.textContent.includes('longword1000'));
  const walker = document.createTreeWalker(long, NodeFilter.SHOW_TEXT); let text = walker.nextNode();
  while (text && !text.textContent.includes('longword1000')) text = walker.nextNode();
  const point = document.createRange(); point.setStart(text, text.textContent.indexOf('longword1000')); point.setEnd(text, point.startOffset + 1);
  const independent = /auto|scroll/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight;
  const bar = document.querySelector('.report-controls');
  const top = independent ? p.getBoundingClientRect().top + p.clientTop : (bar && getComputedStyle(bar).position === 'sticky' ? Math.max(0, bar.getBoundingClientRect().bottom) : 0);
  const bottom = independent ? Math.min(innerHeight, p.getBoundingClientRect().bottom) : innerHeight;
  const delta = point.getBoundingClientRect().top - top - (bottom - top) * .35;
  if (independent) p.scrollTop += delta; else window.scrollBy(0, delta);
}'''
SAVE = '''(side) => {
  const p = document.querySelector('.preview-side[data-side="' + side + '"]');
  const r = p.getBoundingClientRect(), bar = document.querySelector('.report-controls');
  const independent = /auto|scroll/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight;
  const top = independent ? Math.max(0, r.top + p.clientTop) : (bar && getComputedStyle(bar).position === 'sticky' ? Math.max(0, bar.getBoundingClientRect().bottom) : 0);
  const bottom = independent ? Math.min(innerHeight, r.bottom) : innerHeight;
  const x = Math.max(1, Math.min(innerWidth - 1, r.left + r.width * .4)), y = top + (bottom - top) * .35;
  const caret = document.caretPositionFromPoint?.(x, y); let range;
  if (caret) {range = document.createRange(); range.setStart(caret.offsetNode, caret.offset);}
  else range = document.caretRangeFromPoint(x,y);
  range.setEnd(range.startContainer, range.startOffset + 1);
  window.testAnchors ||= {}; testAnchors[side] = {range, ratio: (range.getBoundingClientRect().top-top)/(bottom-top)};
  return range.toString();
}'''
ERROR = '''(side) => {
  const p = document.querySelector('.preview-side[data-side="' + side + '"]');
  const r = p.getBoundingClientRect(), bar = document.querySelector('.report-controls');
  const independent = /auto|scroll/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight;
  const top = independent ? Math.max(0, r.top+p.clientTop) : (bar && getComputedStyle(bar).position === 'sticky' ? Math.max(0, bar.getBoundingClientRect().bottom) : 0);
  const bottom = independent ? Math.min(innerHeight,r.bottom) : innerHeight;
  const anchor = testAnchors[side];
  return Math.abs(anchor.range.getBoundingClientRect().top - top - anchor.ratio * (bottom-top));
}'''


def test_window_and_sidebar_resize_preserve_character(long_preview, browser_page):
    mode, path = long_preview
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(path.as_uri())
    page.wait_for_timeout(150)
    page.evaluate(CAPTURE, 'new')
    page.wait_for_timeout(100)
    assert page.evaluate(SAVE, 'new')
    for width, height in ((1040, 780), (760, 900), (1280, 900)):
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_timeout(150)
        assert page.evaluate(ERROR, 'new') < 3
        # Keep the same reading character through the next resize.
    if mode != 'standalone':
        if page.locator('#sections-side').is_visible():
            page.locator('#close-sections').click()
            page.wait_for_timeout(150)
            assert page.evaluate(ERROR, 'new') < 3
        page.locator('#toggle-sections').click()
        page.wait_for_timeout(150)
        assert page.evaluate(ERROR, 'new') < 3
        page.locator('#close-sections').click()
        page.wait_for_timeout(150)
        assert page.evaluate(ERROR, 'new') < 3
    assert not errors


def test_compare_panels_keep_independent_positions(long_preview, browser_page):
    mode, path = long_preview
    if mode != 'report':
        pytest.skip('compare mode is report-only')
    page = browser_page
    page.goto(path.as_uri())
    page.locator('#layout-mode').select_option('compare')
    page.locator('#sync-scroll').uncheck()
    page.wait_for_timeout(150)
    for side in ('old', 'new'):
        page.evaluate(CAPTURE, side)
    page.wait_for_timeout(150)
    for side in ('old', 'new'):
        assert page.evaluate(SAVE, side)
    page.set_viewport_size({'width': 1500, 'height': 900})
    page.wait_for_timeout(150)
    for side in ('old', 'new'):
        assert page.evaluate(ERROR, side) < 3
