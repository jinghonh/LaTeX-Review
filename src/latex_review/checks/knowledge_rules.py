"""Conservative deterministic rules over the existing parser/source map.

Only literal registered policies and author-confirmed roles/bindings are facts.
The offset-preserving view reuses the existing scanner; it is not a second tree.
"""
from collections import defaultdict
from hashlib import sha256
import re

from ..rules import _body_range, _rule_mask
from ..structure import _COMMAND, _argument, _math_end
from ..latex_commands import REFERENCE_COMMANDS
from ..text_diff import CITATION_COMMANDS
from .model import make_finding
from .registry import canonical_content_hash, validate_registry

KNOWLEDGE_RULES = ('term.forbidden_name', 'term.code_identifier_in_prose', 'acronym.first_use',
                   'concept.use_before_definition', 'concept.dependency_order', 'symbol.binding_conflict')
# Explicit adapters: only their literal argument text has prose semantics here.
_TEXT = {'textbf', 'textit', 'emph', 'underline', 'textsc', 'textrm', 'textnormal', 'footnote',
         'caption', 'section', 'subsection', 'subsubsection', 'chapter', 'part', 'paragraph',
         'subparagraph', 'texttt', 'title', 'mbox'}
_NONPROSE = {*REFERENCE_COMMANDS, *CITATION_COMMANDS, 'label', 'url', 'path', 'includegraphics',
             'bibliography', 'bibliographystyle', 'addbibresource', 'input', 'include',
             'documentclass', 'usepackage', 'begin', 'end', 'bibitem'}
_DECLARATIONS = {'newcommand': 2, 'renewcommand': 2, 'providecommand': 2, 'newacronym': 3,
                 'DeclareAcronym': 2, 'newglossaryentry': 2}


def _confirmed(item):
    return item.get('status', 'confirmed') == 'confirmed'


_PROSE_EVENT = re.compile(r'[\\\\$]')


def _view(document):
    raw = document.parsed.expanded.text
    masked = _rule_mask(raw)
    chars = list(masked)
    start, end = _body_range(masked)
    contexts = []
    blind = []
    def blank(a, b):
        chars[a:b] = [' ' if c not in '\r\n' else c for c in chars[a:b]]
    blank(0, start)
    blank(end, len(chars))
    for node in document.parsed.nodes:
        if node.review.type in ('inline_math', 'equation', 'bibliography', 'metadata'):
            blank(node.expanded_start, node.expanded_end)
    # Reuse existing inline-math delimiter handling, preserving every source offset.
    index = start
    while index < end:
        event = _PROSE_EVENT.search(masked, index, end)
        if event is None:
            break
        index = event.start()
        if masked[index] == '\\':
            command = _COMMAND.match(masked, index)
            index = command.end() if command else index + 1
        elif masked[index] == '$':
            stop = _math_end(masked, index, end)
            blank(index, stop or end)
            index = stop or end
        else:
            index += 1
    for match in _COMMAND.finditer(masked, start, end):
        if not any(c.strip() for c in chars[match.start():match.end()]):
            continue
        name = match.group(1)
        blank(match.start(), match.end())
        arg = _argument(masked, match.end())
        if name in _DECLARATIONS:
            cursor = match.end()
            for _ in range(_DECLARATIONS[name]):
                argument = _argument(masked, cursor)
                if argument is None:
                    break
                blank(cursor, argument[0])
                cursor = argument[0]
        elif name == 'href':
            if arg:
                blank(match.end(), arg[0])
                # Second argument is visible prose, not the URL target.
        elif name in _NONPROSE:
            if arg:
                blank(match.end(), arg[0])
        elif name in _TEXT:
            if arg:
                kind = 'texttt' if name == 'texttt' else 'caption' if name == 'caption' else 'title' if name in {
                    'title', 'chapter', 'section', 'subsection', 'subsubsection', 'part'} else None
                if kind:
                    contexts.append((arg[2], arg[0] - 1, kind))
        elif name.isalpha() and arg:
            blank(match.end(), arg[0])
            blind.append({'code': 'unsupported_prose_macro', 'message': f'No prose adapter for \\{name}',
                          'file': None})
    return ''.join(chars), contexts, blind


def _context(document, offset, ranges):
    context = next((kind for a, b, kind in reversed(ranges) if a <= offset < b), None)
    if context:
        return context
    owners = [n for n in document.parsed.nodes if n.expanded_start <= offset < n.expanded_end]
    if any(n.review.type == 'abstract' for n in owners):
        return 'abstract'
    if any(n.review.type == 'table' for n in owners):
        return 'table'
    return 'body'


def _unit(document, offset):
    return next((u for u in document.units if u['expanded_start'] <= offset < u['expanded_end']), None)


def _scope_available(scope, document, *, positioned=False):
    """A disappeared anchor is unknown coverage, never an empty clean scope."""
    if not scope or scope.get('entry', document.snapshot.entry) != document.snapshot.entry:
        return True
    domain = scope.get('domain', scope['id'])
    if positioned and domain not in ('project', 'abstract', 'body', 'supplement') and not scope.get('node_id') and not ('start_order' in scope and 'end_order' in scope):
        return False
    if scope.get('node_id') and not any(node.review.id == scope['node_id'] for node in document.parsed.nodes):
        return False
    if 'include_instance' in scope and not any(segment.include_instance == scope['include_instance'] for segment in document.parsed.expanded.source_map.segments):
        return False
    if 'start_order' in scope or 'end_order' in scope:
        count = len(document.units)
        if not 0 <= scope.get('start_order', 0) < scope.get('end_order', count) <= count:
            return False
    return True


def _scope_applies(scope, document, offset, context):
    if not scope:
        return True
    if scope.get('entry', document.snapshot.entry) != document.snapshot.entry:
        return False
    domain = scope.get('domain', scope['id'] if scope['id'] in ('project', 'abstract', 'body', 'supplement') else None)
    owners = [node for node in document.parsed.nodes if node.expanded_start <= offset < node.expanded_end]
    in_abstract = any(node.review.type == 'abstract' for node in owners)
    if (domain == 'abstract' and not in_abstract) or (domain == 'body' and in_abstract):
        return False
    if domain is None and not scope.get('node_id') and not ('start_order' in scope and 'end_order' in scope):
        return False
    if 'start_order' in scope or 'end_order' in scope:
        unit = _unit(document, offset)
        if not unit or unit['reading_order'] < scope.get('start_order', 0) or unit['reading_order'] >= scope.get('end_order', float('inf')):
            return False
    if scope.get('node_id') and not any(node.review.id == scope['node_id'] for node in owners):
        return False
    if 'include_instance' in scope:
        evidence = document.evidence(offset, offset + 1)
        if not evidence or any(item['include_instance'] != scope['include_instance'] for item in evidence):
            return False
    # A named local scope without a current source/node interval is not guessed.
    if scope.get('domain') in ('local', 'proof', 'theorem', 'algorithm', 'section'):
        return bool(scope.get('node_id') or ('start_order' in scope and 'end_order' in scope))
    return True


def _live_evidence(document, evidence):
    """Revalidate bytes/quote and uniquely remap existing source-map fragments."""
    positions = []
    fresh = []
    for e in evidence:
        if e['entry'] != document.snapshot.entry or e['reliability'] != 'exact':
            return None
        raw = document.snapshot.files.get(e['file'])
        if raw is None or sha256(raw).hexdigest() != e['file_sha256']:
            return None
        if raw.decode('utf-8')[e['start']:e['end']] != e['quote']:
            return None
        segments = [s for s in document.parsed.expanded.source_map.segments
                    if s.file == e['file'] and s.include_instance == e['include_instance']
                    and s.source_start <= e['start'] and e['end'] <= s.source_end and s.confidence == 'exact']
        if len(segments) != 1:
            return None
        segment = segments[0]
        start = segment.expanded_start + e['start'] - segment.source_start
        positions.append(start)
        fresh.extend(document.evidence(start, start + e['end'] - e['start']))
    return (min(positions), fresh) if positions else None


def _overlap(a, b):
    if a.get('entry') and b.get('entry') and a['entry'] != b['entry']:
        return False
    if a['id'] == b['id'] or a.get('parent_id') == b['id'] or b.get('parent_id') == a['id']:
        return True
    if a.get('domain') == 'project' or b.get('domain') == 'project':
        return True
    if a.get('domain') in ('abstract', 'body', 'supplement') and b.get('domain') in ('abstract', 'body', 'supplement'):
        return a['domain'] == b['domain']
    if all('start_order' in x and 'end_order' in x for x in (a, b)):
        return max(a['start_order'], b['start_order']) < min(a['end_order'], b['end_order'])
    if a.get('parent_id') and a.get('parent_id') == b.get('parent_id'):
        return False  # Explicit author-declared sibling scopes.
    return None


def evaluate_knowledge(document, registry, *, configuration=None):
    """Return (findings, coverage), independently validating registry contracts."""
    validate_registry(registry)
    config = document.snapshot.configuration if configuration is None else configuration
    checks = config.get('checks', {})
    groups = checks.get('rules', ['references', 'terminology', 'introductions', 'symbols'])
    group_for = lambda rule: 'terminology' if rule.startswith(('term.', 'acronym.')) else 'introductions' if rule.startswith('concept.') else 'symbols'
    enabled = {rule for rule in KNOWLEDGE_RULES if checks.get('enabled', True) and group_for(rule) in groups}
    if not enabled:
        return [], [{'rule_id': rule, 'rule_version': '1.0', 'entry': document.snapshot.entry, 'scope': 'all',
                     'status': 'disabled', 'complete': False, 'applicable_count': 0,
                     'reasons': ['disabled'], 'blind_spots': []} for rule in KNOWLEDGE_RULES]
    view, context_ranges, blind = _view(document)
    for issue in document.parsed.expanded.diagnostics:
        if issue.certainty != 'exact':
            blind.append({'code': issue.code, 'message': issue.message, 'file': issue.file})
    for diagnostic in document.parsed.diagnostics:
        if diagnostic.code in ('unknown_latex', 'plastex_parse_error', 'low_confidence_source'):
            location = diagnostic.source_new or diagnostic.source_old
            blind.append({'code': diagnostic.code, 'message': diagnostic.message,
                          'file': location.file if location else None})
    entry = document.snapshot.entry
    findings, coverage = [], []
    counts = defaultdict(int)
    reasons = defaultdict(set)
    for concept in registry['concepts']:
        if _confirmed(concept) or (concept.get('entries') and entry not in concept['entries']):
            continue
        for field, rule in [('forbidden_names', 'term.forbidden_name'), ('code_identifiers', 'term.code_identifier_in_prose'), ('acronym', 'acronym.first_use')]:
            if concept.get(field):
                reasons[rule].add('stale_concept_knowledge')
    concepts = {x['id']: x for x in registry['concepts'] if _confirmed(x)
                and (not x.get('entries') or entry in x['entries'])}
    for concept in concepts.values():
        for field, rule in [('forbidden_names', 'term.forbidden_name'), ('code_identifiers', 'term.code_identifier_in_prose')]:
            for name in concept.get(field, []):
                policy = {'text': name} if isinstance(name, str) else name
                allowed = policy.get('contexts', concept.get('contexts'))
                if field == 'code_identifiers' and not allowed:
                    # Mapping an internal identifier alone does NOT forbid prose.
                    continue
                allowed = allowed or ['body', 'abstract', 'caption', 'title', 'table']
                counts[rule] += 1
                policy_scope = policy.get('scope', concept.get('scope'))
                if not _scope_available(policy_scope, document, positioned=True):
                    reasons[rule].add('scope_anchor_unavailable')
                    continue
                case_sensitive = policy.get('case_sensitive', concept.get('case_sensitive', True))
                term = re.escape(policy['text'])
                if policy.get('word_boundary', True):
                    term = r'(?<!\w)' + term + r'(?!\w)'
                hits = []
                for hit in re.finditer(term, view, 0 if case_sensitive else re.IGNORECASE):
                    context = _context(document, hit.start(), context_ranges)
                    if context not in allowed:
                        continue
                    if not _scope_applies(policy.get('scope', concept.get('scope')), document, hit.start(), context):
                        continue
                    evidence = document.evidence(hit.start(), hit.end())
                    if not evidence or any(e['reliability'] != 'exact' for e in evidence):
                        reasons[rule].add('source_mapping_uncertain')
                        continue
                    unit = _unit(document, hit.start())
                    hits.append({'entry': entry, 'node_id': unit['node_id'] if unit else None,
                                 'reading_order': hit.start(), 'evidence': evidence})
                if hits:
                    findings.append(make_finding(rule, f"{concept['id']}:{policy['text']}:{concept.get('scope', {}).get('id', 'project')}",
                                                 [e for h in hits for e in h['evidence']], related_occurrences=hits))
        acronym = concept.get('acronym')
        if acronym:
            rule = 'acronym.first_use'
            counts[rule] += 1
            if not _scope_available(concept.get('scope'), document, positioned=True):
                reasons[rule].add('scope_anchor_unavailable')
                continue
            if blind:
                reasons[rule].add('introduction_scope_incomplete')
                continue  # A hidden/unsupported introduction must not become a missing-definition assertion.
            flags = 0 if acronym.get('case_sensitive', True) else re.IGNORECASE
            short = r'(?<!\w)' + re.escape(acronym['short']) + r'(?!\w)'
            introduction = re.compile(re.escape(acronym['expansion']) + r'\s*\(\s*' + re.escape(acronym['short']) + r'\s*\)', flags)
            introductions = list(introduction.finditer(view))
            for domain in ('abstract', 'body'):
                uses = [m for m in re.finditer(short, view, flags)
                        if _context(document, m.start(), context_ranges) == domain
                        and _scope_applies(concept.get('scope'), document, m.start(), domain)]
                if not uses:
                    continue
                first = uses[0]
                if any(m.start() <= first.start() < m.end() for m in introductions):
                    continue
                evidence = document.evidence(first.start(), first.end())
                if evidence and all(e['reliability'] == 'exact' for e in evidence):
                    findings.append(make_finding(rule, f"{concept['id']}:{domain}", evidence))
    # Role facts are author declarations, not string heuristics.
    known_targets = set(concepts) | {item['id'] for item in registry['symbol_bindings']
                                    if _confirmed(item) and item['scope'].get('entry', entry) == entry}
    roles = []
    for occurrence in registry['occurrences']:
        if occurrence['entry'] != entry:
            continue
        if not _confirmed(occurrence) or occurrence['role'] == 'unknown':
            reasons['concept.use_before_definition'].add('unconfirmed_or_unknown_role')
            continue
        if not _scope_available(occurrence['scope'], document):
            reasons['concept.use_before_definition'].add('scope_anchor_unavailable')
            reasons['concept.dependency_order'].add('scope_anchor_unavailable')
            continue
        live = _live_evidence(document, occurrence['evidence'])
        if live is None:
            reasons['concept.use_before_definition'].add('stale_occurrence_evidence')
            reasons['concept.dependency_order'].add('stale_occurrence_evidence')
            continue
        if occurrence['target_id'] not in known_targets:
            reasons['concept.use_before_definition'].add('target_knowledge_missing_or_stale')
            reasons['concept.dependency_order'].add('target_knowledge_missing_or_stale')
            continue
        roles.append({**occurrence, '_order': live[0], '_evidence': live[1]})
    definitions = defaultdict(list)
    for role in roles:
        if role['role'] == 'definition':
            definitions[role['target_id']].append(role)
    for role in roles:
        if role['role'] != 'formal_use':
            continue
        rule = 'concept.use_before_definition'
        counts[rule] += 1
        defs = [d for d in definitions[role['target_id']] if _overlap(d['scope'], role['scope']) is True]
        if not defs:
            reasons[rule].add('confirmed_definition_in_scope_missing')
            continue
        first = min(defs, key=lambda d: d['_order'])
        if role.get('introduction_group') and role['introduction_group'] == first.get('introduction_group'):
            continue
        if role['_order'] < first['_order']:
            findings.append(make_finding(rule, f"{role['target_id']}:{role['scope']['id']}", role['_evidence'] + first['_evidence']))
    for relation in registry['relations']:
        if relation['kind'] != 'depends_on' or relation.get('strength') != 'strong':
            continue
        if relation['status'] == 'stale':
            reasons['concept.dependency_order'].add('stale_relation_knowledge')
            continue
        if relation['status'] != 'confirmed':
            continue
        rule = 'concept.dependency_order'
        counts[rule] += 1
        if not _live_evidence(document, relation['evidence']):
            reasons[rule].add('stale_relation_evidence')
            continue
        dependant = definitions[relation['from_id']]
        prerequisite = definitions[relation['to_id']]
        pairs = [(a, b) for a in dependant for b in prerequisite if _overlap(a['scope'], b['scope']) is True]
        if not pairs:
            reasons[rule].add('confirmed_definitions_missing')
        for a, b in pairs:
            if a['_order'] < b['_order']:
                findings.append(make_finding(rule, relation['id'], a['_evidence'] + b['_evidence']))
                break
    bindings = []
    for binding in registry['symbol_bindings']:
        if binding['scope'].get('entry', entry) != entry:
            continue
        if not _confirmed(binding):
            reasons['symbol.binding_conflict'].add('stale_binding_knowledge')
            continue
        counts['symbol.binding_conflict'] += 1
        if not _scope_available(binding['scope'], document):
            reasons['symbol.binding_conflict'].add('scope_anchor_unavailable')
            continue
        live = _live_evidence(document, binding['evidence'])
        if not live:
            reasons['symbol.binding_conflict'].add('stale_binding_evidence')
            continue
        bindings.append({**binding, '_evidence': live[1]})
    for relation in registry['relations']:
        if relation['kind'] == 'different_concept' and relation['status'] == 'confirmed' and (relation['from_id'] not in concepts or relation['to_id'] not in concepts):
            reasons['symbol.binding_conflict'].add('target_knowledge_missing_or_stale')
    different = {frozenset((r['from_id'], r['to_id'])) for r in registry['relations']
                 if r['kind'] == 'different_concept' and r['status'] == 'confirmed'
                 and r['from_id'] in concepts and r['to_id'] in concepts
                 and _live_evidence(document, r['evidence'])}
    for index, a in enumerate(bindings):
        for b in bindings[index + 1:]:
            if a['symbol'] != b['symbol']:
                continue
            if a.get('shadows') == b['id'] or b.get('shadows') == a['id']:
                continue  # Explicitly confirmed local rebinding is not a simultaneous conflict.
            overlap = _overlap(a['scope'], b['scope'])
            if overlap is None:
                reasons['symbol.binding_conflict'].add('scope_overlap_unknown')
                continue
            incompatible = (b['id'] in a.get('incompatible_with', []) or a['id'] in b.get('incompatible_with', [])
                            or (a.get('concept_id') and b.get('concept_id') and frozenset((a['concept_id'], b['concept_id'])) in different))
            if overlap and incompatible:
                findings.append(make_finding('symbol.binding_conflict', ':'.join(sorted((a['id'], b['id']))), a['_evidence'] + b['_evidence']))
    # Apply only explicit, currently valid local exceptions. Never delete findings.
    for finding in findings:
        for exception in registry['exceptions']:
            if exception.get('status', 'active') != 'active' or exception['rule_id'] != finding['rule_id']:
                continue
            if exception.get('finding_key') and exception['finding_key'] != finding['finding_key']:
                continue
            if exception.get('object_id') and not finding['target'].startswith(exception['object_id'] + ':'):
                continue
            valid = exception['valid_when']
            if any(key not in ('rule_version', 'knowledge_hash') for key in valid):
                continue  # Input/evidence fingerprint requires the service's current CheckInput.
            if valid.get('rule_version', '1.0') != '1.0' or valid.get('knowledge_hash', canonical_content_hash(registry)) != canonical_content_hash(registry):
                continue
            evidence = exception.get('evidence', [])
            if evidence and not _live_evidence(document, evidence):
                continue
            if not exception.get('finding_key') and not exception.get('object_id') and not evidence:
                continue  # Never waive a whole rule with an unbounded exception.
            locations = [_live_evidence(document, [fragment]) for fragment in finding['evidence']]
            all_in_scope = locations and all(mapped and _scope_applies(
                exception['scope'], document, mapped[0], _context(document, mapped[0], context_ranges)) for mapped in locations)
            # A single-position exception must not waive other hits of a grouped finding.
            covered = not evidence or all(any(e['file'] == f['file'] and e['include_instance'] == f['include_instance']
                and e['start'] <= f['start'] and f['end'] <= e['end'] for e in evidence) for f in finding['evidence'])
            if all_in_scope and covered:
                finding['review_state'] = 'waived'
    for rule in KNOWLEDGE_RULES:
        if rule not in enabled:
            coverage.append({'rule_id': rule, 'rule_version': '1.0', 'entry': entry, 'scope': 'all',
                             'status': 'disabled', 'complete': False, 'applicable_count': counts[rule],
                             'reasons': ['disabled'], 'blind_spots': []})
            continue
        rule_blind = list(blind) if counts[rule] and rule.startswith(('term.', 'acronym.')) else []
        why = sorted(reasons[rule])
        if rule_blind:
            why.append('unsupported_prose_macro')
        coverage.append({'rule_id': rule, 'rule_version': '1.0', 'entry': entry, 'scope': 'all',
                         'status': 'unsupported' if rule_blind else 'prerequisite_missing' if why else 'executed' if counts[rule] else 'no_applicable_objects',
                         'complete': not why, 'applicable_count': counts[rule], 'reasons': why, 'blind_spots': rule_blind})
    # Multiple confirmed uses/dependencies can yield the same logical finding.
    grouped = {}
    for finding in findings:
        if finding['rule_id'] not in enabled:
            continue
        if finding['finding_key'] not in grouped:
            grouped[finding['finding_key']] = finding
        else:
            existing = grouped[finding['finding_key']]
            for evidence in finding['evidence']:
                if evidence not in existing['evidence']:
                    existing['evidence'].append(evidence)
    return sorted(grouped.values(), key=lambda x: (x['rule_id'], x['finding_key'])), coverage
