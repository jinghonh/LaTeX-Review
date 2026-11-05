"""Shared artifacts stay additive to legacy diff and immutable source evidence."""
import json
import re
from pathlib import Path

from latex_review.comparison import compare_projects
from latex_review.contract import ComparisonSource
from latex_review.live import build
from latex_review.report import write_report
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project
from latex_review.checks.model import validate_report


def source(root, text, side='new'):
    root.mkdir()
    (root / 'main.tex').write_bytes(text.encode('utf-8'))
    return ProjectSource(side, root, 'main.tex', ComparisonSource('directory', str(root)))


def embedded(html):
    return json.loads(re.search(r'<script type="application/json" id="checks-data">(.*?)</script>', html, re.S).group(1))


def test_live_frozen_sidecar_matches_embedded_full_snapshot(tmp_path):
    paper = source(tmp_path / 'paper', '\\documentclass{article}\r\n\\begin{document}\r\nUnicode α😀 text \\ref{missing}.\r\n\\end{document}\r\n')
    output = tmp_path / 'output'
    output.mkdir()
    before = (paper.root / 'main.tex').read_bytes()
    build(paper, output)
    report = validate_report(json.loads((output / 'checks.json').read_text()))
    page = json.loads((output / 'checks-page.json').read_text())
    html = (output / 'report.html').read_text()
    assert embedded(html) == report
    assert page['files'] == report['input']['files']
    assert page['content_id'] == report['input']['content_id']
    assert page['configuration_hash'] == report['input']['configuration_hash']
    from latex_review.annotations import load_content
    assert page['page_version'] == load_content(output)['version_id']
    assert (paper.root / 'main.tex').read_bytes() == before
    assert 'workspace-tab-' in html
    assert 'review-semantic-confirm-relation' in html
    assert 'latex-review-check' not in html  # no command execution UI
    # Runtime assets are embedded, rather than introducing a new remote CDN.
    assert 'src="review_workspace.js"' not in html
    assert str(paper.root) not in json.dumps(report)


def test_diff_checks_do_not_inflate_primary_changes_and_are_frozen(tmp_path):
    old = source(tmp_path / 'old', r'\documentclass{article}\begin{document}Old prose.\end{document}', 'old')
    new = source(tmp_path / 'new', r'\documentclass{article}\begin{document}New prose \ref{missing}.\end{document}')
    before, after = parse_project(old.expand()), parse_project(new.expand())
    comparison = compare_projects(before, after)
    result = write_report(before, after, comparison, tmp_path / 'report')
    frozen = (result.directory / 'checks.json').read_bytes()
    data = json.loads((result.directory / 'diff.json').read_text())
    assert 'findings' not in data and 'checks' not in data
    assert result.document.summary.changes == comparison.document.summary.changes
    report = validate_report(json.loads(frozen))
    assert embedded(result.html.read_text()) == report
    from latex_review.annotations import load_content
    page = json.loads((result.directory / 'checks-page.json').read_text())
    assert page['page_version'] == load_content(result.directory)['version_id']
    (new.root / 'main.tex').write_text('edited after report', encoding='utf-8')
    assert (result.directory / 'checks.json').read_bytes() == frozen


def test_workspace_source_uses_text_only_and_keyboard_focus_contract():
    package = Path(__file__).parents[1] / 'src' / 'latex_review'
    for filename in ('review_workspace.js', 'review_knowledge.js', 'review_semantic.js'):
        text = (package / filename).read_text()
        assert '.innerHTML' not in text
        assert 'eval(' not in text
    text = (package / 'review_workspace.js').read_text()
    assert "event.key === 'Escape'" in text
    assert "event.key === 'ArrowRight'" in text
    assert 'drawerTrigger?.isConnected' in text
    assert '(target || filter).focus' in text
    assert 'readingY' in text
