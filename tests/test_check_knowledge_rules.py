from copy import deepcopy

import pytest

from latex_review.checks.document import build_check_document
from latex_review.checks.knowledge_rules import evaluate_knowledge
from latex_review.checks.registry import empty_registry
from latex_review.checks.snapshot import capture_snapshot


def _document(tmp_path, body, preamble=''):
    path = tmp_path / 'main.tex'
    path.write_text('\\documentclass{article}\n' + preamble + '\n\\begin{document}\n' + body + '\n\\end{document}\n')
    return build_check_document(capture_snapshot(path, root=tmp_path))


def _evidence(document, text, occurrence=0):
    raw = document.parsed.expanded.text
    start = -1
    for _ in range(occurrence + 1):
        start = raw.index(text, start + 1)
    return document.evidence(start, start + len(text))


def _role(document, id, target, text, role, scope=None, **extra):
    return {'id': id, 'target_id': target, 'role': role, 'entry': 'main.tex',
            'scope': scope or {'id': 'body', 'entry': 'main.tex', 'domain': 'body'},
            'source': 'manual', 'evidence': _evidence(document, text), **extra}


def test_term_contexts_exclude_comment_url_verbatim_math_and_keys(tmp_path):
    document = _document(tmp_path, r'''
Forbidden in prose; notForbidden is another word.
% Forbidden comment
\verb|Forbidden| \url{https://Forbidden.example} \cite{Forbidden}
\begin{verbatim}Forbidden\end{verbatim}
$Forbidden$ \(Forbidden\) \texttt{Forbidden}
\begin{figure}\caption{Forbidden in caption}\end{figure}
''')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'concept', 'canonical_name': 'Good',
                             'forbidden_names': [{'text': 'Forbidden', 'contexts': ['body', 'caption']}]}]
    findings, coverage = evaluate_knowledge(document, registry)
    assert len(findings) == 1
    assert len(findings[0]['related_occurrences']) == 2
    assert [e['quote'] for e in findings[0]['evidence']] == ['Forbidden', 'Forbidden']
    # Parser diagnostics remain explicit even where positive literal hits are reliable.
    assert coverage[0]['status'] == 'unsupported'
    assert coverage[0]['blind_spots']
    assert all(c['complete'] for c in coverage[1:])


def test_identifier_mapping_is_not_automatically_forbidden_and_texttt_is_explicit(tmp_path):
    document = _document(tmp_path, r'Internal spectral_mixer and \texttt{spectral_mixer}.')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'module', 'canonical_name': 'Spectral mixing', 'code_identifiers': ['spectral_mixer']}]
    assert evaluate_knowledge(document, registry)[0] == []
    registry['concepts'][0]['code_identifiers'] = [{'text': 'spectral_mixer', 'contexts': ['body', 'texttt']}]
    findings, _ = evaluate_knowledge(document, registry)
    assert findings[0]['rule_id'] == 'term.code_identifier_in_prose'
    assert len(findings[0]['related_occurrences']) == 2
    registry['exceptions'] = [{'id': 'implementation', 'rule_id': 'term.code_identifier_in_prose',
                               'object_id': 'module', 'scope': {'id': 'project', 'domain': 'project'},
                               'reason': 'Implementation discussion', 'valid_when': {'rule_version': '1.0'},
                               'invalid_when': ['rule_changed']}]
    findings, _ = evaluate_knowledge(document, registry)
    assert findings[0]['review_state'] == 'waived'
    assert findings[0]['lifecycle'] == 'current'  # Waived is NOT resolved.


def test_acronym_abstract_and_body_introductions_are_independent(tmp_path):
    document = _document(tmp_path, r'\begin{abstract}Spectral Mixing (SM) is useful.\end{abstract}' '\nSM is used in the body.')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'sm', 'canonical_name': 'Spectral Mixing', 'acronym': {'short': 'SM', 'expansion': 'Spectral Mixing'}}]
    findings, _ = evaluate_knowledge(document, registry)
    assert [(f['rule_id'], f['target']) for f in findings] == [('acronym.first_use', 'sm:body')]
    document = _document(tmp_path, r'\begin{abstract}Spectral Mixing (SM).\end{abstract}' '\nSpectral Mixing (SM) is used.')
    assert evaluate_knowledge(document, registry)[0] == []


def test_declaration_does_not_introduce_acronym_to_reader(tmp_path):
    document = _document(tmp_path, 'SM is used.', preamble=r'\newcommand{\SM}{Spectral Mixing}')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'sm', 'canonical_name': 'Spectral Mixing', 'acronym': {'short': 'SM', 'expansion': 'Spectral Mixing'}}]
    findings, _ = evaluate_knowledge(document, registry)
    assert findings[0]['rule_id'] == 'acronym.first_use'


def test_confirmed_role_order_not_filename_or_stored_order_and_previews_legal(tmp_path):
    document = _document(tmp_path, 'Preview here. Formal use here. Definition here.')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'module', 'canonical_name': 'Module'}]
    registry['occurrences'] = [_role(document, 'preview', 'module', 'Preview', 'preview'),
                               _role(document, 'use', 'module', 'Formal use', 'formal_use', reading_order=999),
                               _role(document, 'def', 'module', 'Definition', 'definition', reading_order=0)]
    findings, _ = evaluate_knowledge(document, registry)
    assert len(findings) == 1
    assert findings[0]['rule_id'] == 'concept.use_before_definition'
    assert [e['quote'] for e in findings[0]['evidence']] == ['Formal use', 'Definition']
    registry['concepts'].clear()
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings and 'target_knowledge_missing_or_stale' in coverage[3]['reasons']
    registry['concepts'] = [{'id': 'module', 'canonical_name': 'Module'}]
    registry['occurrences'][1]['role'] = 'mention'
    assert evaluate_knowledge(document, registry)[0] == []


def test_introduction_group_after_formula_not_automatically_use_before_definition(tmp_path):
    document = _document(tmp_path, 'Formal use here. Definition here.')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'module', 'canonical_name': 'Module'}]
    registry['occurrences'] = [_role(document, 'use', 'module', 'Formal use', 'formal_use', introduction_group='group'),
                               _role(document, 'def', 'module', 'Definition', 'definition', introduction_group='group')]
    assert evaluate_knowledge(document, registry)[0] == []


def test_stale_or_unknown_roles_do_not_become_facts(tmp_path):
    document = _document(tmp_path, 'Formal use here. Definition here.')
    registry = empty_registry()
    registry['occurrences'] = [_role(document, 'use', 'module', 'Formal use', 'unknown'),
                               _role(document, 'def', 'module', 'Definition', 'definition')]
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings
    assert 'unconfirmed_or_unknown_role' in next(c for c in coverage if c['rule_id'] == 'concept.use_before_definition')['reasons']
    registry['occurrences'][0]['role'] = 'formal_use'
    registry['occurrences'][0]['evidence'][0]['file_sha256'] = '0' * 64
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings
    assert any('stale_occurrence_evidence' in c['reasons'] for c in coverage)


def test_confirmed_strong_dependency_only(tmp_path):
    document = _document(tmp_path, 'Dependent definition. Prerequisite definition.')
    registry = empty_registry()
    registry['concepts'] = [{'id': key, 'canonical_name': key} for key in ('dependent', 'prerequisite')]
    registry['occurrences'] = [_role(document, 'a', 'dependent', 'Dependent', 'definition'),
                               _role(document, 'b', 'prerequisite', 'Prerequisite', 'definition')]
    registry['relations'] = [{'id': 'dependency', 'kind': 'depends_on', 'from_id': 'dependent', 'to_id': 'prerequisite',
                              'status': 'confirmed', 'strength': 'strong', 'evidence': _evidence(document, 'Dependent')}]
    findings, _ = evaluate_knowledge(document, registry)
    assert [f['rule_id'] for f in findings] == ['concept.dependency_order']
    registry['relations'][0]['strength'] = 'weak'
    assert not evaluate_knowledge(document, registry)[0]
    registry['relations'][0].update(strength='strong', status='candidate')
    assert not evaluate_knowledge(document, registry)[0]


def test_symbol_confirmed_incompatibility_and_disjoint_scopes(tmp_path):
    document = _document(tmp_path, 'First binding x. Second binding x.')
    scope = {'id': 'proof-1', 'entry': 'main.tex', 'domain': 'proof', 'parent_id': 'body'}
    registry = empty_registry()
    registry['symbol_bindings'] = [
        {'id': 'first', 'symbol': 'x', 'meaning': 'vector', 'scope': scope,
         'evidence': _evidence(document, 'First binding'), 'incompatible_with': ['second']},
        {'id': 'second', 'symbol': 'x', 'meaning': 'scalar', 'scope': deepcopy(scope),
         'evidence': _evidence(document, 'Second binding')}]
    findings, _ = evaluate_knowledge(document, registry)
    assert [f['rule_id'] for f in findings] == ['symbol.binding_conflict']
    registry['symbol_bindings'][1]['shadows'] = 'first'
    assert not evaluate_knowledge(document, registry)[0]
    registry['symbol_bindings'][1].pop('shadows')
    registry['symbol_bindings'][1]['scope']['id'] = 'proof-2'
    assert not evaluate_knowledge(document, registry)[0]
    registry['symbol_bindings'][1]['scope']['id'] = 'proof-1'
    registry['symbol_bindings'][0].pop('incompatible_with')
    assert not evaluate_knowledge(document, registry)[0]  # Free-text types do not prove incompatibility.


def test_unknown_macro_arguments_are_not_guessed_prose(tmp_path):
    document = _document(tmp_path, r'\mystery{Forbidden}')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'x', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden']}]
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings
    term = next(c for c in coverage if c['rule_id'] == 'term.forbidden_name')
    assert term['status'] == 'unsupported' and not term['complete']


@pytest.mark.parametrize('checks', [{'enabled': False}, {'rules': []}, {'rules': ['references']}, {'rules': ['introductions', 'symbols']}])
def test_disabled_knowledge_rules_never_emit_findings(tmp_path, checks):
    document = _document(tmp_path, 'Forbidden')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden']}]
    findings, coverage = evaluate_knowledge(document, registry, configuration={'checks': checks})
    assert findings == []
    terminology = [row for row in coverage if row['rule_id'].startswith(('term.', 'acronym.'))]
    assert all(row['status'] == 'disabled' and row['reasons'] == ['disabled'] for row in terminology)


def test_term_scope_intervals_use_unit_reading_order_not_character_offsets(tmp_path):
    document = _document(tmp_path, 'Forbidden in first paragraph.\n\nForbidden in second paragraph.')
    second = document.parsed.expanded.text.index('Forbidden in second')
    unit = next(row for row in document.units if row['expanded_start'] <= second < row['expanded_end'])
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden'],
        'scope': {'id': 'one-unit', 'domain': 'local', 'start_order': unit['reading_order'], 'end_order': unit['reading_order'] + 1}}]
    findings, _ = evaluate_knowledge(document, registry)
    assert len(findings) == 1 and len(findings[0]['related_occurrences']) == 1
    assert findings[0]['evidence'][0]['start'] == second


def test_term_scope_matches_containing_node_and_include_instance(tmp_path):
    document = _document(tmp_path, r'\begin{figure}Forbidden in prose.\caption{Forbidden in caption}\end{figure}')
    parent = next(node for node in document.parsed.nodes if node.review.type == 'figure')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden'],
        'scope': {'id': 'figure-local', 'domain': 'local', 'node_id': parent.review.id}}]
    findings, _ = evaluate_knowledge(document, registry)
    assert len(findings) == 1 and len(findings[0]['related_occurrences']) == 2
    registry['concepts'][0]['scope']['include_instance'] = 'other-include-instance'
    assert not evaluate_knowledge(document, registry)[0]


def test_body_scope_includes_caption_context_but_excludes_abstract(tmp_path):
    document = _document(tmp_path, r'\begin{abstract}Forbidden\end{abstract}\begin{figure}\caption{Forbidden}\end{figure}')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden'],
                             'scope': {'id': 'body', 'domain': 'body'}}]
    findings, _ = evaluate_knowledge(document, registry)
    assert len(findings) == 1 and len(findings[0]['related_occurrences']) == 1
    assert findings[0]['evidence'][0]['start'] == document.parsed.expanded.text.rindex('Forbidden')


def test_disappeared_scope_and_stale_knowledge_are_not_clean_coverage(tmp_path):
    document = _document(tmp_path, 'Forbidden')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden'],
                             'scope': {'id': 'old', 'domain': 'local', 'node_id': 'deleted-node'}}]
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings and not coverage[0]['complete']
    assert 'scope_anchor_unavailable' in coverage[0]['reasons']
    registry['concepts'][0]['status'] = 'stale'
    findings, coverage = evaluate_knowledge(document, registry)
    assert not findings and not coverage[0]['complete']
    assert 'stale_concept_knowledge' in coverage[0]['reasons']


def test_acronym_checks_obey_registered_scope(tmp_path):
    document = _document(tmp_path, r'\begin{abstract}SM\end{abstract} Spectral Mixing (SM).')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'sm', 'canonical_name': 'Spectral Mixing', 'acronym': {'short': 'SM', 'expansion': 'Spectral Mixing'},
                             'scope': {'id': 'body', 'domain': 'body'}}]
    assert not evaluate_knowledge(document, registry)[0]


def test_default_and_explicit_terminology_groups_are_enabled(tmp_path):
    document = _document(tmp_path, 'Forbidden')
    registry = empty_registry()
    registry['concepts'] = [{'id': 'term', 'canonical_name': 'Good', 'forbidden_names': ['Forbidden']}]
    default, _ = evaluate_knowledge(document, registry)
    explicit, coverage = evaluate_knowledge(document, registry, configuration={'checks': {'rules': ['terminology']}})
    assert default == explicit and default[0]['rule_id'] == 'term.forbidden_name'
    assert all(row['status'] == 'disabled' for row in coverage if row['rule_id'].startswith(('concept.', 'symbol.')))
