from pathlib import Path
import os

from latex_review.contract import ComparisonSource
from latex_review.live import build, fingerprint
from latex_review.sources import ProjectSource


def test_current_preview_updates_without_old_version(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Hello current.\end{document}')
    output = tmp_path / '.latex-review' / 'live'
    output.mkdir(parents=True)
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    build(source, output)
    html = (output / 'report.html').read_text()
    assert 'Hello current.' in html
    assert '当前论文' in html
    import re
    assert '修改前' not in re.sub(r'<script\b.*?</script>', '', html, flags=re.S)
    assert '<section class="preview-side" aria-label="修改前"' not in html
    assert 'live-status.json' in html
    entry.write_text(entry.read_text().replace('Hello current.', 'Updated current.'))
    build(source, output, 2)
    html = (output / 'report.html').read_text()
    assert 'Updated current.' in html
    assert 'Hello current.' not in html
    assert 'let revision = 2;' in html
    assert not (output / 'diff.json').exists()


def test_fingerprint_tracks_dependencies_and_ignores_output(tmp_path):
    output = tmp_path / 'preview'
    output.mkdir()
    (tmp_path / 'main.tex').write_text('main')
    initial = fingerprint(tmp_path, output)
    (output / 'report.html').write_text('generated')
    assert fingerprint(tmp_path, output) == initial
    chapter = tmp_path / 'chapter.tex'
    chapter.write_text('chapter')
    added = fingerprint(tmp_path, output)
    assert added != initial
    chapter.write_text('changed')
    assert fingerprint(tmp_path, output) != added
    chapter.unlink()
    assert fingerprint(tmp_path, output) == initial


def test_live_navigation_and_refresh_preserve_reading_state(tmp_path):
    """Exercise real reloads, section deletion, and narrow-screen controls."""
    import json
    import os
    from functools import partial
    from threading import Thread

    import pytest

    from latex_review.serve import ReportHandler, ReportServer

    api = pytest.importorskip('playwright.sync_api')
    entry = tmp_path / 'main.tex'
    paragraphs = '\n\n'.join(f'Paragraph {index}. Reading text for the current paper.' for index in range(25))
    original = ('\\documentclass{article}\\begin{document}\\section{First}\n' + paragraphs
                + '\n\\section{Second}\nTarget passage for search.\n\n' + paragraphs
                + '\n\\section{Third}\nFinal passage.\\end{document}')
    entry.write_text(original)
    output = tmp_path / 'preview'
    output.mkdir()
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    build(source, output)
    (output / 'live-status.json').write_text(json.dumps({'revision': 1, 'message': 'Ready'}))
    server = ReportServer(('127.0.0.1', 0), partial(ReportHandler, directory=str(output)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with api.sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
            except api.Error as exc:
                pytest.skip(f'浏览器运行环境不可用：{exc}')
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('https://**', lambda route: route.abort())
                reload_diagnostics = []

                def reading_state(index):
                    return page.evaluate('''index => {
                        const rect = node => node ? {top: node.getBoundingClientRect().top,
                            bottom: node.getBoundingClientRect().bottom} : null;
                        return {ready: document.readyState, scroll: scrollY, width: innerWidth,
                            resizing: window.reviewReadingPosition?.isResizing(), fonts: document.fonts.status,
                            toolbar: rect(document.querySelector('.report-controls')),
                            navigation: rect(document.querySelector('#review-workspace > [role="tablist"]')),
                            inset: document.querySelector('#review-workspace')?.style.getPropertyValue('--workspace-heading-inset'),
                            saved: sessionStorage.getItem('latex-live:' + location.pathname),
                            sections: [...document.querySelectorAll('.node-section')].map((node, position) => ({
                                id: node.id, text: node.querySelector('h2')?.textContent,
                                top: node.getBoundingClientRect().top, target: position === index}))};
                    }''', index)

                def wait_for_reading_layout():
                    # load does not drain ResizeObserver/requestAnimationFrame restoration.
                    page.wait_for_load_state('load')
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    page.wait_for_function('() => !window.reviewReadingPosition?.isResizing()')

                def assert_restored(index, revision):
                    state = {'revision': revision, 'before': reading_state(index)}
                    reload_diagnostics.append(state)
                    try:
                        wait_for_reading_layout()
                    except api.TimeoutError as error:
                        raise AssertionError({'layout_settlement_timeout': True, 'reloads': reload_diagnostics}) from error
                    finally:
                        state['settled'] = reading_state(index)
                    assert abs(page.locator('.node-section').nth(index).bounding_box()['y'] - offset) < 3, {
                        'expected_offset': offset, 'reloads': reload_diagnostics}

                page.goto(f'http://127.0.0.1:{server.server_port}/report.html')
                paper = page.locator('.preview-side')
                assert paper.count() == 1
                assert page.evaluate('getComputedStyle(document.querySelector(".preview-side")).fontFamily.includes("Iowan")')
                page.set_viewport_size({'width': 2560, 'height': 900})
                for mode in ('original', 'bilingual'):
                    page.locator('#translation-mode').select_option(mode)
                    for sections, annotations in ((True, False), (False, True), (True, True), (False, False)):
                        if page.locator('#sections-side').is_visible() != sections:
                            page.locator('#toggle-sections' if sections else '#close-sections').click()
                        page.locator('.annotation-overview').evaluate('(node, open) => node.open = open', annotations)
                        page.wait_for_function('(open) => document.body.classList.contains("annotation-panel-open") === open', arg=annotations)
                        gaps = page.evaluate('''() => {
                            const paper = document.querySelector('.preview-side').getBoundingClientRect();
                            const sections = document.body.classList.contains('sections-open');
                            const annotations = document.body.classList.contains('annotation-panel-open');
                            const left = sections ? document.querySelector('#sections-side').getBoundingClientRect().right : 0;
                            const right = annotations ? document.querySelector('.annotation-overview').getBoundingClientRect().left : innerWidth;
                            return [paper.left - left, right - paper.right];
                        }''')
                        assert abs(gaps[0] - gaps[1]) < 1
                page.locator('#translation-mode').select_option('bilingual')
                page.locator('#toggle-sections').click()
                page.set_viewport_size({'width': 1280, 'height': 900})
                page.locator('#live-navigation>summary').click()
                page.locator('#live-search').fill('Target passage')
                assert page.locator('#live-results button').count() == 1
                page.locator('#live-results button').click()
                wait_for_reading_layout()
                assert page.evaluate('scrollY') > 0
                second = page.locator('.node-section').nth(1)
                offset = second.bounding_box()['y']
                entry.write_text(original.replace('Target passage for search.', 'Target passage updated.'))
                build(source, output, 2)
                (output / 'live-status.json').write_text(json.dumps({'revision': 2, 'message': 'Updated'}))
                page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Target passage updated.")')
                assert_restored(1, 2)
                assert page.locator('#live-search').input_value() == 'Target passage'
                assert page.locator('#live-navigation').get_attribute('open') is not None
                # The saved section name survives earlier insertions that change node IDs.
                entry.write_text(entry.read_text().replace('\\section{First}', '\\section{Inserted}\nNew material.\n\\section{First}'))
                build(source, output, 3)
                (output / 'live-status.json').write_text(json.dumps({'revision': 3, 'message': 'Inserted'}))
                page.wait_for_function('document.querySelectorAll(".node-section").length === 4')
                assert_restored(2, 3)
                # If the current section is deleted, fall back to the nearest remaining section.
                entry.write_text(entry.read_text().replace('\\section{Second}', '\\section{Replacement}'))
                build(source, output, 4)
                (output / 'live-status.json').write_text(json.dumps({'revision': 4, 'message': 'Replaced'}))
                page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Replacement")')
                assert_restored(2, 4)
                for width in (1280, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    wait_for_reading_layout()
                    page.evaluate('window.scrollTo(0,0)')
                    if not page.locator('#sections-side').is_visible():
                        page.locator('#toggle-sections').click()
                    page.get_by_role('button', name='3 Replacement', exact=True).click()
                    assert page.evaluate('scrollY') > 0
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('.live-controls a[href="#live-diagnostics"]').click()
                assert page.locator('#live-diagnostics').get_attribute('open') is not None
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_live_uses_available_pdf_converter(tmp_path, monkeypatch):
    from test_issue42 import _two_page_pdf
    from latex_review import compilation

    project = tmp_path / 'paper'
    project.mkdir()
    (project / 'figure.pdf').write_bytes(_two_page_pdf())
    (project / 'main.tex').write_text(
        r'\documentclass{article}\usepackage{graphicx}\begin{document}'
        r'\begin{figure}\includegraphics{figure.pdf}\end{figure}\end{document}')
    output = project / '.latex-review' / 'live'
    output.mkdir(parents=True)
    # A real subprocess exercises the converter call without requiring Poppler.
    import sys
    converter = tmp_path / 'pdftoppm'
    from latex_review.visual import _png
    png = _png(1, 1, b'\xff')
    converter.write_text(f'#!{Path(sys.executable).resolve()}\n'
                         "import pathlib,sys\n"
                         "if sys.argv[1:] == ['-v']: print('fixture converter 1')\n"
                         f"else: pathlib.Path(sys.argv[-1]+'.png').write_bytes({png!r})\n")
    converter.chmod(0o755)
    monkeypatch.setattr(compilation, '_program', lambda name, path, forbidden: str(converter) if name == 'pdftoppm' else None)
    source = ProjectSource('new', project, 'main.tex', ComparisonSource('directory', str(project)))
    messages = build(source, output)
    assert not any('PDF 图预览不可用' in message for message in messages)
    assert (output / 'previews/new/figure.pdf.page-1.png').read_bytes() == png
    assert 'alt="PDF 图 figure.pdf 的第 1 页预览"' in (output / 'report.html').read_text()
    monkeypatch.setattr(compilation, '_program', lambda *args: None)
    messages = build(source, output, 2)
    assert any('缺少可用的 PDF 转换工具' in message for message in messages)


def test_live_section_tree_restores_expansion(tmp_path):
    import pytest
    api = pytest.importorskip('playwright.sync_api')
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}\section{First}\subsection{Second}\subsubsection{Deep}Text.\end{document}')
    output = tmp_path / 'preview'
    output.mkdir()
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    build(source, output)
    with api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.route('https://**', lambda route: route.abort())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto((output / 'report.html').as_uri())
        api.expect(page.locator('#sections-side')).to_be_visible()
        assert page.locator('.section-count').count() == 0
        api.expect(page.get_by_role('button', name='1.1.1 Deep', exact=True)).to_be_hidden()
        page.locator('#section-tree details > summary').nth(1).click(position={'x': 3, 'y': 10})
        api.expect(page.get_by_role('button', name='1.1.1 Deep', exact=True)).to_be_visible()
        page.reload()
        api.expect(page.get_by_role('button', name='1.1.1 Deep', exact=True)).to_be_visible()
        page.locator('#close-sections').click()
        page.reload()
        api.expect(page.locator('#sections-side')).to_be_hidden()
        page.set_viewport_size({'width': 390, 'height': 900})
        page.locator('#toggle-sections').click()
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        page.wait_for_function('() => !window.reviewReadingPosition?.isResizing()')
        page.get_by_role('button', name='1.1 Second', exact=True).click()
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        api.expect(page.locator('#sections-side')).to_be_hidden()
        api.expect(page.get_by_role('button', name='1.1 Second', exact=True, include_hidden=True)).to_have_attribute('aria-current', 'location')
        assert page.evaluate('scrollY >= document.documentElement.scrollHeight - innerHeight - 1')
        heading = page.locator('.preview-side[data-side="new"] h3').filter(has_text='Second')
        assert heading.evaluate('node => node.getBoundingClientRect().top >= document.querySelector(".report-controls").getBoundingClientRect().bottom && node.getBoundingClientRect().bottom <= innerHeight')
        # A subsequent native jump must replace the bottom-clamped selection.
        page.locator('#toggle-sections').click()
        page.get_by_role('button', name='1 First', exact=True).click()
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        api.expect(page.get_by_role('button', name='1 First', exact=True, include_hidden=True)).to_have_attribute('aria-current', 'location')
        heading = page.locator('.preview-side[data-side="new"] .node-section > h2').first
        assert heading.evaluate('node => node.getBoundingClientRect().top >= document.querySelector(".report-controls").getBoundingClientRect().bottom && node.getBoundingClientRect().bottom <= innerHeight')
        assert not errors
        browser.close()


def test_saved_check_notifier_coalesces_cancels_and_honors_effective_profile(tmp_path, monkeypatch):
    from threading import Event
    import latex_review.live as live
    from latex_review.project_profile import directory, settings
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'external-cache'))
    root = tmp_path / 'paper'
    root.mkdir()
    (root / 'main.tex').write_text('Paper')
    profile = directory(root) / 'project.toml'
    profile.parent.mkdir(parents=True)
    profile.write_text('[checks]\ndebounce_ms=1350\non_save=false\n')
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    timers, sent = [], []
    class FakeTimer:
        def __init__(self, delay, callback, args):
            self.delay, self.callback, self.args = delay, callback, args
            self.cancelled = self.started = False
            timers.append(self)
        def start(self):
            self.started = True
        def cancel(self):
            self.cancelled = True
        def fire(self):
            self.callback(*self.args)
    monkeypatch.setattr(live, 'Timer', FakeTimer)
    monkeypatch.setattr(live, '_check_saved_recipe', lambda recipe, config: sent.append((recipe, config)))
    stop = Event()
    notifier = live._SavedCheckNotifier({'paper_id': 'owned'}, stop)
    notifier.schedule(settings(source))
    assert timers == []  # External profile disables default on-save checks.
    from dataclasses import replace
    source = replace(source, configuration={'checks': {'on_save': True}})
    effective = settings(source)
    notifier.schedule(effective)
    assert timers[-1].delay == 1.35 and timers[-1].started and timers[-1].daemon
    first = timers[-1]
    notifier.schedule(effective)
    assert first.cancelled
    first.fire()  # Already-fired cancelled timers also cannot send stale work.
    assert sent == []
    timers[-1].fire()
    assert sent == [({'paper_id': 'owned'}, effective)]
    notifier.schedule({})
    assert timers[-1].delay == .8
    last = timers[-1]
    notifier.schedule({'checks': {'enabled': False}})
    assert last.cancelled
    last.fire()
    assert len(sent) == 1
    notifier.schedule({})
    pending = timers[-1]
    notifier.cancel_pending()  # A failed next rebuild must not send old success.
    pending.fire()
    assert pending.cancelled and len(sent) == 1
    notifier.schedule({})  # Cancel is reusable, unlike permanent worker close.
    stopped = timers[-1]
    stop.set()
    stopped.fire()
    assert len(sent) == 1
    notifier.close()
    assert stopped.cancelled
    notifier.schedule({})
    assert timers[-1] is stopped
