"""网页登录、会话保护和安全跳转。"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re

import pytest

spec = importlib.util.spec_from_file_location('review_web', Path(__file__).resolve().parents[1] / 'deploy/web.py')
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


@pytest.fixture
def app(tmp_path, monkeypatch):
    auth = tmp_path / 'auth'
    auth.mkdir()
    salt = b'test salt'
    (auth / 'login.json').write_text(json.dumps({'username': 'reviewer', 'salt': salt.hex(), 'hash': hashlib.pbkdf2_hmac('sha256', b'test password', salt, 600000).hex()}))
    (auth / 'session.key').write_bytes(b'test key')
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'index.html').write_text('private portal')
    (site / 'image.png').write_bytes(b'private image')
    monkeypatch.setattr(web, 'CONFIG', auth)
    monkeypatch.setattr(web, 'SITE', site)
    return site


def request(path='/', method='GET', cookie='', data='', origin=None):
    status_headers = []
    path, _, query = path.partition('?')
    body = data.encode()
    env = {'PATH_INFO': path, 'QUERY_STRING': query, 'REQUEST_METHOD': method, 'HTTP_COOKIE': cookie,
           'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
    if origin:
        env['HTTP_ORIGIN'] = origin
    result = web.application(env, lambda status, headers: status_headers.extend([status, headers]))
    content = b''.join(result)
    if hasattr(result, 'close'):
        result.close()
    return status_headers[0], status_headers[1], content


def login_form():
    status, headers, body = request('/login')
    csrf = re.search(rb'name="csrf" value="([^"]+)"', body)[1].decode()
    cookie = next(value for name, value in headers if name == 'Set-Cookie').split(';')[0]
    return csrf, cookie


def test_protected_pages_and_assets_redirect(app):
    for path in ['/', '/image.png', '/bue-br/latest/report.html']:
        status, headers, body = request(path)
        assert status == '303 See Other'
        assert dict(headers)['Location'].startswith('/login?next=')
        assert b'private' not in body


def test_login_logout_and_secure_cookie(app):
    token, csrf_cookie = login_form()
    status, headers, _ = request('/login', 'POST', csrf_cookie, f'csrf={token}&username=reviewer&password=test+password&next=%2Fimage.png', web.ORIGIN)
    assert status == '303 See Other'
    assert dict(headers)['Location'] == '/image.png'
    session = next(v for k, v in headers if k == 'Set-Cookie' and v.startswith(web.SESSION))
    assert all(part in session for part in ['Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'])
    cookie = session.split(';')[0]
    assert request('/', cookie=cookie)[2] == b'private portal'
    assert request('/image.png', cookie=cookie)[2] == b'private image'
    assert request('/logout', 'POST', cookie, origin=web.ORIGIN)[0] == '303 See Other'
    assert request('/logout', 'POST', cookie, origin='https://attacker.invalid')[0] == '403 Forbidden'


def test_wrong_password_csrf_and_open_redirect(app):
    token, cookie = login_form()
    assert request('/login', 'POST', cookie, 'username=reviewer&password=wrong')[0] == '403 Forbidden'
    status, headers, body = request('/login', 'POST', cookie, f'csrf={token}&username=reviewer&password=wrong', web.ORIGIN)
    assert status == '401 Unauthorized'
    assert '账号或密码不正确' in body.decode()
    assert b'value="wrong"' not in body
    assert request('/login', 'POST', cookie, f'csrf={token}&username=reviewer&password=test+password', 'https://attacker.invalid')[0] == '403 Forbidden'
    assert request('/login', 'POST', cookie, f'csrf={token}&username=reviewer&password=test+password', 'null')[0] == '403 Forbidden'
    for target in ['//evil.invalid', '%2f%2fevil.invalid', '/\\evil.invalid', '/\r\nLocation: evil']:
        assert web.safe_next(target) == '/'


def test_expired_tampered_sessions_and_password_change(app, monkeypatch):
    token = web.signed_token('session', 10)
    assert web.valid_token(token, 'session')
    assert not web.valid_token(token + 'x', 'session')
    assert not web.valid_token(token, 'csrf')
    now = web.time.time()
    monkeypatch.setattr(web.time, 'time', lambda: now + 11)
    assert not web.valid_token(token, 'session')
    monkeypatch.setattr(web.time, 'time', lambda: now)
    config = json.loads((web.CONFIG / 'login.json').read_text())
    config['hash'] = 'different'
    (web.CONFIG / 'login.json').write_text(json.dumps(config))
    assert not web.valid_token(token, 'session')


def test_authenticated_path_escape_is_blocked(app, tmp_path):
    token = web.signed_token('session', 60)
    cookie = web.SESSION + '=' + token
    (tmp_path / 'secret').write_text('secret')
    (app / 'escape').symlink_to(tmp_path / 'secret')
    assert request('/../secret', cookie=cookie)[0] == '404 Not Found'
    assert request('/escape', cookie=cookie)[0] == '404 Not Found'
    assert request('/.hidden', cookie=cookie)[0] == '404 Not Found'
    assert request('/image.png', 'HEAD', cookie)[2] == b''


def test_published_report_has_home_navigation_and_matching_head_length(app):
    release = app / 'bue-br/releases/test'
    release.mkdir(parents=True)
    original = '<html><body><header><div class="report-identity">Report title</div></header><main>Private report</main></body></html>'
    (release / 'report.html').write_text(original)
    (app / 'bue-br/latest').symlink_to('releases/test', target_is_directory=True)
    cookie = web.SESSION + '=' + web.signed_token('session', 60)
    for path in ['/bue-br/latest/report.html', '/bue-br/releases/test/report.html']:
        status, headers, body = request(path, cookie=cookie)
        assert status == '200 OK'
        assert '<a id="site-report-home" href="/"' in body.decode()
        assert '返回首页' in body.decode()
        assert int(dict(headers)['Content-Length']) == len(body)
        head_status, head_headers, head_body = request(path, 'HEAD', cookie)
        assert head_status == '200 OK' and head_body == b''
        assert dict(head_headers)['Content-Length'] == str(len(body))
    assert (release / 'report.html').read_text() == original
    assert request('/bue-br/latest/report.html')[0] == '303 See Other'
