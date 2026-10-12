"""Shared reading installation and search through both real page modes."""
import json
import re

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.contract import ComparisonSource
from latex_review.live import build
from latex_review.sources import ProjectSource
from test_report_reading import browser_page


@pytest.fixture(params=['report', 'live'])
def reading_page(request, tmp_path):
    for side in ('old', 'new'):
        directory = tmp_path / side
        directory.mkdir()
        (directory / 'main.tex').write_text(r'''\documentclass{article}
\begin{document}
\section{Search heading}
Parent unique text with $x+1$ and \cite{hiddenCitationKey}.

Another paragraph.
\begin{thebibliography}{9}\bibitem{hiddenCitationKey}Citation description.\end{thebibliography}
\end{document}''', encoding='utf-8')
    output = tmp_path / 'report'
    if request.param == 'report':
        with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
            old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
            write_report(old, new, compare_projects(old, new), output, pdf_converter='')
    else:
        output.mkdir()
        build(ProjectSource('new', tmp_path / 'new', 'main.tex',
                            ComparisonSource('directory', str(tmp_path / 'new'))), output)
    return request.param, output / 'report.html'


def test_shared_installation_has_one_control_and_safe_data(reading_page):
    mode, path = reading_page
    content = path.read_text()
    for identifier in ('reader-data', 'translation-units', 'translation-results', 'toggle-sections',
                       'sections-side', 'close-sections', 'section-tree', 'translation-mode',
                       'translate-all', 'translation-cancel', 'translation-export', 'translation-status'):
        assert len(re.findall(r'\bid="' + identifier + r'"', content)) == 1
    nodes = json.loads(re.search(r'id="reader-data">(.*?)</script>', content, re.S)[1])['nodes']
    assert all('citations' in node for node in nodes)
    units = json.loads(re.search(r'id="translation-units">(.*?)</script>', content, re.S)[1])
    assert all('sentences' not in unit for unit in units)


def test_both_modes_search_owned_text_citations_and_dynamic_translations(reading_page, browser_page):
    mode, path = reading_page
    page = browser_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(path.as_uri())
    field = page.locator('#live-search' if mode == 'live' else '#report-search')
    if mode == 'live':
        page.locator('#live-navigation').evaluate('(node) => node.open = true')
    else:
        page.locator('#reader-tools').evaluate('(node) => node.open = true')
    results = page.locator('#live-results li' if mode == 'live' else '#search-results li')
    sides = 1 if mode == 'live' else 2
    field.fill('Parent unique')
    assert results.count() == sides
    # The hidden citation key is searchable in the owning paragraph.
    field.fill('hiddenCitationKey')
    assert page.evaluate('''() => reviewSearch.find('hiddenCitationKey').some(
      item => item.type === 'paragraph')''')
    field.fill('中文独有词')
    assert results.count() == 0
    # Real DOM updates use the same event emitted by translation installation.
    page.evaluate('''() => {
      const target = document.querySelector('.preview-side[data-side="new"] .node-paragraph');
      const block = document.createElement('div'); block.className = 'translation-block';
      block.innerHTML = '<span class="translation-label">LabelOnly</span>' +
        '<span>中文独有词</span><button>TranslatedControlOnly</button>'; target.append(block);
      document.dispatchEvent(new Event('review-translations-updated'));
    }''')
    assert results.count() == 1
    assert '中文译文' in results.first.inner_text()
    assert page.evaluate("reviewSearch.find('LabelOnly').length") == 0
    assert page.evaluate("reviewSearch.find('TranslatedControlOnly').length") == 0
    results.first.locator('button').click()
    assert page.locator('.preview-side[data-side="new"] .node-paragraph').first.is_visible()
    page.evaluate('''() => {
      document.querySelectorAll('.translation-block').forEach(block => block.remove());
      document.dispatchEvent(new Event('review-translations-updated'));
    }''')
    assert results.count() == 0
    field.fill('停止翻译')
    assert results.count() == 0
    assert not errors


def test_search_excludes_nested_nodes_and_controls_at_installation(reading_page, browser_page):
    _, path = reading_page
    page = browser_page
    page.goto(path.as_uri())
    from importlib.resources import files
    search = files('latex_review').joinpath('report_search.js').read_text()
    page.evaluate('''() => {
      const target = document.querySelector('.preview-side[data-side="new"] .node-paragraph');
      target.innerHTML = '<span class="annotation-mark">OwnedOriginal</span>' +
        '<button class="change-trigger">TriggerOriginal</button>' +
        '<span class="review-node">NestedOnly</span>' +
        '<div class="annotation-card">AnnotationOnly</div>' +
        '<div class="translation-item-controls">ControlsOnly</div>';
    ''' + search + '}')
    for word in ('OwnedOriginal', 'TriggerOriginal'):
        assert page.evaluate('(q) => reviewSearch.find(q).length', word) == 1
    for word in ('NestedOnly', 'AnnotationOnly', 'ControlsOnly'):
        assert page.evaluate('(q) => reviewSearch.find(q).length', word) == 0


def test_shared_data_cannot_close_json_script(tmp_path):
    from latex_review.reading import build_reading
    directory = tmp_path / 'paper'
    directory.mkdir()
    (directory / 'main.tex').write_text(r'\begin{document}Safe text.\end{document}')
    source = ProjectSource('new', directory, 'main.tex', ComparisonSource('directory', str(directory)))
    project = parse_project(source.expand())
    payload = '</script><script>alert(1)</script>'
    reading = build_reading(project, [{'id': payload, 'sentences': ['private source']}])
    manifest = re.search(r'id="translation-units">(.*?)</script>', reading.scripts, re.S)[1]
    assert '<' not in manifest
    assert json.loads(manifest) == [{'id': payload}]
