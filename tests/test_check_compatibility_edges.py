"""AC09/AC19: genuine source discontinuity and pre-extension report compatibility."""
from hashlib import sha256
import io
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from latex_review.checks import build_check_document, capture_snapshot


ROOT = Path(__file__).parents[1]
BASELINE = 'fe82620af8bbbb028809d59ceeaaad7085427880'


def test_cross_file_paragraph_keeps_exact_fragments_but_downgrades_joined_location(tmp_path):
    main = ('\\documentclass{article}\r\n\\begin{document}\r\n'
            '前文 α😀 \\input{part} 后文 中文。\r\n\\end{document}\r\n')
    included = '跨文件 β🧪\r\n继续同一段'
    (tmp_path / 'main.tex').write_bytes(main.encode('utf-8'))
    (tmp_path / 'part.tex').write_bytes(included.encode('utf-8'))
    snapshot = capture_snapshot('main.tex', root=tmp_path)
    document = build_check_document(snapshot)
    paragraph = next(node for node in snapshot.parsed.nodes
                     if node.review.type == 'paragraph' and '前文' in node.review.raw_latex)
    assert paragraph.review.raw_latex == '前文 α😀 ' + included + ' 后文 中文。'
    location = paragraph.review.source
    assert location.file is None and location.start_line is None and location.end_line is None
    assert 0 <= location.confidence < 1
    assert location.uncertainty_reason
    assert any(diagnostic.code == 'low_confidence_source' for diagnostic in paragraph.diagnostics)
    assert [mapped.origin.file for mapped in paragraph.origins] == ['main.tex', 'part.tex', 'main.tex']
    # Returning to the same main file does not make the removed \\input interval
    # contiguous: no invented main-file span may cover this paragraph.
    assert paragraph.origins[0].origin.end < paragraph.origins[2].origin.start
    assert paragraph.origins[0].origin.include_instance == paragraph.origins[2].origin.include_instance
    fragments = document.evidence(paragraph.expanded_start, paragraph.expanded_end)
    assert [fragment['file'] for fragment in fragments] == ['main.tex', 'part.tex', 'main.tex']
    assert len({fragment['include_instance'] for fragment in fragments}) == 2
    assert ''.join(fragment['quote'] for fragment in fragments) == paragraph.review.raw_latex
    for fragment in fragments:
        raw = snapshot.files[fragment['file']]
        text = raw.decode('utf-8')
        assert fragment['reliability'] == 'exact' and fragment['reason'] is None
        assert fragment['quote'] == text[fragment['start']:fragment['end']]
        assert fragment['end'] - fragment['start'] == len(fragment['quote'])
        assert fragment['file_sha256'] == sha256(raw).hexdigest()
    assert fragments[1]['quote'] == included
    assert (fragments[1]['start_line'], fragments[1]['start_column']) == (1, 1)
    assert (fragments[1]['end_line'], fragments[1]['end_column']) == (2, len('继续同一段') + 1)
    assert fragments[0]['start_column'] == 1
    unit = next(unit for unit in document.units if unit['node_id'] == paragraph.review.id)
    assert unit['representation'] == 'raw_latex'
    assert unit['evidence'] == fragments
    assert len(unit['evidence']) == 3  # no fabricated single exact aggregate span
    # A truly contiguous Unicode range retains its exact source coordinates.
    left = snapshot.parsed.expanded.text.index('跨文件')
    exact = document.evidence(left, left + len('跨文件 β🧪'))
    assert len(exact) == 1 and exact[0]['reliability'] == 'exact'
    assert exact[0]['quote'] == '跨文件 β🧪'
    assert exact[0]['end'] - exact[0]['start'] == len('跨文件 β🧪')
    assert (tmp_path / 'main.tex').read_bytes() == main.encode('utf-8')
    assert (tmp_path / 'part.tex').read_bytes() == included.encode('utf-8')


@pytest.fixture(scope='module')
def pre_extension_report(tmp_path_factory):
    """Generate real old HTML using ONLY the pinned baseline package, offline."""
    temporary = tmp_path_factory.mktemp('pre-extension-report')
    frozen = temporary / 'frozen'
    frozen.mkdir()
    available = subprocess.run(['git', 'cat-file', '-e', BASELINE + '^{commit}'], cwd=ROOT, capture_output=True)
    if available.returncode:
        pytest.skip('Pinned pre-extension git object unavailable in this checkout; no network fetch attempted')
    archive = subprocess.run(['git', 'archive', '--format=tar', BASELINE, 'src'],
                             cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        # Archive paths are pinned git data. Still reject traversal/symlinks,
        # and do not depend on tar extraction filter support in Python 3.11.
        for member in bundle.getmembers():
            if not member.isfile():
                continue
            destination = (frozen / member.name).resolve()
            assert destination.is_relative_to(frozen.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.extractfile(member).read())
    for side, paragraph in [('old', 'Legacy old α😀 prose.'), ('new', 'Legacy new α😀 prose.')]:
        directory = temporary / side
        directory.mkdir()
        (directory / 'main.tex').write_text(r'\documentclass{article}\begin{document}\section{Legacy section}' + paragraph + r'\end{document}', encoding='utf-8')
    output = temporary / 'report'
    program = '''
from pathlib import Path
import sys
import latex_review.report as report_module
from latex_review.contract import ComparisonSource
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project
from latex_review.comparison import compare_projects
from latex_review.report import write_report
base = Path(sys.argv[1])
assert Path(report_module.__file__).resolve().is_relative_to(base / "frozen" / "src")
projects = []
for side in ("old", "new"):
    root = base / side
    projects.append(parse_project(ProjectSource(side, root, "main.tex", ComparisonSource("directory", str(root))).expand()))
write_report(*projects, compare_projects(*projects), base / "report")
'''
    result = subprocess.run([sys.executable, '-c', program, str(temporary)], cwd=temporary,
                            env={**os.environ, 'PYTHONPATH': str(frozen / 'src')},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    html = (output / 'report.html').read_text(encoding='utf-8')
    assert 'id="checks-data"' not in html and "id='checks-data'" not in html
    assert 'review-workspace' not in html
    assert not (output / 'checks.json').exists()
    return output


@pytest.mark.parametrize('inject_current_workspace', [False, True], ids=['original-baseline-client', 'current-client-without-checks-data'])
def test_real_pre_extension_report_remains_readable_without_writes_or_ai(pre_extension_report, inject_current_workspace):
    directory = pre_extension_report
    before = {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
              for path in directory.rglob('*') if path.is_file()}
    requests, errors, blocked_external = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            requests.append({'method': request.method, 'path': url.path, 'host': url.hostname})
            if url.hostname != 'localhost':
                blocked_external.append(request.url)
                route.abort()  # Baseline CDN MathJax is deliberately unavailable.
                return
            relative = unquote(url.path).removeprefix('/legacy/')
            target = (directory / relative).resolve()
            if target.is_relative_to(directory.resolve()) and target.is_file():
                content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
                route.fulfill(body=target.read_bytes(), content_type=content_type)
            else:
                route.fulfill(status=404, body=json.dumps({'error': 'static legacy report, no backend'}), content_type='application/json')

        page.route('**/*', serve)
        page.goto('http://localhost/legacy/report.html', wait_until='networkidle')
        assert page.locator('#checks-data').count() == 0
        assert page.locator('#review-workspace').count() == 0
        original_text = page.locator('body').inner_text()
        assert 'Legacy section' in original_text and 'Legacy new α😀 prose.' in original_text
        assert not errors
        if inject_current_workspace:
            page.add_style_tag(path=str(ROOT / 'src/latex_review/review_workspace.css'))
            for filename in ('review_workspace.js', 'review_knowledge.js', 'review_semantic.js'):
                page.add_script_tag(path=str(ROOT / 'src/latex_review' / filename))
            # This is a genuinely absent service: all API routes return 404,
            # not a synthetic successful readonly-capabilities response.
            page.wait_for_function('window.reviewWorkspace && document.querySelector("#workspace-checks .workspace-status").textContent.includes("静态或历史页面不提供写操作")')
            assert not page.evaluate('window.reviewWorkspace.capability.available')
            assert page.evaluate('window.reviewWorkspace.report') is None
            page.locator('#workspace-tab-checks').click()
            for label in ('检查当前论文', '检查本次修改', '取消检查', '导出冻结检查结果'):
                assert page.get_by_role('button', name=label, exact=True).is_disabled()
            assert '该报告没有检查数据；静态或历史页面不提供写操作。' in page.locator('#workspace-checks').inner_text()
            page.locator('#workspace-tab-knowledge').click()
            assert page.locator('#knowledge-manager form').count() == 0
            page.locator('#workspace-tab-semantic').click()
            assert page.locator('#semantic-manager form').count() == 0
            assert page.locator('#semantic-manager button').count() == 0
            assert '只读' in page.locator('#semantic-manager').inner_text()
            page.locator('#workspace-tab-coverage').click()
            page.locator('#workspace-tab-reading').click()
            assert 'Legacy new α😀 prose.' in page.locator('#workspace-reading').inner_text()
            assert page.locator('#checks-data').count() == 0
            assert not errors
        assert all(request['method'] == 'GET' for request in requests)
        assert not any('/semantic/' in request['path'] or '/invoke' in request['path'] or '/decisions' in request['path'] for request in requests)
        assert not any(request['host'] not in {'localhost', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'cdn.mathjax.org'} for request in requests)
        assert not any('openai' in url.lower() or 'anthropic' in url.lower() for url in blocked_external)
        browser.close()
    after = {path.relative_to(directory).as_posix(): sha256(path.read_bytes()).hexdigest()
             for path in directory.rglob('*') if path.is_file()}
    assert after == before
