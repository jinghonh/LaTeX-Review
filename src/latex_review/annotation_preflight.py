"""Short-lived batch plans, guarded by the existing project lock."""
import hashlib
import json
import secrets
import time

from .annotations import AnnotationConflict, AnnotationError, _atomic_write, _hash

TTL = 15 * 60


def path_for(store, request_id):
    name = hashlib.sha256(request_id.encode()).hexdigest()
    path = store.directory / f'{store.project_id}.preflight-{name}.json'
    if path.is_symlink():
        raise AnnotationError('预检缓存不可为符号链接')
    return path


def load(store, content, request, fingerprint, revision):
    path = path_for(store, request['request_id'])
    if not path.exists():
        if request.get('preflight_token'):
            raise AnnotationConflict('预检不存在，请重新预检')
        return None
    try:
        plan = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise AnnotationConflict('预检缓存无效，请重新预检') from exc
    if (plan['fingerprint'] != fingerprint or plan['revision'] != revision
            or plan['content_hash'] != _hash(content)
            or plan['expires_at'] <= time.time()
            or (request.get('preflight_token') and request['preflight_token'] != plan['token'])):
        raise AnnotationConflict('预检已失效：正文、批注修订号、载荷或有效期变化，请重新预检')
    return plan


def save(store, content, request, fingerprint, revision, changed_threads, results):
    plan = {'token': secrets.token_urlsafe(24), 'fingerprint': fingerprint, 'revision': revision,
            'content_hash': _hash(content), 'expires_at': time.time() + TTL,
            'threads': changed_threads, 'results': results}
    _atomic_write(path_for(store, request['request_id']), plan)
    return plan
