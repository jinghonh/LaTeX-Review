"""Small HTTP adapters for canonical entry routes and authenticated worker bridge."""
import json
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from ..identity import paper_identity
from ..local_protocol import HEADER_TOKEN, MAX_BODY, LocalError
from ..local_security import local_request
from .service import CheckError

PAPER_HEADER = 'X-LaTeX-Review-Paper-ID'
VERSION_HEADER = 'X-LaTeX-Review-Page-Version'
CONTENT_HEADER = 'X-LaTeX-Review-Checks-Content-ID'
MAX_RESPONSE = 8 * 1024 * 1024


def error_value(exc):
    if isinstance(exc, CheckError):
        return exc.to_dict(), exc.status
    code = getattr(exc, 'code', 'invalid_request')
    status = 409 if code in ('knowledge_conflict', 'knowledge_source_conflict') else getattr(exc, 'status', 422)
    return {**getattr(exc, 'details', {}), 'code': code, 'message': str(exc),
            'action': 'refresh' if status == 409 else 'correct_request'}, status


def read_body(handler):
    if handler.headers.get('Transfer-Encoding'):
        raise CheckError('request_too_large', '不支持分块请求', 413)
    if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
        raise CheckError('content_type', '需要 application/json')
    length = int(handler.headers.get('Content-Length', '0'))
    if not 0 < length <= MAX_BODY:
        raise CheckError('request_too_large', '请求超限', 413)
    value = json.loads(handler.rfile.read(length))
    if not isinstance(value, dict):
        raise CheckError('invalid_body', '请求必须为 JSON 对象')
    return value


def bounded_send(send, value, status=200):
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RESPONSE:
        raise CheckError('response_too_large', '响应超限，请缩小查询范围', 413)
    return send(value, status)


def dispatch(coordinator, paper_id, suffix, method, query, body=None):
    body = body or {}
    one = lambda key, default=None: query.get(key, [default])[0]
    page_snapshot = None
    if method == 'POST' and '_page_snapshot' in body:
        body = dict(body)
        page_snapshot = body.pop('_page_snapshot')
        if not isinstance(page_snapshot, dict) or not isinstance(page_snapshot.get('page_version'), str) or not page_snapshot['page_version']:
            raise CheckError('page_snapshot_unavailable', '页面检查快照缺少版本绑定，请重建预览', 409)
        if body.get('page_version', page_snapshot['page_version']) != page_snapshot['page_version']:
            raise CheckError('page_changed', '请求页面与检查快照版本不一致，请刷新', 409, action='reload')
        # Cancellation cannot promote data and remains usable after source edits.
        if not suffix.endswith('/cancel'):
            coordinator.validate_page(paper_id, page_snapshot)
    if method == 'GET' and suffix == 'status':
        return coordinator.status(paper_id), 200
    if method == 'POST' and suffix == 'runs':
        return coordinator.start(paper_id, body, page_snapshot=page_snapshot), 202
    parts = suffix.split('/')
    if len(parts) == 2 and parts[0] == 'runs' and method == 'GET':
        return coordinator.run(paper_id, parts[1]), 200
    if len(parts) == 3 and parts[0] == 'runs' and parts[2] == 'cancel' and method == 'POST':
        return coordinator.cancel(paper_id, parts[1]), 200
    if suffix == 'knowledge/evidence' and method == 'GET':
        return coordinator.evidence(paper_id, cursor=int(one('cursor', '0')), limit=int(one('limit', '50'))), 200
    if suffix == 'knowledge' and method in ('GET', 'POST'):
        return coordinator.knowledge(paper_id, body if method == 'POST' else None, source=one('source')), 200
    if len(parts) == 3 and parts[0] == 'findings' and parts[2] == 'annotation' and method == 'POST':
        return coordinator.annotate_finding(paper_id, parts[1], body, page_snapshot=page_snapshot), 200
    if len(parts) == 3 and parts[0] == 'findings' and parts[2] == 'decisions' and method == 'POST':
        return coordinator.review_finding(paper_id, parts[1], body), 200
    if suffix == 'decisions' and method == 'POST':
        return coordinator.decide(paper_id, body), 200
    if method == 'GET' and (suffix == 'export' or parts[0] == 'findings'):
        report = coordinator.report(paper_id, one('run_id'))
        if suffix == 'export':
            # Result contract contains relative evidence paths, no admin tokens
            # or authoritative knowledge file. Evidence opt-out is explicit.
            from copy import deepcopy
            result = deepcopy(report)
            if one('evidence', 'true') == 'false':
                for finding in result['findings']:
                    finding['evidence'] = []
                    finding['related_occurrences'] = []
            return result, 200
        findings = report['findings']
        if len(parts) == 2:
            finding = next((f for f in findings if f['finding_key'] == parts[1]), None)
            if finding is None:
                raise CheckError('finding_not_found', '问题不存在', 404)
            return finding, 200
        if len(parts) != 1:
            raise CheckError('route_not_found', '检查接口不存在', 404)
        limit, offset = int(one('limit', '50')), int(one('cursor', '0'))
        if not 1 <= limit <= 200 or offset < 0:
            raise CheckError('invalid_pagination', 'limit 为 1–200，cursor 必须为非负整数')
        for field in ('rule_id', 'severity', 'lifecycle', 'review_state'):
            value = one(field)
            if value:
                findings = [f for f in findings if f.get(field) == value]
        end = offset + limit
        return {'input_id': report['input_id'], 'findings': findings[offset:end], 'total': len(findings),
                'next_cursor': str(end) if end < len(findings) else None, 'truncated': end < len(findings)}, 200
    if suffix == 'semantic/tasks' and method == 'POST':
        return coordinator.semantic.start(paper_id, body, page_snapshot=page_snapshot), 202
    if suffix == 'semantic/import' and method == 'POST':
        return coordinator.semantic.import_response(paper_id, body), 202
    if suffix == 'semantic/decisions' and method == 'POST':
        return coordinator.semantic.decide(paper_id, body), 200
    if len(parts) in (3, 4) and parts[:2] == ['semantic', 'tasks']:
        request_id = parts[2]
        if len(parts) == 3 and method == 'GET':
            return coordinator.semantic.get(paper_id, request_id), 200
        if len(parts) == 4:
            if parts[3] == 'export' and method == 'GET':
                return coordinator.semantic.export(paper_id, request_id), 200
            if parts[3] == 'cancel' and method == 'POST':
                return coordinator.semantic.cancel(paper_id, request_id), 200
            if parts[3] == 'invoke' and method == 'POST':
                return coordinator.semantic.invoke(paper_id, request_id, page_snapshot=page_snapshot), 202
    raise CheckError('route_not_found', '检查接口不存在', 404)


def entry_route(handler):
    """Return False for unrelated routes. All checks data requires entry auth."""
    parsed = urlsplit(handler.path)
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 5 or parts[:3] != ['api', 'v1', 'papers'] or parts[4] != 'checks':
        return False
    try:
        handler.local(write=True)
        body = read_body(handler) if handler.command == 'POST' else None
        suffix = '/'.join(parts[5:])
        value, status = dispatch(handler.server.manager.checks, parts[3], suffix,
                                 handler.command, parse_qs(parsed.query), body)
        bounded_send(handler.send, value, status)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        value, status = error_value(exc)
        handler.send(value, status)
    return True


def _worker_page_snapshot(directory, content, paper_id):
    """Read one trusted sidecar bound to the annotation publication, not disk."""
    try:
        metadata = json.loads((Path(directory) / 'checks-page.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CheckError('page_snapshot_unavailable', '当前页面缺少可靠检查快照，请重建预览', 409) from exc
    if (not isinstance(metadata, dict) or not isinstance(metadata.get('page_version'), str) or not metadata['page_version']
            or not isinstance(metadata.get('content_id'), str) or not metadata['content_id']):
        raise CheckError('page_snapshot_unavailable', '页面检查快照缺少版本绑定，请重建预览', 409)
    if (metadata['page_version'] != content['version_id'] or metadata.get('entry') != content['entry']
            or metadata.get('paper_id') != paper_id):
        raise CheckError('page_changed', '页面与检查快照版本不一致，请刷新或重建预览', 409, action='reload')
    return metadata


def _status_content_identity(handler):
    """Avoid decoding/validating every unit on unchanged status observations.

    The key hashes actual bytes on every request, never timestamps or size.
    Changed bytes must pass the same full validation before identity reuse.
    Keep only a tiny server-owned identity; mutation requests do not use this.
    """
    from hashlib import sha256
    from ..annotations import MANIFEST
    from ..annotations.content import validate_content
    raw = (Path(handler.directory) / MANIFEST).read_bytes()
    key = sha256(raw).digest()
    cached = getattr(handler.server, '_checks_status_identity', None)
    if cached is not None and cached[0] == key:
        return dict(cached[1])
    content = validate_content(json.loads(raw.decode('utf-8')))
    identity = {name: content[name] for name in ('entry', 'version_id')}
    handler.server._checks_status_identity = (key, identity)
    return dict(identity)


def worker_route(handler):
    parsed = urlsplit(handler.path)
    if parsed.path != '/api/checks' and not parsed.path.startswith('/api/checks/'):
        return False
    try:
        if not local_request(handler.headers, handler.client_address[0]):
            raise CheckError('forbidden_origin', '仅允许当前本机页面访问', 403)
        from ..annotations import load_content
        suffix = parsed.path[len('/api/checks/'):]
        bootstrap = handler.command == 'GET' and suffix == 'status'
        content = _status_content_identity(handler) if bootstrap else load_content(handler.directory)
        root = getattr(handler.server, 'annotation_project_root', None)
        if root is None:
            raise CheckError('unavailable', '静态报告没有活动论文身份', 503)
        identity = paper_identity(root, Path(root) / content['entry'], require_entry=True)
        paper_id, page_version = identity['paper_id'], content['version_id']
        # Status can bootstrap its per-worker paper token, but a supplied page
        # identity must still match. All other requests require all three.
        supplied_paper = handler.headers.get(PAPER_HEADER)
        supplied_version = handler.headers.get(VERSION_HEADER)
        if ((not bootstrap or supplied_paper is not None) and supplied_paper != paper_id):
            raise CheckError('paper_identity', '页面论文身份不符', 403)
        if ((not bootstrap or supplied_version is not None) and supplied_version != page_version):
            raise CheckError('page_changed', '页面输入已更新，请刷新', 409,
                             current_page_version=page_version, action='reload')
        token = handler.server.checks_token
        if not bootstrap and not secrets.compare_digest(handler.headers.get(HEADER_TOKEN, ''), token):
            raise CheckError('paper_token', '论文令牌无效', 403)
        # Cache the authenticated publication before a slow body can yield to
        # a rebuild. Never attach a newly published sidecar to old credentials.
        metadata = _worker_page_snapshot(handler.directory, content, paper_id) if handler.command == 'POST' else None
        if metadata is not None and handler.headers.get(CONTENT_HEADER) != metadata['content_id']:
            raise CheckError('page_changed', '页面检查输入已更新或缺少输入绑定，请刷新或重建预览', 409, action='reload')
        body = read_body(handler) if handler.command == 'POST' else None
        if body is not None and 'paper_id' in body and body['paper_id'] != paper_id:
            raise CheckError('paper_identity', '请求论文身份不符', 403)
        if body is not None and 'page_version' in body and body['page_version'] != page_version:
            raise CheckError('page_changed', '请求页面版本不符', 409)
        if body is not None:
            try:
                latest = load_content(handler.directory)
                latest_metadata = _worker_page_snapshot(handler.directory, latest, paper_id)
                # Compare metadata too: config can change without changing the
                # annotation version. Read the manifest around the sidecar.
                final_content = load_content(handler.directory)
            except (ValueError, OSError) as exc:
                raise CheckError('page_changed', '读取请求期间页面发布已变化或暂不可用，请刷新', 409, action='reload') from exc
            if (latest['version_id'] != page_version or latest['entry'] != content['entry']
                    or final_content['version_id'] != page_version or final_content['entry'] != content['entry']
                    or latest_metadata != metadata):
                raise CheckError('page_changed', '读取请求期间页面输入已更新，请刷新', 409,
                                 current_page_version=final_content['version_id'], action='reload')
            body = {**body, '_page_snapshot': metadata}
        if suffix == 'runs' and body is not None:
            body = {**body, 'paper_id': paper_id, 'page_version': page_version}
        if body is not None and suffix.startswith('findings/') and suffix.endswith('/annotation'):
            body = {**body, '_annotation_directory': str(handler.directory), 'page_version': page_version}
        from ..local_client import checks_request
        try:
            value = checks_request(paper_id, suffix, body, query=parsed.query)
        except LocalError as exc:
            if not bootstrap:
                raise
            value = {'available': False, 'read_only': True, 'paper_id': paper_id, 'state': 'unavailable',
                     'current_input_id': None, 'current_run': None, 'stale': True, 'coverage': [],
                     'capabilities': {key: False for key in ('run', 'cancel', 'knowledge', 'decisions', 'annotation_conversion', 'semantic_import', 'export')},
                     'message': '本地入口不可用；仅可阅读已有冻结结果。'}
        if bootstrap:
            value = {**value, 'paper_id': paper_id, 'page_version': page_version, 'token': token}
        asynchronous = suffix in ('runs', 'semantic/tasks', 'semantic/import') or suffix.endswith('/invoke')
        bounded_send(handler._json, value, 202 if asynchronous and handler.command == 'POST' else 200)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        value, status = error_value(exc)
        handler._json(value, status)
    return True
