"""Exact-source validation and explicit, input-bound human finding decisions."""
from hashlib import sha256
import json
from pathlib import Path

from ..identity import identity_hash


def fail(code, message, status=422):
    from .service import CheckError
    raise CheckError(code, message, status)


def fingerprint(evidence):
    return sha256(json.dumps(evidence, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_evidence(context, report, evidence, _raw=None):
    """Check actual immutable source bytes, not author-supplied hashes/locations."""
    if not isinstance(evidence, list):
        fail('invalid_evidence', '证据必须为片段列表')
    cache = {} if _raw is None else _raw
    root = Path(context['paper']['root'])
    known = report['input']['files']
    for item in evidence:
        if not isinstance(item, dict):
            fail('invalid_evidence', '证据片段无效')
        # Definitions may carry an explicit role wrapper with source evidence.
        if 'evidence' in item and 'file' not in item:
            validate_evidence(context, report, item['evidence'], cache)
            continue
        document = context.get('document')
        if not document or document.snapshot.content_id != report['input']['content_id']:
            fail('evidence_unavailable', '请运行当前检查以验证包含实例和源码映射', 409)
        name = item.get('file')
        if not isinstance(name, str) or name not in known or item.get('entry') != report['input']['entry'] or item.get('content_id') != report['input']['content_id']:
            fail('invalid_evidence', '证据不属于当前论文快照', 409)
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()):
            fail('invalid_evidence', '证据路径超出论文范围', 403)
        if name not in cache:
            cache[name] = target.read_bytes()
        raw = cache[name]
        start, end = item.get('start'), item.get('end')
        if (sha256(raw).hexdigest() != known[name] or item.get('file_sha256') != known[name]
                or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw.decode('utf-8'))
                or raw.decode('utf-8')[start:end] != item.get('quote') or item.get('reliability') != 'exact'):
            fail('invalid_evidence', '证据原文、哈希或 Unicode 区间不符', 409)
        source_map = document.parsed.expanded.source_map
        start_line, start_column = source_map._line_column(name, start)
        end_line, end_column = source_map._line_column(name, end)
        if any(item.get(k) != v for k, v in (('start_line', start_line), ('start_column', start_column),
                                             ('end_line', end_line), ('end_column', end_column))):
            fail('invalid_evidence', '证据行列坐标与 Unicode 区间不符', 409)
        if not any(m.file == name and m.include_instance == item.get('include_instance')
                   and m.source_start <= start and end <= m.source_end and m.confidence == 'exact'
                   for m in source_map.segments):
            fail('invalid_evidence', '证据包含实例或源码映射不符', 409)


def apply_exceptions(report, registry, document):
    """Reapply only exact condition-bound author waivers to a fresh run."""
    from .knowledge_rules import _live_evidence, _scope_applies, _context
    for finding in report['findings']:
        if finding['lifecycle'] != 'current':
            continue
        for exception in registry['exceptions']:
            valid = exception.get('valid_when', {})
            if (exception.get('status', 'active') != 'active' or exception.get('finding_key') != finding['finding_key']
                    or exception['rule_id'] != finding['rule_id'] or valid.get('rule_version') != finding['rule_version']
                    or valid.get('evidence_fingerprint') != identity_hash(finding['evidence'])
                    or set(valid) - {'evidence_fingerprint', 'rule_version'}):
                continue
            mapped = [_live_evidence(document, [e]) for e in finding['evidence']]
            if (mapped and all(m and _scope_applies(exception['scope'], document, m[0], _context(document, m[0], [])) for m in mapped)
                    and _live_evidence(document, exception.get('evidence', []))):
                finding['review_state'] = 'waived'
    return report


def decide(coordinator, paper_id, finding_key, body):
    context = coordinator._paper(paper_id)
    fields = {'input_id', 'request_id', 'action', 'reason', 'evidence_fingerprint', 'scope', 'author', 'expected_source'}
    if set(body) - fields or any(not isinstance(body.get(k), str) or not body[k] or len(body[k]) > 8192
                                for k in ('input_id', 'request_id', 'action', 'reason', 'evidence_fingerprint')):
        fail('invalid_decision', '问题决定缺少字段或字段超限')
    if body['action'] not in ('confirm', 'reject', 'waive', 'defer') or not isinstance(body.get('scope'), dict):
        fail('invalid_decision', '动作必须为 confirm/reject/waive/defer，且须明确 scope')
    from .registry import _SCHEMA
    from jsonschema import Draft202012Validator
    if (not Draft202012Validator(_SCHEMA['$defs']['scope']).is_valid(body['scope'])
            or not isinstance(body.get('author', 'author'), str) or not body.get('author', 'author')
            or len(body.get('author', 'author')) > 256):
        fail('invalid_decision', '作者或 scope 字段无效')
    value = {**body, 'finding_key': finding_key, 'author': body.get('author', 'author')}
    journal = context['service_state']
    with coordinator.lock:
        previous = journal.review(body['request_id'])
        if previous:
            if previous != value:
                fail('idempotency_conflict', '请求 ID 已用于不同决定', 409)
            return {'decision': previous, 'replayed': True}
        if body['action'] == 'waive':
            coordinator._expected_source(coordinator._knowledge(context), body)
            registry = coordinator._knowledge(context).read()['registry']
            audited = next((d for d in registry['decisions'] if d.get('request_id') == body['request_id']), None)
            if audited:
                after = audited.get('after') or {}
                if (audited.get('action') != 'waive' or audited.get('reason') != body['reason']
                        or audited.get('scope') != body['scope'] or audited.get('input_id') != body['input_id']
                        or audited.get('author') != value['author'] or after.get('finding_key') != finding_key
                        or body['evidence_fingerprint'] not in (fingerprint(after.get('evidence', [])), identity_hash(after.get('evidence', [])))):
                    fail('idempotency_conflict', '请求 ID 已用于不同决定', 409)
                journal.save_review(value)
                return {'decision': value, 'replayed': True}
        report = coordinator.report(paper_id)
        if body['input_id'] != report['input_id'] or not coordinator._fresh(context, report):
            fail('stale_input', '问题证据已过期，请复检后决定', 409)
        finding = next((f for f in report['findings'] if f['finding_key'] == finding_key), None)
        if not finding:
            fail('finding_not_found', '问题不存在', 404)
        if finding.get('lifecycle') != 'current':
            fail('finding_not_current', '历史或已解决问题不能接受当前作者决定，请复检', 409)
        evidence = finding['evidence']
        if body['evidence_fingerprint'] not in (fingerprint(evidence), identity_hash(evidence)):
            fail('evidence_conflict', '问题证据指纹不符', 409)
        validate_evidence(context, report, evidence)
        if not evidence and body['action'] in ('confirm', 'waive'):
            fail('evidence_required', '确认或豁免需要精确源码证据', 409)
        if evidence:
            from .knowledge_rules import _live_evidence, _scope_applies, _context
            document = context['document']
            mapped = [_live_evidence(document, [e]) for e in evidence]
            if not all(m and _scope_applies(body['scope'], document, m[0], _context(document, m[0], [])) for m in mapped):
                fail('scope_conflict', '决定的 scope 未覆盖问题全部证据', 409)
        if body['action'] == 'waive':
            store = coordinator._knowledge(context)
            current = store.read()
            exception_id = 'finding-' + identity_hash({'request_id': body['request_id'], 'paper_id': paper_id})
            # Registry decides and backs up atomically. An interrupted retry can
            # recognize the same audited exception before persisting its view.
            if not any(d.get('request_id') == body['request_id'] for d in current['registry']['decisions']):
                exception = {'id': exception_id, 'rule_id': finding['rule_id'], 'finding_key': finding_key,
                             'scope': body['scope'], 'evidence': evidence, 'reason': body['reason'], 'status': 'active',
                             'valid_when': {'evidence_fingerprint': identity_hash(evidence), 'rule_version': finding['rule_version']},
                             'invalid_when': ['source_evidence_changed', 'rule_version_changed', 'scope_changed']}
                store.decide('exceptions', exception_id, exception, action='waive', reason=body['reason'],
                             author=value['author'], evidence_fingerprint=identity_hash(evidence), scope=body['scope'],
                             input_id=report['input_id'], request_id=body['request_id'],
                             expected_revision=current['revision'], expected_hash=current['content_hash'])
            context['generation'] += 1
        journal.save_review(value)
        result = next(f for f in coordinator.report(paper_id)['findings'] if f['finding_key'] == finding_key)
        return {'decision': value, 'finding': result, 'replayed': False}
