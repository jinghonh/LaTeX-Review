"""Task currentness and check-specific authorization boundaries."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from functools import partial
from http.client import HTTPConnection
import json
import logging
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from latex_review.checks.http import dispatch
from latex_review.checks.service import CheckCoordinator, CheckError
from latex_review.identity import paper_identity
from latex_review.local_protocol import LocalError


@pytest.fixture
def coordinator(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    (root / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}Hello \\ref{missing}.\\end{document}')
    identity = paper_identity(root, root / 'main.tex', require_entry=True)
    service = CheckCoordinator(tmp_path / 'state', lambda pid: identity if pid == identity['paper_id'] else None)
    yield service, identity, root
    service.close()
    for context in service.papers.values():
        context['executor'].shutdown(wait=True)


def finish(service, pid):
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)


def test_async_run_and_paginated_real_evidence(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    task = service.start(pid, {'scope': 'changed'})
    assert task['state'] in ('queued', 'running')
    finish(service, pid)
    assert service.run(pid, task['run_id'])['state'] in ('completed', 'partial'), service.run(pid, task['run_id'])
    report = service.report(pid)
    assert report['evaluation_mode'] == 'full'
    assert report['requested_scope'] == 'changed'
    result, status = dispatch(service, pid, 'findings', 'GET', {'limit': ['1']})
    assert status == 200 and result['total'] >= 1
    finding = result['findings'][0]
    assert finding['evidence'][0]['file'] == 'main.tex'
    evidence, _ = dispatch(service, pid, 'knowledge/evidence', 'GET', {'limit': ['1']})
    assert evidence['units'][0]['evidence'][0]['reliability'] == 'exact'
    assert evidence['input_id'] == report['input_id']
    (root / 'main.tex').write_text('changed')
    assert service.status(pid)['stale'] is True


def test_cancel_is_idempotent_and_late_result_not_current(coordinator, monkeypatch):
    service, identity, root = coordinator
    pid = identity['paper_id']
    import latex_review.checks.service as module
    actual = module.evaluate_current
    started, release = Event(), Event()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return actual(*args, **kwargs)
    monkeypatch.setattr(module, 'evaluate_current', blocked)
    task = service.start(pid, {})
    assert started.wait(10)
    service.cancel(pid, task['run_id'])
    service.cancel(pid, task['run_id'])
    release.set()
    finish(service, pid)
    assert service.run(pid, task['run_id'])['state'] == 'cancelled'
    with pytest.raises(CheckError, match='尚无'):
        service.report(pid)


def test_newer_task_prevents_old_input_becoming_current(coordinator, monkeypatch):
    service, identity, root = coordinator
    pid = identity['paper_id']
    import latex_review.checks.service as module
    actual = module.evaluate_current
    started, release = Event(), Event()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return actual(*args, **kwargs)
    monkeypatch.setattr(module, 'evaluate_current', blocked)
    old = service.start(pid, {})
    assert started.wait(10)
    newer = service.start(pid, {})
    release.set()
    finish(service, pid)
    assert service.run(pid, old['run_id'])['state'] == 'stale'
    assert service.run(pid, newer['run_id'])['state'] in ('completed', 'partial')
    assert service._paper(pid)['current'] == newer['run_id']


def test_missing_dependency_appearing_invalidates_status(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    (root / 'main.tex').write_text('\\documentclass{article}\\begin{document}\\input{later}\\end{document}')
    task = service.start(pid, {})
    finish(service, pid)
    assert not service.status(pid)['stale']
    (root / 'later.tex').write_text('Now present.')
    assert service.status(pid)['stale']


def test_frozen_page_binding_rejects_disk_and_config_changes(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    from latex_review.checks.snapshot import capture_snapshot
    from latex_review.identity import identity_hash
    snapshot = capture_snapshot(root / 'main.tex', root=root, configuration={})
    metadata = {**snapshot.identity, 'configuration_hash': identity_hash({}), 'page_version': 'test-page-v1'}
    service.validate_page(pid, metadata)
    (root / 'main.tex').write_text('edited before preview rebuild')
    with pytest.raises(CheckError, match='源码已变更'):
        dispatch(service, pid, 'runs', 'POST', {}, {'_page_snapshot': metadata})
    assert not service._paper(pid)['tasks']
    with pytest.raises(CheckError, match='配置'):
        service.validate_page(pid, {**metadata, 'configuration_hash': 'wrong'})


def test_effective_profile_recipe_matches_frozen_page_and_cli_snapshot(coordinator, monkeypatch, tmp_path):
    service, identity, root = coordinator
    pid = identity['paper_id']
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'external-cache'))
    from latex_review.project_profile import directory
    profile = directory(root) / 'project.toml'
    profile.parent.mkdir(parents=True)
    profile.write_text('[checks]\ndebounce_ms=900\non_save=false\n[source.include_macros]\nchapterfile="#1"\n')
    explicit = tmp_path / 'explicit.toml'
    explicit.write_text('[checks]\non_save=true\n')
    paper = {**identity, 'checks_recipe': {'config': str(explicit), 'config_explicit': True}}
    service.paper_lookup = lambda _: paper
    effective = service._configuration(service._paper(pid))
    assert effective['source']['include_macros'] == {'chapterfile': '#1'}
    assert effective['checks']['on_save'] is True and effective['checks']['debounce_ms'] == 900
    from latex_review.checks.snapshot import capture_snapshot
    from latex_review.cli import _config
    from latex_review.identity import identity_hash
    snapshot = capture_snapshot(root / 'main.tex', root=root, configuration=_config(explicit, True), include_title=True)
    assert snapshot.configuration == effective
    diff_snapshot = capture_snapshot(root / 'main.tex', root=root, configuration={**effective,
        'compile': {'enabled': False, 'engine': 'pdflatex', 'timeout': 90}, 'diff': {'comments': True},
        'render': {'math': True}}, include_title=True)
    assert diff_snapshot.configuration == effective and diff_snapshot.content_id == snapshot.content_id
    metadata = {**snapshot.identity, 'configuration_hash': identity_hash(snapshot.configuration)}
    service.validate_page(pid, metadata)
    assert service.status(pid)['configuration_hash'] == identity_hash(snapshot.configuration)
    with pytest.raises(CheckError, match='配置'):
        service.start(pid, {'configuration_hash': 'wrong'})
    task = service.start(pid, {'configuration_hash': identity_hash(effective)}, page_snapshot=metadata)
    finish(service, pid)
    report = service.report(pid)
    assert service.run(pid, task['run_id'])['state'] in ('completed', 'partial')
    assert report['input']['content_id'] == snapshot.content_id
    assert report['input']['configuration_hash'] == identity_hash(snapshot.configuration)


def test_knowledge_source_selection_and_conditional_writes(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    before = service.knowledge(pid, source='external')
    result = service.knowledge(pid, {'registry': before['registry'], 'expected_revision': before['revision'],
                                     'expected_hash': before['content_hash'], 'expected_source': before['source']})
    assert result['revision'] == before['revision'] + 1
    from latex_review.checks.registry import KnowledgeConflict
    with pytest.raises(KnowledgeConflict):
        service.knowledge(pid, {'registry': before['registry'], 'expected_revision': before['revision'],
                                'expected_hash': before['content_hash'], 'expected_source': before['source']})
    assert service.knowledge(pid)['source'] == 'external'
    preview = service.knowledge(pid, {'operation': 'import_preview', 'registry': result['registry']})
    assert preview['conflicts'] == []


def test_source_bound_mutations_cannot_cross_equal_empty_stores(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    external = service.knowledge(pid, source='external')
    project = service.knowledge(pid, source='project')
    assert external['revision'] == project['revision'] and external['content_hash'] == project['content_hash']
    with pytest.raises(CheckError, match='知识来源'):
        service.knowledge(pid, {'registry': external['registry'], 'expected_source': 'external',
                                'expected_revision': external['revision'], 'expected_hash': external['content_hash']})
    assert service.knowledge(pid)['revision'] == project['revision']


def test_advanced_registry_writes_imports_and_edit_cannot_activate_forged_evidence(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    service.start(pid, {})
    finish(service, pid)
    report = service.report(pid)
    current = service.knowledge(pid)
    fragment = report['findings'][0]['evidence'][0]
    forged = {**fragment, 'quote': 'x' * len(fragment['quote'])}
    record = {'id': 'forged-role', 'target_id': 'manually-named', 'role': 'definition', 'entry': 'main.tex',
              'scope': {'id': 'project', 'domain': 'project'}, 'source': 'author_confirmed', 'evidence': [forged]}
    registry = json.loads(json.dumps(current['registry']))
    registry['occurrences'].append(record)
    conditions = {'expected_source': current['source'], 'expected_revision': current['revision'], 'expected_hash': current['content_hash']}
    with pytest.raises(CheckError, match='证据'):
        service.knowledge(pid, {'operation': 'write', 'registry': registry, **conditions})
    from latex_review.checks.finding_review import fingerprint
    with pytest.raises(CheckError, match='证据'):
        service.decide(pid, {'collection': 'occurrences', 'id': record['id'], 'changes': record, 'action': 'edit',
                             'reason': 'author advanced edit', 'author': 'author', 'scope': record['scope'],
                             'evidence_fingerprint': fingerprint([forged]), 'input_id': report['input_id'],
                             'request_id': 'forged-advanced-edit', **conditions})
    assert service.knowledge(pid)['revision'] == current['revision']
    imported = service.knowledge(pid, {'operation': 'import', 'registry': registry, **conditions})
    assert imported['registry']['occurrences'][0]['status'] == 'stale'


def test_foreign_import_without_report_stays_stale_and_reconfirmation_requires_current_source(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    current = service.knowledge(pid)
    registry = current['registry']
    evidence = {'entry': 'foreign.tex', 'file': 'foreign.tex', 'content_id': 'a' * 64, 'file_sha256': 'b' * 64,
                'start': 0, 'end': 7, 'quote': 'Foreign', 'include_instance': 'foreign:0', 'start_line': 1,
                'start_column': 1, 'end_line': 1, 'end_column': 8, 'reliability': 'exact', 'reason': None}
    record = {'id': 'foreign-role', 'target_id': 'manual', 'role': 'definition', 'entry': 'foreign.tex',
              'scope': {'id': 'project', 'domain': 'project'}, 'source': 'author_confirmed', 'evidence': [evidence]}
    registry['occurrences'].append(record)
    imported = service.knowledge(pid, {'operation': 'import', 'registry': registry, 'expected_source': current['source'],
                                      'expected_revision': current['revision'], 'expected_hash': current['content_hash']})
    assert imported['registry']['occurrences'][0]['status'] == 'stale'
    service.start(pid, {})
    finish(service, pid)
    from latex_review.checks.finding_review import fingerprint
    with pytest.raises(CheckError, match='证据'):
        service.decide(pid, {'collection': 'occurrences', 'id': record['id'], 'changes': {'status': 'confirmed'}, 'action': 'confirm',
                             'reason': 'reconfirm imported role', 'author': 'author', 'scope': record['scope'],
                             'evidence_fingerprint': fingerprint([evidence]), 'input_id': service.report(pid)['input_id'],
                             'request_id': 'reconfirm-foreign', 'expected_source': imported['source'],
                             'expected_revision': imported['revision'], 'expected_hash': imported['content_hash']})


def test_shared_root_registry_validates_each_entry_profile_and_recovery_independently(coordinator, monkeypatch, tmp_path):
    service, identity_a, root = coordinator
    pid_a = identity_a['paper_id']
    (root / 'second.tex').write_text(r'\documentclass{article}\begin{document}Second \ref{missing-b}.\end{document}')
    from latex_review.identity import paper_identity
    from latex_review.project_profile import directory
    from latex_review.checks.finding_review import fingerprint
    identity_b = paper_identity(root, root / 'second.tex', require_entry=True)
    pid_b = identity_b['paper_id']
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'external-cache'))
    profile = directory(root) / 'project.toml'
    profile.parent.mkdir(parents=True)
    profile.write_text('[project]\nentries=["main.tex"]\n[checks]\ndebounce_ms=901\n[source.include_macros]\nchapterfile="#1"\n')
    recipe_b = tmp_path / 'second-recipe.toml'
    recipe_b.write_text('[checks]\non_save=false\n')
    papers = {pid_a: identity_a, pid_b: {**identity_b, 'checks_recipe': {'config': str(recipe_b), 'config_explicit': True}}}
    service.paper_lookup = papers.get
    assert service._configuration(service._paper(pid_a))['checks']['debounce_ms'] == 901
    assert service._configuration(service._paper(pid_b)) == {'checks': {'on_save': False}}
    service.start(pid_a, {})
    finish(service, pid_a)
    evidence_a = service.report(pid_a)['findings'][0]['evidence']
    def role(key, entry, evidence):
        return {'id': key, 'target_id': key, 'role': 'definition', 'entry': entry, 'source': 'author_confirmed',
                'scope': {'id': 'project', 'domain': 'project'}, 'evidence': evidence}
    def conditions(envelope):
        return {'expected_source': envelope['source'], 'expected_revision': envelope['revision'], 'expected_hash': envelope['content_hash']}
    current = service.knowledge(pid_a)
    current['registry']['occurrences'].append(role('role-a', 'main.tex', evidence_a))
    current = service.knowledge(pid_a, {'registry': current['registry'], **conditions(current)})
    service.start(pid_b, {})
    finish(service, pid_b)
    evidence_b = next(f for f in service.report(pid_b)['findings'] if f['lifecycle'] == 'current')['evidence']
    current['registry']['occurrences'].append(role('role-b', 'second.tex', evidence_b))
    papers.pop(pid_a)  # A's default/profile fact survives after A is unregistered.
    import latex_review.checks.service as module
    captures, capture = [], module.capture_snapshot
    def counted(entry, **kwargs):
        captures.append(entry)
        return capture(entry, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'capture_snapshot', counted)
        current = service.knowledge(pid_b, {'registry': current['registry'], **conditions(current)})
    assert captures == ['main.tex']
    recipe_a = tmp_path / 'first-recipe.toml'
    recipe_a.write_text('[checks]\ndebounce_ms=901\n')
    papers[pid_a] = {**identity_a, 'checks_recipe': {'config': str(recipe_a), 'config_explicit': True}}
    service.start(pid_b, {})
    finish(service, pid_b)
    current = service.decide(pid_b, {'collection': 'occurrences', 'id': 'role-a', 'changes': {'notes': 'B edits shared A fact'},
        'action': 'edit', 'reason': 'explicit shared registry edit', 'author': 'author', 'scope': {'id': 'project', 'domain': 'project'},
        'evidence_fingerprint': fingerprint(evidence_a), 'input_id': service.report(pid_b)['input_id'], 'request_id': 'shared-edit',
        **conditions(current)})
    original_a = (root / 'main.tex').read_bytes()
    (root / 'main.tex').write_bytes(original_a + b'\n% changed A')
    service.start(pid_b, {})
    finish(service, pid_b)
    with pytest.raises(CheckError, match='证据'):
        service.knowledge(pid_b, {'registry': current['registry'], **conditions(current)})
    assert service.knowledge(pid_b)['content_hash'] == current['content_hash']
    store = service._knowledge(service._paper(pid_b))
    backup = store.backup_path.read_bytes()
    store.path.write_bytes(b'{corrupt shared registry')
    corrupt = store.read()
    recovery = {'operation': 'recover', **conditions(corrupt)}
    with pytest.raises(CheckError, match='证据'):
        service.knowledge(pid_b, recovery)
    assert store.path.read_bytes() == b'{corrupt shared registry' and store.backup_path.read_bytes() == backup
    (root / 'main.tex').write_bytes(original_a)
    recovered = service.knowledge(pid_b, recovery)
    assert {r['id'] for r in recovered['registry']['occurrences']} == {'role-a', 'role-b'}
    service.start(pid_b, {})
    finish(service, pid_b)
    deleted = service.decide(pid_b, {'collection': 'occurrences', 'id': 'role-a', 'changes': None,
        'action': 'reject', 'reason': 'temporarily delete A fact', 'author': 'author', 'scope': {'id': 'project', 'domain': 'project'},
        'evidence_fingerprint': fingerprint(evidence_a), 'input_id': service.report(pid_b)['input_id'], 'request_id': 'shared-delete',
        **conditions(recovered)})
    undo = {'operation': 'undo', 'decision_id': deleted['registry']['decisions'][-1]['id'], **conditions(deleted)}
    before_undo, before_backup = store.path.read_bytes(), store.backup_path.read_bytes()
    (root / 'main.tex').write_bytes(original_a + b'\n% stale A again')
    with pytest.raises(CheckError, match='证据'):
        service.knowledge(pid_b, undo)
    assert store.path.read_bytes() == before_undo and store.backup_path.read_bytes() == before_backup
    (root / 'main.tex').write_bytes(original_a)
    restored = service.knowledge(pid_b, undo)
    assert {r['id'] for r in restored['registry']['occurrences']} == {'role-a', 'role-b'}


def test_recovery_preflight_rejects_stale_backup_without_touching_corrupt_bytes(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    original = (root / 'main.tex').read_bytes()
    service.start(pid, {})
    finish(service, pid)
    fragment = service.report(pid)['findings'][0]['evidence'][0]
    store = service._knowledge(service._paper(pid))
    current = store.read()
    registry = current['registry']
    registry['occurrences'].append({'id': 'saved-role', 'target_id': 'manual', 'role': 'definition', 'entry': 'main.tex',
                                    'scope': {'id': 'project', 'domain': 'project'}, 'source': 'author_confirmed', 'evidence': [fragment]})
    current = store.write(registry, expected_revision=current['revision'], expected_hash=current['content_hash'])
    store.write(current['registry'], expected_revision=current['revision'], expected_hash=current['content_hash'])
    backup = store.backup_path.read_bytes()
    store.path.write_bytes(b'{corrupt-author-bytes')
    corrupt = store.read()
    body = {'operation': 'recover', 'expected_source': corrupt['source'], 'expected_revision': corrupt['revision'], 'expected_hash': corrupt['content_hash']}
    (root / 'main.tex').write_bytes(original + b'\n% source changed')
    with pytest.raises(CheckError, match='源码已变更'):
        service.knowledge(pid, body)
    assert store.path.read_bytes() == b'{corrupt-author-bytes' and store.backup_path.read_bytes() == backup
    (root / 'main.tex').write_bytes(original)
    recovered = service.knowledge(pid, body)
    assert recovered['registry']['occurrences'][0]['evidence'] == [fragment]
    assert recovered['status'] != 'corrupt'


def test_queued_ids_survive_entry_restart(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    context = service._paper(pid)
    finish(service, pid)
    release = Event()
    context['executor'].submit(release.wait)
    queued = service.start(pid, {})
    assert queued['state'] == 'queued'
    service.close()
    release.set()
    context['executor'].shutdown(wait=True)
    other = CheckCoordinator(service.state, lambda _: identity)
    try:
        other._paper(pid)
        finish(other, pid)
        assert other.run(pid, queued['run_id'])['state'] == 'interrupted'
    finally:
        other.close()
        other._paper(pid)['executor'].shutdown(wait=True)


def test_finding_decisions_persist_without_mutating_frozen_result(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    task = service.start(pid, {})
    finish(service, pid)
    report = service.report(pid)
    finding = report['findings'][0]
    from latex_review.checks.finding_review import fingerprint
    body = {'input_id': report['input_id'], 'request_id': 'author-confirm-1', 'action': 'confirm',
            'reason': 'Reviewed the exact source', 'evidence_fingerprint': fingerprint(finding['evidence']),
            'scope': {'id': 'project', 'domain': 'project'}}
    result, status = dispatch(service, pid, 'findings/' + finding['finding_key'] + '/decisions', 'POST', {}, body)
    assert status == 200 and result['finding']['assessment'] == 'author_confirmed'
    assert service.review_finding(pid, finding['finding_key'], body)['replayed']
    with pytest.raises(CheckError, match='请求 ID'):
        service.review_finding(pid, finding['finding_key'], {**body, 'action': 'reject'})
    saved = service._paper(pid)['store'].get_run(task['run_id'])['report']['findings'][0]
    assert saved['review_state'] == 'unreviewed'
    assert service.report(pid)['findings'][0]['review_state'] == 'confirmed'
    assert service.report(pid)['findings'][0]['origin'] == finding['origin']
    assert service.report(pid)['findings'][0]['lifecycle'] == finding['lifecycle']
    other = CheckCoordinator(service.state, lambda _: identity)
    try:
        assert other.status(pid)['current_run']['run_id'] == task['run_id']
        assert other.report(pid)['findings'][0]['review_state'] == 'confirmed'
    finally:
        other.close()
        other._paper(pid)['executor'].shutdown(wait=True)
    rejected = service.review_finding(pid, finding['finding_key'], {**body, 'request_id': 'reject-1', 'action': 'reject'})
    assert rejected['finding']['review_state'] == 'rejected'
    deferred = service.review_finding(pid, finding['finding_key'], {**body, 'request_id': 'defer-1', 'action': 'defer'})
    assert deferred['finding']['review_state'] == 'unreviewed'
    with pytest.raises(CheckError, match='指纹'):
        service.review_finding(pid, finding['finding_key'], {**body, 'request_id': 'bad-fingerprint', 'evidence_fingerprint': 'wrong'})
    waived = service.review_finding(pid, finding['finding_key'], {**body, 'request_id': 'waive-1', 'action': 'waive',
                                                                'expected_source': service.knowledge(pid)['source']})
    assert waived['finding']['review_state'] == 'waived'
    knowledge = service.knowledge(pid)
    assert knowledge['registry']['exceptions'][0]['valid_when']['rule_version'] == finding['rule_version']
    assert service.status(pid)['stale']
    service.start(pid, {})
    finish(service, pid)
    current = next(f for f in service.report(pid)['findings'] if f['finding_key'] == finding['finding_key'])
    assert current['review_state'] == 'waived'
    (root / 'main.tex').write_text((root / 'main.tex').read_text() + '\n% changed source input')
    service.start(pid, {})
    finish(service, pid)
    current = next(f for f in service.report(pid)['findings'] if f['finding_key'] == finding['finding_key'])
    assert current['review_state'] == 'unreviewed'


def test_historical_findings_refuse_all_author_actions_without_writes(coordinator, monkeypatch):
    service, identity, root = coordinator
    pid = identity['paper_id']
    service.start(pid, {})
    finish(service, pid)
    report = service.report(pid)
    finding = report['findings'][0]
    knowledge = service.knowledge(pid)
    from latex_review.checks.finding_review import fingerprint
    monkeypatch.setattr(service, 'report', lambda _: report)
    journal = service._paper(pid)['service_state']
    for lifecycle in ('resolved', 'stale', 'unknown'):
        finding['lifecycle'] = lifecycle
        for action in ('confirm', 'reject', 'waive', 'defer'):
            request_id = lifecycle + '-' + action
            with pytest.raises(CheckError) as error:
                service.review_finding(pid, finding['finding_key'], {'input_id': report['input_id'],
                    'request_id': request_id, 'action': action, 'reason': 'explicit author review',
                    'evidence_fingerprint': fingerprint(finding['evidence']), 'scope': {'id': 'project', 'domain': 'project'},
                    'expected_source': knowledge['source']})
            assert error.value.code == 'finding_not_current'
            assert journal.review(request_id) is None
    assert service.knowledge(pid)['content_hash'] == knowledge['content_hash']


def test_debounced_saved_notification_binds_rendered_page_and_rejects_new_disk(coordinator, monkeypatch):
    service, identity, root = coordinator
    pid = identity['paper_id']
    from latex_review.live import build, _check_saved_recipe
    from latex_review.sources import ProjectSource
    from latex_review.contract import ComparisonSource
    from latex_review import local_client
    output = root / 'preview'
    output.mkdir()
    build(ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root))), output)
    attempts = []
    def bridge(paper_id, path, body):
        attempts.append(body)
        return dispatch(service, paper_id, path, 'POST', {}, body)
    monkeypatch.setattr(local_client, 'checks_request', bridge)
    recipe = {**identity, 'output': str(output)}
    _check_saved_recipe(recipe, {})
    finish(service, pid)
    original = service.report(pid)['input_id']
    count = len(service._paper(pid)['tasks'])
    assert attempts[0]['_page_snapshot']['files']
    (root / 'main.tex').write_text('Changed disk without a successful reading rebuild.')
    _check_saved_recipe(recipe, {})  # Source mismatch is caught; reading stays usable.
    assert len(attempts) == 2 and len(service._paper(pid)['tasks']) == count
    assert service.report(pid)['input_id'] == original
    (output / 'checks-page.json').write_text('invalid metadata')
    _check_saved_recipe(recipe, {})
    assert len(attempts) == 2
    from latex_review.annotations import MANIFEST
    legacy = root / 'legacy-preview'
    legacy.mkdir()
    (legacy / MANIFEST).write_text((output / MANIFEST).read_text())
    _check_saved_recipe({**recipe, 'output': str(legacy)}, {})
    assert len(attempts) == 2  # Missing trusted metadata also cannot submit.


def test_forged_source_evidence_rejected(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    service.start(pid, {})
    finish(service, pid)
    report = service.report(pid)
    evidence = report['findings'][0]['evidence']
    from latex_review.checks.finding_review import validate_evidence
    validate_evidence(service._paper(pid), report, evidence)
    for forged in ({**evidence[0], 'quote': 'invented'}, {**evidence[0], 'include_instance': 'invented'},
                   {**evidence[0], 'start_line': evidence[0]['start_line'] + 1}):
        with pytest.raises(CheckError, match='证据'):
            validate_evidence(service._paper(pid), report, [forged])


def test_restart_recovers_abandoned_run(coordinator):
    service, identity, root = coordinator
    pid = identity['paper_id']
    context = service._paper(pid)
    finish(service, pid)
    abandoned = context['store'].begin_run(pid)
    other = CheckCoordinator(service.state, lambda _: identity)
    try:
        other._paper(pid)
        finish(other, pid)
        assert other.run(pid, abandoned)['state'] == 'interrupted'
    finally:
        other.close()
        other._paper(pid)['executor'].shutdown(wait=True)


def request(server, path, *, body=None, headers=None):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    values = dict(headers or {})
    if body is not None:
        values['Content-Type'] = 'application/json'
    connection.request('POST' if body is not None else 'GET', path,
                       json.dumps(body) if body is not None else None, values)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


@pytest.fixture
def worker(tmp_path, monkeypatch):
    from latex_review.serve import create_server
    root = tmp_path / 'paper'
    root.mkdir()
    (root / 'main.tex').write_text('Hello')
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'report.html').write_text('<html></html>')
    identity = paper_identity(root, root / 'main.tex', require_entry=True)
    (report / 'checks-page.json').write_text(json.dumps({**identity, 'files': {}, 'page_version': 'page-v1', 'content_id': 'c' * 64}))
    monkeypatch.setattr('latex_review.annotations.load_content', lambda _: {'entry': 'main.tex', 'version_id': 'page-v1'})
    server = create_server(report, annotation_project_root=root)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, identity
    server.shutdown()
    thread.join()
    server.server_close()


def test_worker_checks_three_independent_credentials(worker, monkeypatch):
    server, identity = worker
    calls = []
    def bridge(*args, **kwargs):
        calls.append(args)
        return {'available': True, 'capabilities': {}}
    monkeypatch.setattr('latex_review.local_client.checks_request', bridge)
    status, value = request(server, '/api/checks/status')
    assert status == 200 and value['token'] == server.checks_token
    assert 'admin' not in value
    headers = {'X-LaTeX-Review-Token': value['token'], 'X-LaTeX-Review-Paper-ID': identity['paper_id'],
               'X-LaTeX-Review-Page-Version': 'page-v1', 'X-LaTeX-Review-Checks-Content-ID': 'c' * 64}
    assert request(server, '/api/checks/runs', body={}, headers=headers)[0] == 202
    assert len(calls) == 2
    for key, replacement, expected in [('X-LaTeX-Review-Token', 'wrong', 403),
                                       ('X-LaTeX-Review-Paper-ID', '0' * 64, 403),
                                       ('X-LaTeX-Review-Page-Version', 'old', 409)]:
        assert request(server, '/api/checks/runs', body={}, headers={**headers, key: replacement})[0] == expected
    assert len(calls) == 2
    assert request(server, '/api/checks/status', headers={'Origin': 'http://evil.invalid'})[0] == 403


def test_direct_worker_entry_unavailable_is_read_only(worker, monkeypatch):
    server, _ = worker
    def unavailable(*args, **kwargs):
        raise LocalError('entry unavailable', 503)
    monkeypatch.setattr('latex_review.local_client.checks_request', unavailable)
    status, value = request(server, '/api/checks/status')
    assert status == 200 and not value['available'] and value['read_only']
    assert not any(value['capabilities'].values())


def test_canonical_reads_also_require_entry_token(tmp_path):
    from latex_review.local_service import EntryServer, Handler
    from latex_review.checks.http import entry_route
    server = EntryServer(('127.0.0.1', 0), Handler)
    server.token = 'private-entry-token'
    server.manager = SimpleNamespace(logger=logging.getLogger('test'), checks=SimpleNamespace(status=lambda pid: {'paper_id': pid}))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = '/api/v1/papers/' + 'a' * 64 + '/checks/status'
    try:
        assert request(server, path)[0] == 403
        assert request(server, path, headers={'X-LaTeX-Review-Token': server.token})[0] == 200
        assert request(server, path, headers={'X-LaTeX-Review-Token': server.token, 'Origin': 'null'})[0] == 403
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
