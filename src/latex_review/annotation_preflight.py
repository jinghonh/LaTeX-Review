"""Short-lived batch plans, guarded by the existing project lock."""
import json
import secrets
import time

from .annotations import AnnotationConflict, _hash

TTL = 15 * 60


def content_hash(store, content):
    if getattr(store, '_prepared_content', None) is content:
        return store._prepared_content_hash
    return _hash(content)


def load(store, content, request, fingerprint, revision, *, connection):
    row = connection.execute('SELECT payload FROM preflights WHERE id=?', (request['request_id'],)).fetchone()
    if row is None:
        if request.get('preflight_token'):
            raise AnnotationConflict('预检不存在，请重新预检',
                {'code': 'preflight_missing', 'reasons': ['cache_missing'],
                 'expected_revision': request['expected_revision'], 'actual_revision': revision})
        return None
    try:
        plan = json.loads(row[0])
    except (ValueError, TypeError) as exc:
        raise AnnotationConflict('预检缓存无效，请重新预检',
            {'code': 'preflight_invalid', 'reasons': ['cache_invalid'],
             'expected_revision': request['expected_revision'], 'actual_revision': revision}) from exc
    reasons = []
    if plan['revision'] != revision:
        reasons.append('annotation_revision_changed')
    if plan['content_hash'] != content_hash(store, content):
        reasons.append('content_changed')
    if plan.get('version_id', content['version_id']) != content['version_id']:
        reasons.append('version_changed')
    payload_hash = _hash({'project_id': content['project_id'], 'items': request['items'],
                          'atomic': request.get('atomic', False)})
    if (plan.get('payload_hash') is not None and plan['payload_hash'] != payload_hash
            or plan.get('payload_hash') is None and plan['fingerprint'] != fingerprint):
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
             'expected_version_id': plan.get('version_id'), 'actual_version_id': content['version_id'],
             'expires_at': plan['expires_at']})
    return plan


def save(store, content, request, fingerprint, revision, changed_threads, results, *, connection):
    plan = {'token': secrets.token_urlsafe(24), 'fingerprint': fingerprint, 'revision': revision,
            'content_hash': content_hash(store, content), 'expires_at': time.time() + TTL,
            'version_id': content['version_id'],
            'payload_hash': _hash({'project_id': content['project_id'], 'items': request['items'],
                                   'atomic': request.get('atomic', False)}),
            'threads': changed_threads, 'results': results}
    from .annotation_storage import encode
    connection.execute('INSERT INTO preflights(id,payload) VALUES(?,?) '
        'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (request['request_id'], encode(plan)))
    return plan
