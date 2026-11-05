"""Display adapter never changes the legacy diagnostic/checks contracts."""
from dataclasses import asdict
import json
import re

from latex_review.checks.diagnostics import preview_diagnostics_data
from latex_review.contract import Diagnostic, SourceLocation


def test_explicit_legacy_producer_keys_and_current_side_only():
    location = SourceLocation('main.tex', 2, 2, 1, 30)
    raw = Diagnostic('unresolved_reference', 'info', '交叉引用目标不存在：missing', source_new=location)
    old = Diagnostic('unresolved_reference', 'info', raw.message, source_old=location)
    unknown = Diagnostic('unresolved_reference', 'info', 'arbitrary message missing', source_new=location)
    grouped = Diagnostic('duplicate_label', 'warning', 'any localized title', source_new=location, evidence=('键：same',))
    result = preview_diagnostics_data((raw, raw, old, unknown, grouped), 'frozen-content')
    assert result['content_id'] == 'frozen-content'
    assert len(result['diagnostics']) == 3
    assert [record['target'] for record in result['diagnostics']] == ['missing', None, 'same']
    assert result['diagnostics'][0]['diagnostic'] == asdict(raw)
    assert raw.source_new == location and raw.evidence == ()


def embedded_diagnostics(html):
    return json.loads(re.search(r'<script type="application/json" id="preview-diagnostics-data">(.*?)</script>', html, re.S).group(1))


def test_live_and_diff_publish_diagnostics_separately_without_changing_primary_counts(tmp_path):
    from test_check_workspace import source, embedded
    from latex_review.live import build
    from latex_review.structure import parse_project
    from latex_review.comparison import compare_projects
    from latex_review.report import write_report
    old = source(tmp_path / 'old', r'\documentclass{article}\begin{document}Old prose \ref{missing}.\end{document}', 'old')
    new = source(tmp_path / 'new', r'\documentclass{article}\begin{document}New prose \ref{missing}.\end{document}')
    output = tmp_path / 'live'
    output.mkdir()
    build(new, output)
    html = (output / 'report.html').read_text()
    live = json.loads((output / 'checks.json').read_text())
    display = embedded_diagnostics(html)
    assert embedded(html) == live
    assert display['content_id'] == live['input']['content_id']
    assert any(record['target'] == 'missing' and record['diagnostic']['source_new'] for record in display['diagnostics'])
    before, after = parse_project(old.expand()), parse_project(new.expand())
    comparison = compare_projects(before, after)
    result = write_report(before, after, comparison, tmp_path / 'diff')
    diff = json.loads((result.directory / 'diff.json').read_text())
    frozen = json.loads((result.directory / 'checks.json').read_text())
    assert frozen == embedded(result.html.read_text())
    display = embedded_diagnostics(result.html.read_text())
    assert display['content_id'] == frozen['input']['content_id']
    assert all(record['diagnostic']['source_new'] or not record['diagnostic']['source_old'] for record in display['diagnostics'])
    assert diff['summary']['changes'] == comparison.document.summary.changes == result.document.summary.changes
    assert 'preview_diagnostics' not in frozen and 'preview_diagnostics' not in diff
