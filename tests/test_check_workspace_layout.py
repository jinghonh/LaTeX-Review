"""Real generated readers: viewport reservation, normal pointer clicks and keyboard access."""
import os

import pytest
from playwright.sync_api import sync_playwright

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.live import build


@pytest.fixture(scope='module', params=['diff', 'live'])
def publication(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('workspace-layout-' + request.param)
    paragraphs = '\n\n'.join(
        f'Observation {index} describes the measured response and its supporting experimental evidence. '
        'The original reading and all review navigation remain available during inspection.'
        for index in range(40))
    for side in ('old', 'new'):
        directory = root / side
        directory.mkdir()
        (directory / 'main.tex').write_text(
            '\\documentclass{article}\n\\begin{document}\n\\section{Introduction}\n'
            + ('Old wording.' if side == 'old' else 'Updated wording.')
            + ' See \\ref{missing}.\n\n' + paragraphs
            + '\n\\section{Results}\nFinal observations.\n\\end{document}\n', encoding='utf-8')
    with resolve_sources(entry='main.tex', old_dir=root / 'old', new_dir=root / 'new') as pair:
        if request.param == 'diff':
            old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
            result = write_report(old, new, compare_projects(old, new), root / 'report', pdf_converter='', single_file=True)
            return request.param, result.html
        build(pair.new, root / 'live')
        return request.param, root / 'live' / 'report.html'


def settle(page):
    # Let the existing character-position reader finish its own layout callbacks.
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')


def reserved(page):
    page.wait_for_function("""() => {
      const root = document.getElementById('review-workspace');
      const nav = root.querySelector('[role=tablist]');
      return parseFloat(getComputedStyle(root).getPropertyValue('--workspace-nav-height')) === Math.ceil(nav.getBoundingClientRect().height);
    }""")
    return page.locator('[role=tablist]').bounding_box()['height']


@pytest.mark.parametrize('width', [1440, 824, 390])
def test_generated_reader_navigation_and_overlay_controls_are_reachable(publication, width):
    mode, html = publication
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        page.set_default_timeout(5000)
        page.route('**/*', lambda route: route.abort() if route.request.url.startswith('https://') else route.continue_())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(html.as_uri())
        page.wait_for_function('window.reviewWorkspace')
        settle(page)
        height = reserved(page)
        if width == 390:
            assert page.locator('#workspace-tab-reading').bounding_box()['y'] < page.locator('#workspace-tab-coverage').bounding_box()['y']
        if page.locator('#sections-side').is_hidden():
            page.locator('#toggle-sections').click()
        assert page.locator('#sections-side').bounding_box()['y'] >= height
        # This is the real capture failure: an open chapter directory must not
        # intercept the first tabs. No forced clicks or event dispatch are used.
        page.get_by_role('tab', name='问题检查', exact=True).click()
        page.locator('.workspace-findings > li > button').first.click()
        assert page.locator('#workspace-evidence').bounding_box()['y'] >= height
        page.get_by_role('button', name='关闭证据', exact=True).click()
        page.get_by_role('tab', name='阅读与变更', exact=True).click()
        page.locator('#close-sections').click()
        assert page.locator('#sections-side').is_hidden()
        if mode == 'diff':
            page.locator('#toggle-changes').click()
            assert page.locator('#changes-side').bounding_box()['y'] >= height
            page.locator('#close-changes').click()
            assert page.locator('#changes-side').is_hidden()
        page.locator('#annotation-overview > summary').click()
        assert page.locator('#annotation-overview').bounding_box()['y'] >= height
        page.get_by_role('tab', name='概念与符号', exact=True).click()
        page.get_by_role('tab', name='阅读与变更', exact=True).click()
        page.locator('#annotation-overview > summary').click()
        settle(page)
        page.evaluate('scrollTo(0, 333)')
        settle(page)
        assert page.evaluate('scrollY') == 333
        assert page.locator('.report-controls').bounding_box()['y'] >= height
        page.get_by_role('tab', name='问题检查', exact=True).click()
        page.get_by_role('tab', name='阅读与变更', exact=True).click()
        settle(page)
        assert page.evaluate('scrollY') == 333
        # Keyboard navigation remains visible and usable while the document is scrolled.
        page.locator('#workspace-tab-reading').focus()
        page.keyboard.press('End')
        assert page.locator('#workspace-tab-coverage').get_attribute('aria-selected') == 'true'
        page.keyboard.press('Home')
        assert page.locator('#workspace-tab-reading').get_attribute('aria-selected') == 'true'
        # ResizeObserver must recompute wrapping, not retain a desktop magic height.
        page.set_viewport_size({'width': 280, 'height': 1000})
        settle(page)
        narrow_height = reserved(page)
        assert narrow_height >= height
        page.locator('#toggle-sections').click()
        assert page.locator('#sections-side').bounding_box()['y'] >= narrow_height
        page.get_by_role('tab', name='问题检查', exact=True).click()
        page.get_by_role('tab', name='阅读与变更', exact=True).click()
        page.locator('#close-sections').click()
        assert not errors
        browser.close()
