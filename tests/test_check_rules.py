from copy import deepcopy

import pytest
from jsonschema import ValidationError

from latex_review.checks import capture_snapshot, build_check_document, evaluate_current, validate_report
from latex_review.rules import check_rules


def evaluate(tmp_path, text, previous=None, **kwargs):
    path = tmp_path / 'main.tex'
    path.write_text('\\begin{document}\n' + text + '\n\\end{document}')
    document = build_check_document(capture_snapshot(path))
    return document, evaluate_current(document, previous=previous, **kwargs)


def test_literal_url_path_arguments_do_not_execute_rule_commands(tmp_path):
    _, report = evaluate(tmp_path, r'\url{https://example.org/\ref{url-only}} \href{https://example.org/\ref{url-only-two}}{\ref{real}} \path{\label{fake}\ref{path-only}} \ref{fake}')
    assert {f['target'] for f in report['findings']} == {'real', 'fake'}
    assert all(f['rule_id'] == 'unresolved_reference' for f in report['findings'])


def test_current_rules_deterministic_exact_grouped_evidence(tmp_path):
    document, report = evaluate(tmp_path, r'\ref{missing} \ref{missing} \cite{absent} \label{dup} \label{dup}')
    assert report['status'] == 'completed'
    assert {f['rule_id'] for f in report['findings']} == {'unresolved_reference', 'bibliography_key_unresolved', 'duplicate_label'}
    assert all(f['delta_status'] == 'not_comparable' for f in report['findings'])
    missing = next(f for f in report['findings'] if f['target'] == 'missing')
    assert len(missing['related_occurrences']) == 2
    assert missing['evidence'][0]['quote'] == r'\ref{missing}'
    assert evaluate_current(document) == report
    assert check_rules(document.parsed, document.parsed)
    bad = deepcopy(report)
    bad['secret'] = True
    with pytest.raises(ValidationError):
        validate_report(bad)


def test_definition_rename_rechecks_unchanged_cross_file_use_with_full_fallback(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\begin{document}\input{definition}\input{uses}\end{document}')
    definition, uses = tmp_path / 'definition.tex', tmp_path / 'uses.tex'
    definition.write_text(r'\label{shared}')
    uses.write_text(r'Unchanged cross-file use: \ref{shared}.')
    unchanged = uses.read_bytes()
    before = evaluate_current(build_check_document(capture_snapshot(entry)))
    assert not before['findings']
    definition.write_text(r'\label{renamed}')
    after = evaluate_current(build_check_document(capture_snapshot(entry)), previous=before, scope='changed')
    assert after['evaluation_mode'] == 'full'
    assert after['fallback_reason'] == 'full_scan_required_for_global_rules'
    assert uses.read_bytes() == unchanged
    finding = next(f for f in after['findings'] if f['rule_id'] == 'unresolved_reference' and f['target'] == 'shared')
    assert finding['evidence'][0]['file'] == 'uses.tex'
    assert finding['evidence'][0]['quote'] == r'\ref{shared}'


def test_new_knowledge_policy_searches_unchanged_included_text(tmp_path):
    from latex_review.checks.registry import empty_registry
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\begin{document}\input{uses}\end{document}')
    uses = tmp_path / 'uses.tex'
    uses.write_text('Previously unregistered AliasToken in unchanged prose.')
    unchanged = uses.read_bytes()
    document = build_check_document(capture_snapshot(entry))
    registry = empty_registry()
    before = evaluate_current(document, knowledge=registry)
    assert not before['findings']
    registry['concepts'].append({'id': 'new-term', 'canonical_name': 'Preferred', 'forbidden_names': ['AliasToken']})
    after = evaluate_current(document, previous=before, knowledge=registry, scope='changed')
    assert after['evaluation_mode'] == 'full' and uses.read_bytes() == unchanged
    finding = next(f for f in after['findings'] if f['rule_id'] == 'term.forbidden_name')
    assert finding['evidence'][0]['file'] == 'uses.tex' and finding['evidence'][0]['quote'] == 'AliasToken'
    assert finding['delta_status'] == 'not_comparable'
    assert 'knowledge_hash_changed' in after['comparison']['reasons']


def test_resolution_only_when_comparable_complete(tmp_path):
    _, before = evaluate(tmp_path, r'\ref{gone}')
    _, after = evaluate(tmp_path, 'Fixed', previous=before)
    assert after['findings'][0]['lifecycle'] == 'resolved'
    _, partial = evaluate(tmp_path, r'\input{missing}', previous=before)
    assert partial['status'] == 'partial'
    assert partial['findings'][0]['lifecycle'] == 'unknown'
    _, changed = evaluate(tmp_path, 'Fixed', previous=before, configuration={'checks': {'enabled': True}})
    assert changed['findings'][0]['delta_status'] == 'not_comparable'
    assert 'configuration_hash_changed' in changed['comparison']['reasons']


def test_absence_gates_keep_duplicate_positive(tmp_path):
    _, report = evaluate(tmp_path, r'\input{missing} \ref{maybe} \cite{maybe} \label{x}\label{x}')
    assert [f['rule_id'] for f in report['findings']] == ['duplicate_label']
    assert report['status'] == 'partial'
    assert all(c['applicable_count'] is None for c in report['coverage'])


def test_bibliography_unreadable_or_malformed_no_missing_claim(tmp_path):
    (tmp_path / 'refs.bib').write_text('@article{unfinished,')
    _, report = evaluate(tmp_path, r'\cite{x}\bibliography{refs}')
    assert report['status'] == 'partial'
    assert not report['findings']
    assert 'refs.bib' in report['input']['files']


@pytest.mark.parametrize('hide_tex_resolver', [False, True])
def test_canonical_fixture_raw_rules_survive_preview_blindspots(tmp_path, monkeypatch, hide_tex_resolver):
    from pathlib import Path
    import shutil
    import json
    if hide_tex_resolver:
        original = shutil.which
        monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'kpsewhich' else original(name))
    root = tmp_path / 'paper'
    shutil.copytree(Path(__file__).parent / 'fixtures' / 'writing_checks' / 'paper', root)
    snapshot = capture_snapshot('main.tex', root=root, include_title=True)
    report = evaluate_current(build_check_document(snapshot))
    # Keep all four expected rules. Failure details deliberately contain only
    # synthetic capture metadata, never source text, home config or providers.
    actual = {f['rule_id'] for f in report['findings']}
    details = {
        'status': report['status'], 'rule_ids': sorted(actual),
        'files': {name: len(raw) for name, raw in sorted(snapshot.files.items())},
        'enabled_rules': sorted(report['input']['rules']),
        'coverage': [{key: value for key, value in row.items() if key != 'blind_spots'} |
                     {'blind_spots': [{key: spot.get(key) for key in ('code', 'file')} for spot in row['blind_spots']]}
                     for row in report['coverage']],
        'expansion_diagnostics': [{key: getattr(issue, key) for key in ('code', 'certainty', 'file')}
                                  for issue in snapshot.parsed.expanded.diagnostics],
        'parse_diagnostic_codes': [issue.code for issue in snapshot.parsed.diagnostics],
    }
    assert actual == {'bibliography_key_unresolved', 'unresolved_reference', 'figure_unreferenced', 'table_unreferenced'}, json.dumps(details, indent=2)
    assert all('verbatim-only' != f['target'] and 'comment-only' != f['target'] for f in report['findings'])
    assert any(c['rule_id'] == 'source_rendering' and c['blind_spots'] for c in report['coverage'])
    assert all(c['complete'] for c in report['coverage'] if c['rule_id'] in report['input']['rules'])
    assert not any(issue.code == 'tex_style_unverified' for issue in snapshot.parsed.expanded.diagnostics)
    assert 'fixture.bst' in snapshot.files


def test_absent_fixture_style_without_tex_remains_conservative(tmp_path, monkeypatch):
    from pathlib import Path
    import shutil
    root = tmp_path / 'paper'
    shutil.copytree(Path(__file__).parent / 'fixtures' / 'writing_checks' / 'paper', root,
                    ignore=shutil.ignore_patterns('fixture.bst'))
    original = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'kpsewhich' else original(name))
    snapshot = capture_snapshot('main.tex', root=root, include_title=True)
    report = evaluate_current(build_check_document(snapshot))
    assert any(issue.code == 'tex_style_unverified' for issue in snapshot.parsed.expanded.diagnostics)
    assert report['status'] == 'partial' and report['findings'] == []
    assert all(not row['complete'] and 'source_incomplete' in row['reasons']
               for row in report['coverage'] if row['rule_id'] in report['input']['rules'])


def test_current_figure_table_and_escaped_comments(tmp_path):
    _, report = evaluate(tmp_path, r'''
\begin{figure}Picture\caption{Caption}\label{fig:x}\end{figure}
\begin{table}Table\caption{Caption}\label{tab:x}\end{table}
''')
    assert {f['rule_id'] for f in report['findings']} == {'figure_unreferenced', 'table_unreferenced'}
    _, masked = evaluate(tmp_path, r'\verb|\label{not-real}| % \label{not-real}' + '\n' + r'\begin{verbatim}\label{not-real}\end{verbatim}')
    assert all('not-real' != f['target'] for f in masked['findings'])


def test_macro_definitions_are_not_executed_label_sites(tmp_path):
    _, report = evaluate(tmp_path, r'\newcommand{\unused}{\label{x}} \label{x}')
    assert not any(f['rule_id'] == 'duplicate_label' for f in report['findings'])
    assert report['status'] == 'partial'
    assert any(b['code'] == 'unexpanded_rule_macro' for c in report['coverage'] for b in c['blind_spots'])


def test_unexecuted_tex_conditionals_cannot_prove_duplicate(tmp_path):
    _, report = evaluate(tmp_path, r'\iffalse\label{x}\fi\label{x}')
    assert report['status'] == 'partial'
    assert not report['findings']
    assert any(b['code'] == 'unexpanded_conditional' for c in report['coverage'] for b in c['blind_spots'])


def test_corrupt_knowledge_preserves_base_rules_but_partial(tmp_path):
    _, report = evaluate(tmp_path, r'\ref{missing}', knowledge_status={'status': 'corrupt', 'content_hash': 'corrupt:123'})
    assert report['status'] == 'partial'
    assert report['findings'][0]['rule_id'] == 'unresolved_reference'
    assert any(c['reasons'] == ['knowledge_corrupt'] for c in report['coverage'])


def test_frozen_project_never_reads_resource_bytes(tmp_path, monkeypatch):
    from latex_review.checks import snapshot_from_project
    (tmp_path / 'refs.bib').write_text('@article{known,title={T},author={A},year={2020}}')
    document, _ = evaluate(tmp_path, r'\cite{missing}\bibliography{refs}')
    frozen = snapshot_from_project(document.parsed)
    assert 'refs.bib' not in frozen.files
    report = evaluate_current(build_check_document(frozen))
    assert report['status'] == 'partial'
    assert not report['findings']
