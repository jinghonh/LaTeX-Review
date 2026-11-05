"""Resize keeps the same text line in live, report and independently scrolled panels."""
import json
import time

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


# Evaluated only after the original error sample fails, in a separate browser RPC.
# This describes later state, not an atomic snapshot of the failing measurement.
FAILURE_CONTEXT = '''(side) => {
  const p = document.querySelector('.preview-side[data-side="' + side + '"]');
  const r = p.getBoundingClientRect(), bar = document.querySelector('.report-controls');
  const overflowY = getComputedStyle(p).overflowY;
  const independent = /auto|scroll/.test(overflowY) && p.scrollHeight > p.clientHeight;
  const top = independent ? Math.max(0, r.top + p.clientTop) : (bar && getComputedStyle(bar).position === 'sticky' ? Math.max(0, bar.getBoundingClientRect().bottom) : 0);
  const bottom = independent ? Math.min(innerHeight, r.bottom) : innerHeight;
  const anchor = testAnchors[side], position = window.reviewReadingPosition;
  const rect = value => ({top: value.top, bottom: value.bottom, left: value.left,
    right: value.right, width: value.width, height: value.height});
  let isResizing = null;
  try { if (typeof position?.isResizing === 'function') isResizing = position.isResizing(); }
  catch (error) { isResizing = {error: String(error)}; }
  return {sampledAt: performance.now(), readyState: document.readyState,
    viewport: {width: innerWidth, height: innerHeight},
    publishedReadingPosition: {present: !!position, type: typeof position,
      keys: position ? Object.keys(position) : [], isResizing},
    currentScroller: independent ? 'paper' : 'window', overflowY,
    windowY: scrollY, paperScrollTop: p.scrollTop,
    paperClientWidth: p.clientWidth, paperClientHeight: p.clientHeight,
    paperScrollHeight: p.scrollHeight, paperRect: rect(r),
    rangeRect: rect(anchor.range.getBoundingClientRect()),
    rangeConnected: anchor.range.startContainer.isConnected,
    savedCharacter: anchor.range.toString(), rangeStartOffset: anchor.range.startOffset,
    expectedRatio: anchor.ratio, viewTop: top, viewBottom: bottom,
    expectedTop: top + anchor.ratio * (bottom - top)};
}'''


def _position_failure_json(page, mode, side, action, error):
    # Failure-only: this host timestamp follows the original ERROR evaluation.
    # sampledAt in the later browser context uses a DIFFERENT clock.
    context_requested_host_ns = time.monotonic_ns()
    detail = {
        'mode': mode, 'side': side, 'action': action, 'original_error_px': error,
        'original_error_timing': 'original ERROR evaluation; browser timestamp not recorded',
        'context_timing': 'separate evaluation AFTER the original failed error sample',
        'context_requested_host_monotonic_ns': context_requested_host_ns,
        'after_error_context': page.evaluate(FAILURE_CONTEXT, side),
    }
    serialized = json.dumps(detail, ensure_ascii=False, indent=2)
    print('READING_POSITION_FAILURE_JSON_BEGIN\n' + serialized
          + '\nREADING_POSITION_FAILURE_JSON_END', flush=True)
    return serialized  # String bypasses pytest's truncated safe repr of a dict.


def assert_position_preserved(page, mode, side, action):
    error = page.evaluate(ERROR, side)
    assert error < 3, _position_failure_json(page, mode, side, action, error)


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
        assert_position_preserved(page, mode, 'new', ('viewport', width, height))
        # Keep the same reading character through the next resize.
    if mode != 'standalone':
        if page.locator('#sections-side').is_visible():
            page.locator('#close-sections').click()
            page.wait_for_timeout(150)
            assert_position_preserved(page, mode, 'new', 'close initially visible sections')
        page.locator('#toggle-sections').click()
        page.wait_for_timeout(150)
        assert_position_preserved(page, mode, 'new', 'toggle sections')
        page.locator('#close-sections').click()
        page.wait_for_timeout(150)
        assert_position_preserved(page, mode, 'new', 'close sections')
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
        assert_position_preserved(page, mode, side, ('compare viewport', 1500, 900))
