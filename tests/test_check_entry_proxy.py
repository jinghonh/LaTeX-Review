"""Actual browser -> private entry proxy -> worker -> coordinator integration."""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from latex_review import local_client
from latex_review.live import prepare_request


def test_browser_run_preserves_frozen_content_header_through_real_entry_proxy(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Current paper \ref{missing}.\end{document}')
    recipe = prepare_request(entry, root=root, output=tmp_path / 'preview')
    result = local_client.ensure(recipe, timeout=40)
    sidecar = json.loads((Path(recipe['output']) / 'checks-page.json').read_text())
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        try:
            page = browser.new_page()
            page.set_default_timeout(20000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(result['url'])
            workspace = page.frame_locator('iframe')
            workspace.get_by_role('tab', name='问题检查', exact=True).click()
            run = workspace.get_by_role('button', name='检查当前论文', exact=True)
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/api/checks/runs')) as pending:
                run.click()
            accepted = pending.value
            assert accepted.status == 202, accepted.text()
            task = accepted.json()
            assert accepted.request.headers['x-latex-review-checks-content-id'] == sidecar['content_id']
            assert accepted.request.headers['x-latex-review-page-version'] == sidecar['page_version']
            assert '/papers/' + recipe['paper_id'] + '/live/worker/' in accepted.url
            frame = next(frame for frame in page.frames if '/live/worker/' in frame.url)
            assert frame.evaluate('window.reviewWorkspace.report.input.content_id') == sidecar['content_id']
            frame.wait_for_function('runId => window.reviewWorkspace.capability.current_run?.run_id === runId && ["completed","partial"].includes(window.reviewWorkspace.capability.current_run?.state)', arg=task['run_id'])
            assert local_client.checks_request(recipe['paper_id'], 'runs/' + task['run_id'])['state'] in {'completed', 'partial'}
            # The entry must carry, not invent, this binding. A client omitting
            # the header is still refused by the real worker behind the proxy.
            denied = frame.evaluate('''async () => {
              const cap = window.reviewWorkspace.capability;
              const response = await fetch('api/checks/runs', {method:'POST', headers:{
                'Content-Type':'application/json', 'X-LaTeX-Review-Token':cap.token,
                'X-LaTeX-Review-Paper-ID':cap.paper_id,
                'X-LaTeX-Review-Page-Version':cap.page_version
              }, body:'{}'});
              return {status:response.status, value:await response.json()};
            }''')
            assert denied['status'] == 409 and denied['value']['code'] == 'page_changed'
            assert local_client.checks_request(recipe['paper_id'], 'status')['current_run']['run_id'] == task['run_id']
            assert errors == []
            assert entry.read_text() == r'\documentclass{article}\begin{document}Current paper \ref{missing}.\end{document}'
        finally:
            browser.close()
