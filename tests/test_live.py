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
                assert page.evaluate('scrollY') > 0
                second = page.locator('.node-section').nth(1)
                offset = second.bounding_box()['y']
                entry.write_text(original.replace('Target passage for search.', 'Target passage updated.'))
                build(source, output, 2)
                (output / 'live-status.json').write_text(json.dumps({'revision': 2, 'message': 'Updated'}))
                page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Target passage updated.")')
                page.wait_for_load_state('load')
                assert page.locator('#live-search').input_value() == 'Target passage'
                assert page.locator('#live-navigation').get_attribute('open') is not None
                assert abs(page.locator('.node-section').nth(1).bounding_box()['y'] - offset) < 3
                # The saved section name survives earlier insertions that change node IDs.
                entry.write_text(entry.read_text().replace('\\section{First}', '\\section{Inserted}\nNew material.\n\\section{First}'))
                build(source, output, 3)
                (output / 'live-status.json').write_text(json.dumps({'revision': 3, 'message': 'Inserted'}))
                page.wait_for_function('document.querySelectorAll(".node-section").length === 4')
                page.wait_for_load_state('load')
                assert abs(page.locator('.node-section').nth(2).bounding_box()['y'] - offset) < 3
                # If the current section is deleted, fall back to the nearest remaining section.
                entry.write_text(entry.read_text().replace('\\section{Second}', '\\section{Replacement}'))
                build(source, output, 4)
                (output / 'live-status.json').write_text(json.dumps({'revision': 4, 'message': 'Replaced'}))
                page.wait_for_function('document.querySelector(".preview-side").textContent.includes("Replacement")')
                page.wait_for_load_state('load')
                assert abs(page.locator('.node-section').nth(2).bounding_box()['y'] - offset) < 3
                for width in (1280, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.evaluate('window.scrollTo(0,0)')
                    if not page.locator('#sections-side').is_visible():
                        page.locator('#toggle-sections').click()
                    page.get_by_role('button', name='Replacement', exact=True).click()
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
    converter.write_text(f'#!{Path(sys.executable).resolve()}\n'
                         "import pathlib,sys\npathlib.Path(sys.argv[-1]+'.png').write_bytes(b'PNG')\n")
    converter.chmod(0o755)
    monkeypatch.setattr(compilation, '_program', lambda name, path, forbidden: str(converter) if name == 'pdftoppm' else None)
    source = ProjectSource('new', project, 'main.tex', ComparisonSource('directory', str(project)))
    messages = build(source, output)
    assert not any('PDF 图预览不可用' in message for message in messages)
    assert (output / 'previews/new/figure.pdf.page-1.png').read_bytes() == b'PNG'
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
        api.expect(page.get_by_role('button', name='Deep', exact=True)).to_be_hidden()
        page.locator('#section-tree details > summary').nth(1).click(position={'x': 3, 'y': 10})
        api.expect(page.get_by_role('button', name='Deep', exact=True)).to_be_visible()
        page.reload()
        api.expect(page.get_by_role('button', name='Deep', exact=True)).to_be_visible()
        page.locator('#close-sections').click()
        page.reload()
        api.expect(page.locator('#sections-side')).to_be_hidden()
        page.set_viewport_size({'width': 390, 'height': 900})
        page.locator('#toggle-sections').click()
        page.get_by_role('button', name='Second', exact=True).click()
        api.expect(page.locator('#sections-side')).to_be_hidden()
        api.expect(page.get_by_role('button', name='Second', exact=True, include_hidden=True)).to_have_attribute('aria-current', 'location')
        assert not errors
        browser.close()
