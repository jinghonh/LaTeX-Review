"""Explicit finding-to-annotation conversion through the existing annotation store."""
import json
from pathlib import Path

from ..annotations import AnnotationStore, load_content, utf16_length
from ..identity import identity_hash
from .finding_review import fail, fingerprint, validate_evidence


def convert(coordinator, paper_id, finding_key, body, *, page_snapshot):
    allowed = {'input_id', 'evidence_fingerprint', 'page_version', '_annotation_directory'}
    if set(body) - allowed or not allowed <= set(body) or not isinstance(page_snapshot, dict):
        fail('invalid_annotation_conversion', '转入批注需要当前页面、输入及精确证据绑定')
    with coordinator.lock:
        context = coordinator._paper(paper_id)
        report = coordinator.report(paper_id)
        if body['input_id'] != report['input_id'] or not coordinator._fresh(context, report):
            fail('stale_input', '检查输入已变化，请先复检再转为批注', 409)
        finding = next((f for f in report['findings'] if f['finding_key'] == finding_key), None)
        if not finding:
            fail('finding_not_found', '问题不存在', 404)
        if finding.get('lifecycle') != 'current':
            fail('finding_not_current', '只能将当前问题转为批注', 409)
        evidence = finding['evidence']
        if not evidence or body['evidence_fingerprint'] not in (fingerprint(evidence), identity_hash(evidence)):
            fail('evidence_conflict', '转入批注需要当前精确问题证据', 409)
        validate_evidence(context, report, evidence)
        # Only the worker injects this path. The public worker route overwrites
        # browser values; canonical access requires the independent admin token.
        directory = Path(body['_annotation_directory'])
        content = load_content(directory)
        metadata = json.loads((directory / 'checks-page.json').read_text(encoding='utf-8'))
        if (metadata != page_snapshot or metadata.get('page_version') != body['page_version']
                or content['version_id'] != body['page_version'] or content['project_id'] != paper_id
                or content['entry'] != context['paper']['entry'] or metadata.get('content_id') != report['input']['content_id']):
            fail('page_changed', '批注页面与检查输入不同，请刷新或重建预览', 409)
        coordinator.validate_page(paper_id, metadata)
        first = evidence[0]
        document = context['document']
        nodes = {node.review.id: node for node in document.parsed.nodes}
        candidates = []
        for unit in content['units']:
            span = unit.get('source_span', {})
            owner = nodes.get(unit['node_id'])
            if (not span.get('reliable') or not owner or span.get('file') != first['file']
                    or span.get('sha256') != first['file_sha256']
                    or not span['start'] <= first['start'] < first['end'] <= span['end']):
                continue
            origins = document.evidence(owner.expanded_start, owner.expanded_end)
            if any(e['include_instance'] == first['include_instance'] and e['file'] == first['file']
                   and e['start'] <= first['start'] < first['end'] <= e['end'] for e in origins):
                candidates.append(unit)
        if not candidates:
            fail('annotation_anchor_unavailable', '精确证据不能唯一关联现有批注正文单元，请使用源码位置', 409)
        # Prefer the smallest containing existing unit. Never translate raw
        # offsets to displayed offsets: associate the whole trusted unit.
        unit = min(candidates, key=lambda u: (u['source_span']['end'] - u['source_span']['start'], u['order']))
        selection = {'unit_id': unit['id'], 'side': 'new'}
        if unit['kind'] == 'text':
            selection.update(start=0, end=utf16_length(unit['text']), quote=unit['text'])
        association = identity_hash({'paper_id': paper_id, 'finding_key': finding_key,
                                     'rule_version': finding['rule_version'], 'evidence': evidence})
        request_id, tag = 'writing-check-annotation-' + association, 'check-link:' + association
        with AnnotationStore.open(content, context['paper']['root']) as session:
            previous = session.receipt(request_id)
            if previous:
                thread_id = previous.get('result', {}).get('thread_id')
                if not thread_id or not any(t['id'] == thread_id and tag in t.get('tags', []) for t in previous.get('threads', [])):
                    fail('annotation_link_conflict', '批注关联收据冲突，请核对现有批注', 409)
                current = session.read_thread(thread_id)
                if not any(t['id'] == thread_id and not t.get('deleted_at') and tag in t.get('tags', []) for t in current['threads']):
                    fail('annotation_link_unavailable', '关联批注已删除或关联标签已移除；不会重复新建，请核对现有批注', 409)
                return {'annotation_thread_id': thread_id, 'association_id': association, 'reused': True}
            snapshot = session.metadata()
            text = ('用户将当前写作检查转入批注；这不表示问题已解决，也不修改论文。\n'
                    f"规则：{finding['rule_id']}\n问题：{finding['message']}\n"
                    f"检查输入：{report['input_id']}\n问题键：{finding_key}\n\n精确源码证据：\n" +
                    '\n\n'.join(f"{e['entry']} / {e['file']}:{e['start_line']}:{e['start_column']}\n{e['quote']}" for e in evidence))
            request = {'operation': 'create', 'expected_revision': snapshot['revision'], 'request_id': request_id,
                       'selection': selection, 'body': text, 'author': {'type': 'user', 'name': '审阅者（写作检查转入）'},
                       'category': 'question', 'title': '写作检查：' + finding['rule_id'], 'tags': ['writing-check', tag]}
            # Revalidate immediately before the existing store transaction. No
            # finding review/knowledge state or source-apply API is involved.
            coordinator.validate_page(paper_id, metadata)
            validate_evidence(context, report, evidence)
            response = session.execute({'project_id': content['project_id'], 'version_id': content['version_id'], 'request': request}, incremental=True)
            return {'annotation_thread_id': response['result']['thread_id'], 'association_id': association, 'reused': False}
