"""Canonical checks use raw capture facts; compiled numbers remain legacy display data."""
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from latex_review.checks import (build_check_document, capture_snapshot, evaluate_current,
                                 snapshot_from_project)
from latex_review.compile_numbering import collect
from latex_review.reading import build_reading
from latex_review.rules import current_findings


@pytest.fixture
def projection(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}\section{A}'
                     r'See \ref{missing}.\end{document}')
    raw = capture_snapshot(entry)
    # Exercise the real private-compilation collector, not an invented metadata DTO.
    private_build = tmp_path / 'private-build'
    private_build.mkdir()
    (private_build / 'main.aux').write_text(
        r'\newlabel{missing}{{99}{1}}' + '\n'
        r'\newlabel{latex-review-number-heading-1}{{99}{1}}')
    heading = next(n for n in raw.parsed.nodes if n.review.type == 'section')
    metadata = json.loads(collect(private_build, {
        'nodes': {heading.review.id: 'latex-review-number-heading-1'}, 'rows': {}}, 'main'))
    assert metadata == {'labels': {'missing': '99'}, 'nodes': {heading.review.id: '99'}, 'rows': {}}
    compiled = replace(raw.parsed, compiled_numbering=metadata)
    # Warm the mutable cached property before handing the parse to any adapter.
    assert compiled.numbering.labels['missing'] == '99'
    assert compiled.numbering.nodes[heading.review.id] == '99'
    return raw, compiled, metadata


def evaluate(snapshot):
    return evaluate_current(build_check_document(snapshot))


def test_snapshot_adapter_drops_compiled_metadata_and_cached_numbering(projection):
    raw, compiled, metadata = projection
    before = evaluate(raw)
    frozen = snapshot_from_project(compiled, raw_files=raw.files,
                                   configuration=raw.configuration, include_title=True)
    assert frozen.identity == raw.identity
    assert frozen.parsed is not compiled
    assert frozen.parsed.expanded is compiled.expanded
    assert frozen.parsed.compiled_numbering == {}
    assert 'numbering' not in frozen.parsed.__dict__
    assert evaluate(frozen) == before
    assert [f['rule_id'] for f in before['findings']] == ['unresolved_reference']
    assert compiled.compiled_numbering == metadata
    assert compiled.numbering.labels['missing'] == '99'
    # The legacy scanner still honors actual compiled metadata.
    assert current_findings(compiled)['findings'] == []
    from latex_review.rules import check_rules
    legacy = check_rules(raw.parsed, compiled)
    assert any(d.code == 'unresolved_reference' and d.rule_status == 'resolved' for d in legacy)


@pytest.mark.parametrize('with_baseline', [False, True])
def test_reading_fallback_adapters_preserve_raw_findings(projection, with_baseline):
    raw, compiled, _ = projection
    kwargs = {'old': raw.parsed} if with_baseline else {}
    expected = build_reading(raw.parsed, [], **kwargs).checks
    assert build_reading(compiled, [], **kwargs).checks == expected
    assert expected['findings'][0]['rule_id'] == 'unresolved_reference'
    assert compiled.numbering.labels['missing'] == '99'


@pytest.mark.parametrize('supplied_snapshot', [False, True])
def test_diff_report_and_cli_snapshot_adapter_use_raw_projection(tmp_path, projection, supplied_snapshot):
    from latex_review.comparison import compare_projects
    from latex_review.report import write_report
    raw, compiled, _ = projection
    # The explicit form is the CLI diff call shape, including capture-time bytes.
    kwargs = {'check_snapshot': snapshot_from_project(
        compiled, raw_files=raw.files, configuration=raw.configuration, include_title=True)} if supplied_snapshot else {}
    output = tmp_path / 'report'
    write_report(raw.parsed, compiled, compare_projects(raw.parsed, compiled), output, **kwargs)
    actual = json.loads((output / 'checks.json').read_text())
    baseline = evaluate(snapshot_from_project(raw.parsed))
    current = raw if supplied_snapshot else snapshot_from_project(raw.parsed)
    expected = evaluate_current(build_check_document(current), previous=baseline)
    assert actual == expected
    assert actual['findings'][0]['rule_id'] == 'unresolved_reference'
    assert '99' in (output / 'report.html').read_text()


def test_live_passes_original_raw_snapshot_to_reading(tmp_path, projection, monkeypatch):
    from latex_review import compilation, live, reading
    raw, compiled, metadata = projection
    output = tmp_path / 'live'
    output.mkdir()
    source = replace(raw.parsed.expanded.source, configuration={'compile': {'enabled': True}})
    seen = {}
    real_reading = reading.build_reading

    def fake_compile(source, output, **kwargs):
        path = output / 'compiled' / source.side
        path.mkdir(parents=True)
        (path / 'numbering.json').write_text(json.dumps(metadata))
        return SimpleNamespace(pdf='compiled/new/document.pdf'), ()

    def observe(project, units, **kwargs):
        seen['project'] = project
        seen['snapshot'] = kwargs['check_snapshot']
        return real_reading(project, units, **kwargs)

    monkeypatch.setattr(compilation, 'compile_side', fake_compile)
    monkeypatch.setattr(reading, 'build_reading', observe)
    # No compilation, converter, or external executable may be launched.
    monkeypatch.setattr(live, '_figure_assets', lambda *a, **k: ({}, ()))
    monkeypatch.setattr(live, '_pdf_converters', lambda *a, **k: {})
    live.build(source, output)
    assert seen['project'].compiled_numbering == compiled.compiled_numbering
    assert seen['snapshot'].parsed.compiled_numbering == {}
    assert seen['snapshot'].parsed is not seen['project']
    assert json.loads((output / 'checks.json').read_text()) == evaluate(raw)
    assert seen['project'].numbering.labels['missing'] == '99'
    assert sha256((raw.parsed.expanded.source.root / 'main.tex').read_bytes()).hexdigest() == raw.identity['files']['main.tex']


def test_canonical_rules_ignore_uncaptured_aux_even_with_warm_numbering(projection):
    raw, _, _ = projection
    expected = evaluate(raw)
    root = raw.parsed.expanded.source.root
    (root / 'main.aux').write_text(r'\newlabel{missing}{{8}{1}}')
    # Fresh capture has identical bytes/identity but legacy numbering now reads aux.
    current = capture_snapshot(root / 'main.tex')
    assert current.identity == raw.identity
    assert current.parsed.numbering.labels['missing'] == '8'
    assert current_findings(current.parsed)['findings'] == []
    assert evaluate(current) == expected
    assert evaluate(snapshot_from_project(current.parsed, include_title=True)) == expected


def test_computed_label_command_makes_absence_coverage_partial(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}\section{A}'
                     r'\csname label\endcsname{generated}See \ref{generated}.\end{document}')
    raw = capture_snapshot(entry)
    compiled = replace(raw.parsed, compiled_numbering={'labels': {'generated': '1'}})
    report = evaluate(raw)
    assert evaluate(snapshot_from_project(compiled, include_title=True)) == report
    assert report['status'] == 'partial'
    assert report['findings'] == []
    coverage = next(c for c in report['coverage'] if c['rule_id'] == 'unresolved_reference')
    assert not coverage['complete'] and coverage['status'] == 'unsupported'
    assert 'source_incomplete' in coverage['reasons']
    assert any(b['code'] == 'unexpanded_rule_macro' and 'computed commands' in b['message']
               for b in coverage['blind_spots'])


def test_computed_commands_in_verbatim_comments_and_url_do_not_disable_checks(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text('\\begin{document}\n% \\csname label\\endcsname{ignored}\n'
                     r'\verb|\csname label\endcsname{ignored}| '
                     r'\url{https://example.org/\csname} Escaped \\csname. See \ref{missing}.\end{document}')
    report = evaluate(capture_snapshot(entry))
    coverage = next(c for c in report['coverage'] if c['rule_id'] == 'unresolved_reference')
    assert coverage['complete']
    assert [(f['rule_id'], f['target']) for f in report['findings']] == [('unresolved_reference', 'missing')]


def test_computed_command_gate_preserves_existing_literal_duplicate_policy(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\begin{document}\csname label\endcsname{generated}'
                     r'\label{duplicate}\label{duplicate}\ref{missing}.\end{document}')
    report = evaluate(capture_snapshot(entry))
    assert report['status'] == 'partial'
    assert [(f['rule_id'], f['target']) for f in report['findings']] == [('duplicate_label', 'duplicate')]
    entry.write_text(r'\begin{document}\csname label\endcsname{generated}'
                     r'\iffalse\label{duplicate}\fi\label{duplicate}\end{document}')
    assert evaluate(capture_snapshot(entry))['findings'] == []


def test_engine_patch_changes_input_identity_without_rewriting_history(projection, monkeypatch):
    from copy import deepcopy
    from latex_review.checks import engine, validate_report
    from latex_review.checks.model import ENGINE_VERSION
    raw, _, _ = projection
    monkeypatch.setattr(engine, 'ENGINE_VERSION', '1.0')
    previous = evaluate(raw)
    original = deepcopy(previous)
    monkeypatch.setattr(engine, 'ENGINE_VERSION', ENGINE_VERSION)
    current = evaluate_current(build_check_document(raw), previous=previous)
    assert ENGINE_VERSION == '1.0.1'
    assert current['input']['content_id'] == previous['input']['content_id']
    assert current['input_id'] != previous['input_id']
    assert 'engine_version_changed' in current['comparison']['reasons']
    assert not current['comparison']['comparable']
    assert previous == original and validate_report(previous) == original


@pytest.mark.parametrize(('field', 'outdated'), [('engine_version', '1.0'), ('parser_version', 'older-parser')])
def test_persisted_outdated_report_is_readable_but_cannot_authorize_writes(tmp_path, projection, field, outdated):
    from copy import deepcopy
    from latex_review.checks.service import CheckCoordinator, CheckError
    from latex_review.identity import identity_hash, paper_identity
    raw, _, _ = projection
    root = raw.parsed.expanded.source.root
    identity = paper_identity(root, 'main.tex', require_entry=True)
    pid = identity['paper_id']
    service = CheckCoordinator(tmp_path / 'state', lambda key: identity if key == pid else None)
    try:
        context = service._paper(pid)
        context['executor'].submit(lambda: None).result(timeout=5)
        knowledge = service.knowledge(pid)
        document = build_check_document(raw)
        context['document'] = document
        current = evaluate_current(document, knowledge=knowledge['registry'], knowledge_status=knowledge)
        assert service._fresh(context, current)
        previous = deepcopy(current)
        previous['input'][field] = outdated
        previous['input_id'] = identity_hash(previous['input'])
        old_run = context['store'].save_report(previous)
        original = deepcopy(context['store'].get_run(old_run))
        # Source, configuration and knowledge all match; only version compatibility differs.
        assert previous['input']['files'] == current['input']['files']
        assert previous['input']['configuration_hash'] == current['input']['configuration_hash']
        assert previous['input']['knowledge_hash'] == current['input']['knowledge_hash']
        status = service.status(pid)
        assert status['stale'] and status['state'] == 'stale'
        assert status['current_input_id'] is None
        assert service.report(pid, old_run) == previous
        finding = previous['findings'][0]
        review = {'input_id': previous['input_id'], 'request_id': 'outdated-review', 'action': 'confirm',
                  'reason': 'Explicit author review', 'evidence_fingerprint': identity_hash(finding['evidence']),
                  'scope': {'id': 'project', 'domain': 'project'}, 'author': 'author'}
        with pytest.raises(CheckError) as error:
            service.review_finding(pid, finding['finding_key'], review)
        assert error.value.code == 'stale_input'
        with pytest.raises(CheckError) as error:
            service.decide(pid, {'collection': 'concepts', 'id': 'concept', 'changes': {'canonical_name': 'Name'},
                'action': 'confirm', 'reason': 'Explicit author decision', 'author': 'author',
                'scope': review['scope'], 'input_id': previous['input_id'], 'request_id': 'outdated-knowledge',
                'evidence_fingerprint': identity_hash([]), 'expected_source': knowledge['source'],
                'expected_revision': knowledge['revision'], 'expected_hash': knowledge['content_hash']})
        assert error.value.code == 'stale_input'
        assert context['service_state'].review('outdated-review') is None
        assert service.knowledge(pid) == knowledge
        assert context['store'].get_run(old_run) == original
        assert (root / 'main.tex').read_bytes() == raw.files['main.tex']
        # Rechecking with the current engine restores normal write authority,
        # without deleting or rewriting the earlier frozen export/history.
        context['store'].save_report(current)
        assert not service.status(pid)['stale']
        assert service.status(pid)['current_input_id'] == current['input_id']
        accepted = service.review_finding(pid, finding['finding_key'], {
            **review, 'input_id': current['input_id'], 'request_id': 'current-review'})
        assert accepted['decision']['input_id'] == current['input_id']
        assert context['store'].get_run(old_run) == original
        assert service.report(pid, old_run) == previous
    finally:
        service.close()
        for context in service.papers.values():
            context['executor'].shutdown(wait=True)


def test_incompatible_or_missing_report_versions_fail_freshness_closed(tmp_path, projection):
    from copy import deepcopy
    from latex_review.checks.service import CheckCoordinator
    raw, _, _ = projection
    service = CheckCoordinator(tmp_path / 'state', lambda key: None)
    try:
        report = evaluate(raw)
        for field in ('engine_version', 'parser_version'):
            missing = deepcopy(report)
            missing['input'].pop(field)
            assert not service._fresh({}, missing)
        incompatible = deepcopy(report)
        incompatible['schema_version'] = 'older-schema'
        assert not service._fresh({}, incompatible)
    finally:
        service.close()
