import json
from pathlib import Path

from latex_review.agent_tools import query_main, scope_main, acceptance_main
from latex_review.run_manifest import new_manifest, finish

FIXTURE = Path(__file__).parent / 'snapshots/representative_diff.json'


def test_query_category_detail_pagination_and_raw(capsys):
    assert query_main([str(FIXTURE), '--category', 'citation', '--limit', '1']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['total_matched'] == 1
    assert data['changes'][0]['id'] == 'c1'
    assert 'raw_old' not in data['changes'][0]
    assert query_main([str(FIXTURE), '--change-id', 'c1', '--file', 'other.tex']) == 0
    assert json.loads(capsys.readouterr().out)['total_matched'] == 0
    assert query_main([str(FIXTURE), '--change-id', 'c1', '--include-raw']) == 0
    assert json.loads(capsys.readouterr().out)['changes'][0]['raw_old']
    assert query_main([str(FIXTURE), '--offset', '100']) == 0
    assert json.loads(capsys.readouterr().out)['changes'] == []


def test_scope_unknown_and_strict(capsys):
    assert scope_main([str(FIXTURE), '--allow', '**']) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['counts']['unknown'] > 0
    assert scope_main([str(FIXTURE), '--allow', '**', '--strict']) == 2
    capsys.readouterr()
    assert scope_main([str(FIXTURE), '--allow', 'elsewhere.tex']) == 0
    assert json.loads(capsys.readouterr().out)['counts']['outside'] > 0


def test_scope_move_checks_both_sides(tmp_path, capsys):
    data = json.loads(FIXTURE.read_text())
    c = data['changes'][0]
    c['source_old']['file'] = 'outside.tex'
    c['kind'] = 'moved'
    target = tmp_path / 'diff.json'
    target.write_text(json.dumps(data))
    assert scope_main([str(target), '--allow', 'sections/*', '--strict']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['outside'][0]['outside_sides'] == ['old']


def test_invalid_report_is_structured_error(tmp_path, capsys):
    target = tmp_path / 'diff.json'
    target.write_text('{}')
    assert query_main([str(target)]) == 4
    assert 'error' in json.loads(capsys.readouterr().out)


def test_acceptance_requires_evidence_and_unchanged_report(tmp_path, capsys):
    (tmp_path / 'diff.json').write_bytes(FIXTURE.read_bytes())
    (tmp_path / 'report.html').write_text('<p>report</p>')
    finish(tmp_path, new_manifest(), 0)
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('browser checks completed')
    args = [str(tmp_path), '--status', 'passed', '--evidence', str(evidence)]
    assert acceptance_main(args) == 0
    assert json.loads((tmp_path / 'run.json').read_text())['browser_acceptance']['status'] == 'passed'
    capsys.readouterr()
    (tmp_path / 'diff.json').write_text('{}')
    assert acceptance_main(args) == 4
    assert 'error' in json.loads(capsys.readouterr().out)


def test_section_filter_and_category_intersection(capsys):
    assert query_main([str(FIXTURE), '--section', '方法', '--category', 'citation']) == 0
    assert json.loads(capsys.readouterr().out)['total_matched'] == 1
    assert query_main([str(FIXTURE), '--section', '不存在']) == 0
    assert json.loads(capsys.readouterr().out)['total_matched'] == 0


def test_acceptance_survives_preview_finalization(tmp_path, capsys):
    (tmp_path / 'diff.json').write_bytes(FIXTURE.read_bytes())
    (tmp_path / 'report.html').write_text('<p>report</p>')
    run = new_manifest()
    finish(tmp_path, run, 0)
    evidence = tmp_path / 'evidence.txt'
    evidence.write_text('checks incomplete')
    assert acceptance_main([str(tmp_path), '--status', 'incomplete', '--evidence', str(evidence)]) == 0
    finish(tmp_path, run, 0)
    assert json.loads((tmp_path / 'run.json').read_text())['browser_acceptance']['status'] == 'incomplete'


def test_removed_change_checks_old_side(tmp_path, capsys):
    data = json.loads(FIXTURE.read_text())
    c = data['changes'][1]
    c['kind'] = 'removed'
    c['old_node_id'], c['new_node_id'] = data['changes'][0]['old_node_id'], None
    c['source_old'], c['source_new'] = data['changes'][0]['source_old'], None
    target = tmp_path / 'diff.json'
    target.write_text(json.dumps(data))
    assert scope_main([str(target), '--allow', 'elsewhere.tex', '--strict']) == 2
    result = json.loads(capsys.readouterr().out)
    item = next(c for c in result['outside'] if c['id'] == 'c2')
    assert item['outside_sides'] == ['old']


def test_manifest_running_has_no_final_exit_code(tmp_path):
    run = new_manifest()
    run['stages']['preview'] = 'running'
    finish(tmp_path, run, 0)
    data = json.loads((tmp_path / 'run.json').read_text())
    assert data['status'] == 'running'
    assert data['exit_code'] is None
    run['stages']['preview'] = 'complete'
    finish(tmp_path, run, 2)
    data = json.loads((tmp_path / 'run.json').read_text())
    assert data['status'] == 'degraded'
    assert data['exit_code'] == 2
