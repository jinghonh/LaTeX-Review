"""Short-lived batch plans, guarded by the existing project lock."""
import json
import secrets
import time

from ._validation import AnnotationConflict, digest

TTL = 15 * 60
FORMAT_VERSION = 1


def content_hash(store, content):
    if getattr(store, '_prepared_content', None) is content:
        return store._prepared_content_hash
    return digest(content)


def load(store, content, request, revision, *, transaction):
    payload = transaction.preflight(request['request_id'])
    if payload is None:
        if request.get('preflight_token'):
            raise AnnotationConflict('预检不存在，请重新预检',
                {'code': 'preflight_missing', 'reasons': ['cache_missing'],
                 'expected_revision': request['expected_revision'], 'actual_revision': revision})
        return None
    try:
        plan = json.loads(payload)
        if (not isinstance(plan, dict) or type(plan.get('format_version')) is not int
                or plan['format_version'] != FORMAT_VERSION
                or not {'token', 'revision', 'content_hash', 'expires_at', 'version_id',
                        'payload_hash', 'threads', 'results'} <= plan.keys()):
            raise ValueError('预检格式已失效')
    except (ValueError, TypeError) as exc:
        raise AnnotationConflict('预检缓存无效，请重新预检',
            {'code': 'preflight_invalid', 'reasons': ['cache_invalid'],
             'expected_revision': request['expected_revision'], 'actual_revision': revision}) from exc
    reasons = []
    if plan['revision'] != revision:
        reasons.append('annotation_revision_changed')
    if plan['content_hash'] != content_hash(store, content):
        reasons.append('content_changed')
    if plan['version_id'] != content['version_id']:
        reasons.append('version_changed')
    payload_hash = digest({'project_id': content['project_id'], 'items': request['items'],
                          'atomic': request.get('atomic', False)})
    if plan['payload_hash'] != payload_hash:
        reasons.append('payload_changed')
    if plan['expires_at'] <= time.time():
        reasons.append('expired')
    if request.get('preflight_token') and request['preflight_token'] != plan['token']:
        reasons.append('token_mismatch')
    if reasons:
        labels = {'annotation_revision_changed': '批注修订号变化', 'content_changed': '正文变化',
                  'version_changed': '正文版本变化', 'payload_changed': '载荷变化',
                  'expired': '缓存过期', 'token_mismatch': '令牌不匹配'}
        raise AnnotationConflict('预检已失效：' + '、'.join(labels[reason] for reason in reasons) + '；请重新预检',
            {'code': 'preflight_stale', 'reasons': reasons,
             'expected_revision': plan['revision'], 'actual_revision': revision,
             'request_expected_revision': request['expected_revision'],
             'expected_version_id': plan['version_id'], 'actual_version_id': content['version_id'],
             'expires_at': plan['expires_at']})
    return plan


def save(store, content, request, revision, changed_threads, results, *, transaction):
    plan = {'format_version': FORMAT_VERSION, 'token': secrets.token_urlsafe(24), 'revision': revision,
            'content_hash': content_hash(store, content), 'expires_at': time.time() + TTL,
            'version_id': content['version_id'],
            'payload_hash': digest({'project_id': content['project_id'], 'items': request['items'],
                                   'atomic': request.get('atomic', False)}),
            'threads': changed_threads, 'results': results}
    transaction.save_preflight(request['request_id'], plan)
    return plan
