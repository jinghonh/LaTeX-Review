"""WSGI 登录与受保护静态报告，运行于仅监听回环地址的 Gunicorn。"""
import hashlib
import hmac
from html import escape
from http.cookies import SimpleCookie
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, quote, unquote

CONFIG = Path(os.environ.get('LATEX_REVIEW_AUTH_DIR', '/etc/latex-review'))
SITE = Path(os.environ.get('LATEX_REVIEW_SITE', '/srv/latex-review/site'))
ORIGIN = 'https://reports.gpare.top'
SESSION = '__Host-review-session'
CSRF = '__Host-review-csrf'
TTL = 24 * 3600


def safe_next(value):
    value = unquote(value)
    if not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return '/'
    return value


def credentials():
    return json.loads((CONFIG / 'login.json').read_text())


def secret():
    # 修改密码后，原有登录会话立即失效。
    return hmac.digest((CONFIG / 'session.key').read_bytes(), credentials()['hash'].encode(), 'sha256')


def signed_token(purpose, ttl):
    value = f'{purpose}.{int(time.time()) + ttl}.{secrets.token_hex(16)}'
    return value + '.' + hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()


def valid_token(token, purpose):
    try:
        label, expiry, nonce, signature = token.split('.')
        value = f'{label}.{expiry}.{nonce}'
        return (label == purpose and int(expiry) > time.time()
                and hmac.compare_digest(signature, hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()))
    except (ValueError, TypeError):
        return False


def cookie(name, value, ttl):
    return f'{name}={value}; Path=/; Max-Age={ttl}; Secure; HttpOnly; SameSite=Lax'


def read_cookies(environ):
    values = SimpleCookie()
    try:
        values.load(environ.get('HTTP_COOKIE', ''))
    except Exception:
        return {}
    return {key: item.value for key, item in values.items()}


def login_page(target, error='', username=''):
    token = signed_token('csrf', 900)
    html = Path(__file__).with_name('login.html').read_text(encoding='utf-8')
    for key, value in [('ERROR', error), ('CSRF', token), ('NEXT', safe_next(target)), ('USERNAME', username)]:
        html = html.replace('{{' + key + '}}', escape(value, quote=True))
    return html.encode(), token


def report_navigation(html):
    link = ('<nav aria-label="站点导航" style="margin-bottom:12px">'
            '<a id="site-report-home" href="/" style="display:inline-flex;align-items:center;'
            'gap:6px;padding:5px 10px;border:1px solid #c5d8c9;border-radius:4px;'
            'background:#edf5ef;color:#205c49;font-size:12px;text-decoration:none;'
            'font-family:inherit">← 返回首页</a></nav>')
    marker = '<div class="report-identity">'
    if marker in html:
        return html.replace(marker, marker + link, 1)
    return re.sub(r'(<body\b[^>]*>)', lambda match: match[0] + link, html, count=1, flags=re.I)


def application(environ, start_response):
    headers = [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff')]
    def respond(status, body=b'', extra=()):
        start_response(status, [*headers, *extra, ('Content-Length', str(len(body)))])
        return [body] if environ['REQUEST_METHOD'] != 'HEAD' else []
    def redirect(target, extra=()):
        return respond('303 See Other', extra=[('Location', target), *extra])
    def form(target, error='', username='', status='200 OK'):
        body, token = login_page(target, error, username)
        return respond(status, body, [('Content-Type', 'text/html; charset=utf-8'), ('Set-Cookie', cookie(CSRF, token, 900))])
    path = environ.get('PATH_INFO', '/')
    method = environ['REQUEST_METHOD']
    cookies = read_cookies(environ)
    authenticated = valid_token(cookies.get(SESSION, ''), 'session')
    query = parse_qs(environ.get('QUERY_STRING', ''))
    if path == '/login':
        target = safe_next(query.get('next', ['/'])[0])
        if method in {'GET', 'HEAD'}:
            return redirect(target) if authenticated else form(target)
        if method != 'POST':
            return respond('405 Method Not Allowed', extra=[('Allow', 'GET, POST')])
        try:
            length = int(environ.get('CONTENT_LENGTH', '0'))
            if not 0 < length <= 8192:
                raise ValueError()
            data = parse_qs(environ['wsgi.input'].read(length).decode('utf-8'))
        except (ValueError, UnicodeError):
            return respond('400 Bad Request', b'Invalid form')
        target = safe_next(data.get('next', ['/'])[0])
        csrf = data.get('csrf', [''])[0]
        if (environ.get('HTTP_ORIGIN', ORIGIN) != ORIGIN or not csrf
                or not hmac.compare_digest(csrf, cookies.get(CSRF, '')) or not valid_token(csrf, 'csrf')):
            return form(target, '登录页面已过期，请重新输入。', status='403 Forbidden')
        username, password = data.get('username', [''])[0], data.get('password', [''])[0]
        stored = credentials()
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(stored['salt']), 600000).hex()
        if (len(username) > 128 or len(password) > 256
                or not hmac.compare_digest(username.encode(), stored['username'].encode())
                or not hmac.compare_digest(candidate, stored['hash'])):
            return form(target, '账号或密码不正确，请重试。', username[:128], '401 Unauthorized')
        return redirect(target, [('Set-Cookie', cookie(SESSION, signed_token('session', TTL), TTL)),
                                 ('Set-Cookie', cookie(CSRF, '', 0))])
    if path == '/logout':
        if method != 'POST':
            return respond('405 Method Not Allowed', extra=[('Allow', 'POST')])
        if environ.get('HTTP_ORIGIN') != ORIGIN:
            return respond('403 Forbidden')
        return redirect('/login', [('Set-Cookie', cookie(SESSION, '', 0))])
    if not authenticated:
        return redirect('/login?next=' + quote(safe_next(path + ('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')), safe=''))
    if method not in {'GET', 'HEAD'}:
        return respond('405 Method Not Allowed', extra=[('Allow', 'GET, HEAD')])
    try:
        path = path.encode('latin-1').decode('utf-8')
    except (UnicodeError, UnicodeEncodeError):
        pass
    relative = Path(path.lstrip('/'))
    root = SITE.resolve()
    destination = (root / relative).resolve()
    if any(part.startswith('.') for part in relative.parts) or not destination.is_relative_to(root):
        return respond('404 Not Found')
    if destination.is_dir():
        if not path.endswith('/'):
            return redirect(quote(path, safe='/') + '/')
        destination = destination / 'index.html'
    if not destination.is_file():
        return respond('404 Not Found')
    if destination.name == 'report.html':
        body = report_navigation(destination.read_text(encoding='utf-8')).encode('utf-8')
        return respond('200 OK', body, [('Content-Type', 'text/html; charset=utf-8')])
    stream = destination.open('rb')
    media = mimetypes.guess_type(destination.name)[0] or 'application/octet-stream'
    if media == 'text/html':
        media += '; charset=utf-8'
    start_response('200 OK', [*headers, ('Content-Type', media), ('Content-Length', str(os.fstat(stream.fileno()).st_size))])
    if method == 'HEAD':
        stream.close()
        return []
    wrapper = environ.get('wsgi.file_wrapper')
    if wrapper:
        return wrapper(stream, 65536)
    def chunks():
        try:
            while chunk := stream.read(65536):
                yield chunk
        finally:
            stream.close()
    return chunks()
