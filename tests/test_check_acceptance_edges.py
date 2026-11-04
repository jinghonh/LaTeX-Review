"""AC11/18: real asynchronous coordinator over author-confirmed knowledge.

Synthetic papers, real parser/source maps/registry/executor; no HTTP or provider.
"""
from copy import deepcopy
from hashlib import sha256

import pytest

from latex_review.checks.service import CheckCoordinator
from latex_review.identity import paper_identity


RULE = 'concept.use_before_definition'


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    papers = {}
    service = CheckCoordinator(tmp_path / 'state', papers.get)
    yield root, papers, service
    service.close()
    for context in service.papers.values():
        context['executor'].shutdown(wait=True)


def _entry(root, papers, name, includes):
    path = root / name
    path.write_text('\\documentclass{article}\n\\begin{document}\n' + ''.join(
        f'\\input{{{part}}}\n' for part in includes) + '\\end{document}\n')
    identity = paper_identity(root, path, require_entry=True)
    papers[identity['paper_id']] = identity
    return identity['paper_id']


def _run(service, pid, scope='all'):
    task = service.start(pid, {'scope': scope})
    assert task['state'] in {'queued', 'running'}
    service._paper(pid)['executor'].submit(lambda: None).result(timeout=20)
    finished = service.run(pid, task['run_id'])
    assert finished['state'] in {'completed', 'partial'}, finished
    assert service.status(pid)['current_run']['run_id'] == task['run_id']
    return service.report(pid)


def _role(service, pid, key, file, quote, role):
    document = service._paper(pid)['document']
    text = document.parsed.expanded.text
    assert text.count(quote) == 1
    start = text.index(quote)
    evidence = document.evidence(start, start + len(quote))
    entry = document.snapshot.entry
    assert len(evidence) == 1 and evidence[0]['file'] == file
    return {'id': key, 'target_id': 'shared-filter', 'role': role,
            'entry': entry, 'status': 'confirmed', 'source': 'author_confirmed',
            'scope': {'id': 'body', 'domain': 'body', 'entry': entry},
            'evidence': evidence}


def _register(service, pid, roles):
    current = service.knowledge(pid)
    registry = deepcopy(current['registry'])
    registry['concepts'] = [{'id': 'shared-filter', 'canonical_name': 'Shared Filter',
                             'status': 'confirmed', 'source': 'author_confirmed'}]
    registry['occurrences'] = roles
    return service.knowledge(pid, {'registry': registry, 'expected_source': current['source'],
                                  'expected_revision': current['revision'],
                                  'expected_hash': current['content_hash']})


def _coverage(report):
    return next(row for row in report['coverage'] if row['rule_id'] == RULE)


def test_deleted_registered_concept_definition_rechecks_untouched_usage_async(project):
    root, papers, service = project
    usage = root / 'usage.tex'
    definition = root / 'definition.tex'
    usage.write_text('Shared Filter is formally used by the estimator.\n')
    definition.write_text('Shared Filter means the normalized linear filter.\n')
    original_usage = usage.read_bytes()
    pid = _entry(root, papers, 'main.tex', ['usage', 'definition'])
    _run(service, pid)
    roles = [_role(service, pid, 'use-main', 'usage.tex',
                   'Shared Filter is formally used', 'formal_use'),
             _role(service, pid, 'definition-main', 'definition.tex',
                   'Shared Filter means the normalized linear filter', 'definition')]
    saved = _register(service, pid, roles)
    before = _run(service, pid)
    old = [finding for finding in before['findings'] if finding['rule_id'] == RULE]
    assert len(old) == 1 and old[0]['lifecycle'] == 'current'
    assert [e['file'] for e in old[0]['evidence']] == ['usage.tex', 'definition.tex']
    assert _coverage(before)['complete'] and _coverage(before)['applicable_count'] == 1

    # Delete the actual registered definition text, not a label or the include.
    # Parser coverage remains reliable; only the author-confirmed fact is stale.
    definition.write_text('This paragraph no longer defines any concept.\n')
    assert service.status(pid)['stale']
    after = _run(service, pid, scope='changed')
    assert after['evaluation_mode'] == 'full' and after['requested_scope'] == 'changed'
    assert after['fallback_reason'] == 'full_scan_required_for_global_rules'
    assert after['input_id'] != before['input_id']
    assert after['input']['files']['usage.tex'] == before['input']['files']['usage.tex'] == sha256(original_usage).hexdigest()
    assert usage.read_bytes() == original_usage
    assert after['input']['files']['definition.tex'] != before['input']['files']['definition.tex']
    assert after['input']['knowledge_hash'] == before['input']['knowledge_hash']
    assert service.knowledge(pid)['registry'] == saved['registry']

    coverage = _coverage(after)
    # A real full re-evaluation still sees the untouched formal use, rather than
    # only scanning the changed definition file or declaring an empty clean run.
    assert coverage['applicable_count'] == 1
    assert coverage['status'] == 'prerequisite_missing' and not coverage['complete']
    assert {'stale_occurrence_evidence', 'confirmed_definition_in_scope_missing'} <= set(coverage['reasons'])
    assert not coverage['blind_spots']  # not a missing-input/unknown-macro test
    assert after['status'] == 'partial'
    retained = [finding for finding in after['findings'] if finding['rule_id'] == RULE]
    assert len(retained) == 1 and retained[0]['finding_key'] == old[0]['finding_key']
    assert retained[0]['lifecycle'] == 'unknown'  # neither fixed nor a new certainty
    assert not any(finding['lifecycle'] == 'current' and finding['rule_id'].startswith('concept.')
                   for finding in after['findings'])


@pytest.mark.parametrize('violating_entry', ['main.tex', 'supplement.tex'])
def test_shared_name_with_two_confirmed_entry_definitions_isolates_findings(project, violating_entry):
    root, papers, service = project
    pids = {}
    meanings = {'main.tex': 'the normalized linear filter',
                'supplement.tex': 'the discrete empirical lookup filter'}
    for entry, meaning in meanings.items():
        prefix = entry.removesuffix('.tex')
        (root / f'{prefix}-use.tex').write_text(f'Shared Filter is formally used in {prefix}.\n')
        (root / f'{prefix}-definition.tex').write_text(f'Shared Filter means {meaning}.\n')
        parts = [f'{prefix}-use', f'{prefix}-definition']
        if entry != violating_entry:
            parts.reverse()
        pids[entry] = _entry(root, papers, entry, parts)
        _run(service, pids[entry])
    original_files = {path.name: path.read_bytes() for path in root.glob('*.tex')}
    roles = []
    for entry, pid in pids.items():
        prefix = entry.removesuffix('.tex')
        roles += [_role(service, pid, prefix + '-use', f'{prefix}-use.tex',
                        f'Shared Filter is formally used in {prefix}', 'formal_use'),
                  _role(service, pid, prefix + '-definition', f'{prefix}-definition.tex',
                        f'Shared Filter means {meanings[entry]}', 'definition')]
    saved = _register(service, pids['main.tex'], roles)
    assert len(saved['registry']['concepts']) == 1  # one project-wide name, not renamed copies
    definitions = [role for role in saved['registry']['occurrences'] if role['role'] == 'definition']
    assert {role['entry'] for role in definitions} == set(pids)
    assert all(role['status'] == 'confirmed' and role['scope']['entry'] == role['entry']
               for role in definitions)
    assert service.knowledge(pids['supplement.tex'])['registry'] == saved['registry']

    reports = {entry: _run(service, pid, scope='changed') for entry, pid in pids.items()}
    for entry, report in reports.items():
        assert report['evaluation_mode'] == 'full'
        assert report['input']['knowledge_hash'] == reports['main.tex']['input']['knowledge_hash']
        coverage = _coverage(report)
        # The other entry's use/definition must neither count nor degrade this one.
        assert coverage['entry'] == entry and coverage['applicable_count'] == 1
        assert coverage['complete'] and not coverage['reasons']
        findings = [finding for finding in report['findings'] if finding['rule_id'] == RULE]
        if entry == violating_entry:
            assert len(findings) == 1 and findings[0]['lifecycle'] == 'current'
            assert findings[0]['target'] == 'shared-filter:body'
            assert {e['entry'] for e in findings[0]['evidence']} == {entry}
            prefix = entry.removesuffix('.tex')
            assert [e['file'] for e in findings[0]['evidence']] == [f'{prefix}-use.tex', f'{prefix}-definition.tex']
            assert findings[0]['evidence'][1]['quote'] == f'Shared Filter means {meanings[entry]}'
        else:
            assert findings == []  # main's/supplement's violation cannot leak
        assert not service.status(pids[entry])['stale']
    assert {path.name: path.read_bytes() for path in root.glob('*.tex')} == original_files
