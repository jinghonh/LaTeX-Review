"""Client for the resident entry. Importing it never starts a service."""
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
import uuid

from .local_paths import state_directory
from .local_protocol import HEADER_TOKEN, LocalError, PROTOCOL_VERSION
from .local_state import file_lock, read_json, write_json


def child_environment():
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parent.parent)
    env['PYTHONPATH'] = source + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def endpoint(*, verify=True):
    try:
        value = read_json(state_directory() / 'endpoint.json')
    except (OSError, ValueError) as exc:
        raise LocalError(f'本地入口状态损坏：{exc}', 503) from exc
    if not isinstance(value, dict):
        raise LocalError('本地入口尚未运行', 503)
    if value.get('protocol') != PROTOCOL_VERSION:
        raise LocalError('本地入口协议不兼容；请先停止旧版本入口', 409)
    port = value.get('port')
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(value.get('nonce'), str):
        raise LocalError('本地入口状态损坏', 503)
    if verify:
        health = request('/health', endpoint_value=value)
        if health.get('nonce') != value['nonce'] or health.get('protocol') != PROTOCOL_VERSION:
            raise LocalError('本地入口身份不匹配', 409)
    return value


def request(path, body=None, *, endpoint_value=None, timeout=10):
    value = endpoint_value or endpoint(verify=False)
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        headers.update({'Content-Type': 'application/json', HEADER_TOKEN: value['token']})
        data = json.dumps(body, ensure_ascii=False).encode()
    try:
        with build_opener(ProxyHandler({})).open(Request(f"http://127.0.0.1:{value['port']}" + path, data=data, headers=headers), timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get('message', str(exc))
        except (ValueError, AttributeError):
            message = str(exc)
        raise LocalError(message, exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LocalError(f'本地入口不可用：{exc}', 503) from exc


def start(*, port=None, timeout=20):
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise LocalError('入口端口必须为1至65535')
    try:
        value = endpoint()
        if port is not None and value['port'] != port:
            raise LocalError('入口正在使用其他端口；请先stop再更改端口', 409)
        return value
    except LocalError as exc:
        if exc.status == 409:
            raise
    state = state_directory()
    with file_lock(state / 'startup.lock'):
        try:
            value = endpoint()
            if port is not None and value['port'] != port:
                raise LocalError('入口正在使用其他端口；请先stop再更改端口', 409)
            return value
        except LocalError as exc:
            if exc.status == 409:
                raise
        from .local_state import lock_busy
        if lock_busy(state / 'local.lock'):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    return endpoint()
                except LocalError as exc:
                    if exc.status == 409:
                        raise
                    time.sleep(.1)
            raise LocalError('入口持锁但未就绪；检查entry.log', 503)
        config = read_json(state / 'config.json', {})
        chosen = port or int(os.environ.get('LATEX_REVIEW_LOCAL_PORT', config.get('port', 8765)))
        if not 1 <= chosen <= 65535:
            raise LocalError('入口端口无效')
        write_json(state / 'config.json', {'port': chosen})
        rotate_log(state / 'entry.log')
        with (state / 'entry.log').open('ab') as log:
            os.chmod(state / 'entry.log', 0o600)
            proc = subprocess.Popen([sys.executable, '-m', 'latex_review.local_service', 'run', '--port', str(chosen)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    env=child_environment(), cwd='/', start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise LocalError(f'本地入口启动失败；检查{state / "entry.log"}', 503)
            try:
                return endpoint()
            except LocalError:
                # A stale endpoint may briefly name the previous daemon while
                # our new process has bound its port but not published JSON yet.
                time.sleep(.05)
        # This is our own newly spawned daemon, not a process read from a PID file.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise LocalError('入口启动超时', 503)


def rotate_log(path):
    path = Path(path)
    if path.exists() and path.stat().st_size >= 5 * 1024**2:
        for index in range(4, 0, -1):
            old = path.with_name(path.name + f'.{index}')
            if old.exists():
                old.replace(path.with_name(path.name + f'.{index + 1}'))
        path.replace(path.with_name(path.name + '.1'))


def ensure(value, *, timeout=120):
    if timeout <= 0:
        raise LocalError('启动超时必须大于0')
    current = start()
    body = {**value, 'request_id': value.get('request_id', uuid.uuid4().hex)}
    result = request(f"/api/v1/papers/{value['paper_id']}/{value['kind']}/ensure", body,
                     endpoint_value=current, timeout=max(10, timeout))
    operation = result['operation_id']
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = request('/api/v1/operations/' + operation, endpoint_value=current)
        if snapshot['state'] == 'ready':
            return snapshot
        if snapshot['state'] in ('failed', 'cancelled'):
            raise LocalError(snapshot.get('message', '本地阅读启动失败'), snapshot.get('error_status', 503))
        time.sleep(.05)
    raise LocalError(f'工作进程启动超时；操作{operation}可能仍在运行；请查询status或显式stop-paper', 503)


def follow(paper_id, kind):
    previous = None
    def detach(*_):
        raise KeyboardInterrupt
    old_signal = signal.signal(signal.SIGTERM, detach)
    try:
        while True:
            values = request('/api/v1/papers')['papers']
            slot = next((p for p in values if p['paper_id'] == paper_id), {}).get('slots', {}).get(kind, {})
            current = (slot.get('state'), slot.get('message'))
            if current != previous:
                print(f"{kind}: {current[0]} {current[1] or ''}", file=sys.stderr, flush=True)
                previous = current
            if slot.get('state') not in ('ready', 'starting', 'updating'):
                return
            time.sleep(.5)
    except KeyboardInterrupt:
        return
    finally:
        signal.signal(signal.SIGTERM, old_signal)


def stop():
    value = endpoint()
    result = request('/api/v1/shutdown', {}, endpoint_value=value)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            request('/health', endpoint_value=value, timeout=.2)
        except LocalError:
            return result
        time.sleep(.05)
    raise LocalError('入口停止超时；检查日志')
