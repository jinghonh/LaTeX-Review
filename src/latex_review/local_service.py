"""Resident per-user entry and serialized paper slots. Local use only."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection

from importlib.resources import files
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from threading import Condition, Event, RLock, Thread
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid

from .identity import identity_hash, paper_identity
from .local_client import child_environment
from .local_paths import state_directory
from .local_protocol import HEADER_TOKEN, LocalError, MAX_BODY, PROTOCOL_VERSION, valid_id
from .local_reports import copy_report, report_identity
from .local_security import local_request
from .local_state import file_lock, lock_busy, read_json, write_json


@dataclass
class OwnedProcess:
    process: subprocess.Popen
    owner_fd: int
    recipe: dict

    def stop(self):
        if self.owner_fd >= 0:
            os.close(self.owner_fd)
            self.owner_fd = -1
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Popen ownership is retained; we never kill a PID from registry JSON.
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class Slot:
    def __init__(self, manager, paper_id, kind, saved):
        self.manager, self.paper_id, self.kind = manager, paper_id, kind
        self.condition = Condition(RLock())
        self.queue = deque()
        self.current = None
        self.worker = None
        self.builder = None
        self.cancel = Event()
        self.closing = False
        self.saved = saved
        self.state = 'blocked' if lock_busy(self.lock_path) else 'stopped'
        self.error = ''
        self.fingerprint = None
        self.thread = Thread(target=self._run, name=f'local-{kind}-{paper_id[:8]}', daemon=True)
        self.thread.start()

    @property
    def lock_path(self):
        return self.manager.state / 'slots' / f'{self.paper_id}.{self.kind}.lock'

    @property
    def url(self):
        return f'http://127.0.0.1:{self.manager.port}/papers/{self.paper_id}/{self.kind}'

    def snapshot(self):
        with self.condition:
            if self.worker and self.worker.process.poll() is not None:
                self.worker.stop()
                self.worker = None
                self.state = 'failed'
                self.error = '工作进程已退出；请显式重试'
            value = {'state': self.state, 'url': self.url, 'message': self.error,
                     'log': str(self.manager.state / 'logs' / f'{self.paper_id}.{self.kind}.log')}
            if self.worker:
                value.update(pid=self.worker.process.pid, port=self.worker.recipe.get('bound_port'),
                             generation=self.worker.recipe['nonce'], directory=self.worker.recipe.get('directory', self.worker.recipe.get('output')))
            if self.saved:
                value['request'] = deepcopy(self.saved)
                if self.kind == 'diff':
                    try:
                        manifest = read_json(Path(self.saved['directory']) / 'run.json', {})
                        value['baselines'] = manifest.get('baselines', {})
                    except (OSError, ValueError, TypeError):
                        value['baselines'] = {}
            return value

    def submit(self, request, fingerprint, candidate, request_id):
        with self.condition:
            self.snapshot()
            if request.get('port') and self.worker and self.worker.recipe.get('bound_port') != request['port']:
                raise LocalError('活动工作端口不同；请先stop-paper再更改端口', 409)
            for operation in ([self.current] if self.current else []) + list(self.queue):
                if operation['action'] == 'ensure' and operation['fingerprint'] == fingerprint:
                    if candidate:
                        shutil.rmtree(candidate)
                    return operation
            if self.worker and self.state == 'ready' and self.fingerprint == fingerprint:
                if candidate:
                    shutil.rmtree(candidate)
                operation = self.manager.operation(self, 'ensure', request_id, state='ready')
                operation.update(pid=self.worker.process.pid, port=self.worker.recipe['bound_port'],
                                 generation=self.worker.recipe['nonce'])
                return operation
            if len(self.queue) >= 16:
                raise LocalError('槽位等待队列已满', 429)
            operation = self.manager.operation(self, 'ensure', request_id)
            operation.update(request=request, fingerprint=fingerprint, candidate=str(candidate) if candidate else None)
            self.queue.append(operation)
            self.condition.notify_all()
            return operation

    def stop_request(self, request_id):
        with self.condition:
            self.cancel.set()
            while self.queue:
                operation = self.queue.popleft()
                operation.update(state='cancelled', message='已被停止请求取消')
                if operation.get('candidate'):
                    shutil.rmtree(operation['candidate'], ignore_errors=True)
            operation = self.manager.operation(self, 'stop', request_id)
            self.queue.append(operation)
            self.condition.notify_all()
            return operation

    def _spawn(self, recipe):
        nonce = uuid.uuid4().hex
        recipe = {**recipe, 'nonce': nonce, 'ready': str(self.manager.state / 'jobs' / (nonce + '.ready.json')),
                  'log': str(self.manager.state / 'logs' / f'{self.paper_id}.{self.kind}.log')}
        path = self.manager.state / 'jobs' / (nonce + '.recipe.json')
        write_json(path, recipe)
        reader, writer = os.pipe()
        try:
            proc = subprocess.Popen([sys.executable, '-m', 'latex_review.local_worker', str(path), '--owner-fd', str(reader)],
                                    pass_fds=(reader,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, env=child_environment(), cwd=recipe['root'], start_new_session=True)
        except BaseException:
            os.close(writer)
            raise
        finally:
            os.close(reader)
        return OwnedProcess(proc, writer, recipe)

    def _wait_ready(self, owned, *, build=False):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self.cancel.is_set() or self.closing or self.manager.closed.is_set():
                raise LocalError('操作已被停止', 409)
            ready = read_json(owned.recipe['ready'])
            if ready and ready.get('nonce') == owned.recipe['nonce']:
                if ready.get('error'):
                    raise LocalError(ready['error'], 503)
                if build:
                    if owned.process.wait(timeout=5) != 0:
                        raise LocalError('候选预览生成失败', 503)
                    return ready
                if owned.process.poll() is not None:
                    raise LocalError('工作进程在就绪前退出', 503)
                try:
                    with urlopen(f"http://127.0.0.1:{ready['port']}/api/local-worker/health", timeout=1) as response:
                        health = json.load(response)
                    if health.get('nonce') == owned.recipe['nonce'] and health.get('pid') == owned.process.pid:
                        owned.recipe['bound_port'] = ready['port']
                        return ready
                except (OSError, ValueError):
                    pass
            if owned.process.poll() is not None:
                raise LocalError(f"工作进程退出{owned.process.returncode}；检查槽位日志", 503)
            self.cancel.wait(.05)
        raise LocalError('工作进程超时；检查槽位日志', 503)

    def _ensure(self, operation):
        request = operation['request']
        candidate = Path(operation['candidate']) if operation.get('candidate') else None
        old_request = deepcopy(self.saved)
        old_fingerprint = self.fingerprint
        self.state = 'updating' if self.worker else 'starting'
        # Revalidate the effective configuration in the owner, not just the client.
        from .cli import _config
        _config(Path(request['config']), request['config_explicit'])
        if lock_busy(self.lock_path) and not self.worker:
            self.state = 'blocked'
            raise LocalError('旧工作进程仍持有槽位锁；不能启动第二份', 409)
        if self.kind == 'live':
            candidate = Path(tempfile.mkdtemp(prefix='live-', dir=self.manager.state / 'jobs'))
            operation['candidate'] = str(candidate)
            self.builder = self._spawn({**request, 'kind': 'build-live', 'candidate': str(candidate)})
            try:
                built = self._wait_ready(self.builder, build=True)
                request['source_fingerprint'] = built['source_fingerprint']
            finally:
                self.builder.stop()
                self.builder = None
        try:
            if self.cancel.is_set():
                raise LocalError('操作已被停止', 409)
            if self.worker:
                self.state = 'stopping'
                self.worker.stop()
                self.worker = None
            # Lifetime worker lock still protects immediate restart after owner crash.
            if lock_busy(self.lock_path):
                self.state = 'blocked'
                raise LocalError('槽位锁尚未释放', 409)
            worker_request = {**request}
            if self.kind == 'diff':
                target = self.manager.state / 'reports' / self.paper_id / uuid.uuid4().hex
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                candidate.replace(target)
                candidate = None
                worker_request['directory'] = str(target)
            else:
                worker_request['candidate'] = str(candidate)
            self.worker = self._spawn(worker_request)
            self._wait_ready(self.worker)
            # Save the served private directory for diff recovery; live only saves
            # its public output recipe, not the disposable staging directory.
            self.saved = {k: v for k, v in worker_request.items() if k not in ('candidate', 'source_fingerprint')}
            self.fingerprint = operation['fingerprint']
            self.state, self.error = 'ready', ''
            self.manager.save()
            operation.update(state='ready', url=self.url, pid=self.worker.process.pid,
                             port=self.worker.recipe['bound_port'], generation=self.worker.recipe['nonce'])
            self._collect_reports()
        except BaseException:
            if self.worker:
                self.worker.stop()
                self.worker = None
            # A bad replacement must not throw away the last valid report recipe.
            if old_request and not self.cancel.is_set() and not self.manager.closed.is_set():
                try:
                    self.worker = self._spawn(old_request)
                    self._wait_ready(self.worker)
                    self.saved, self.fingerprint, self.state = old_request, old_fingerprint, 'ready'
                except (OSError, ValueError, RuntimeError):
                    if self.worker:
                        self.worker.stop()
                        self.worker = None
            raise
        finally:
            if candidate:
                shutil.rmtree(candidate, ignore_errors=True)

    def _collect_reports(self):
        parent = self.manager.state / 'reports' / self.paper_id
        if not parent.is_dir():
            return
        keep = sorted((p for p in parent.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)[:2]
        active = Path(self.saved.get('directory', '')) if self.saved else None
        for p in parent.iterdir():
            if p.is_dir() and p not in keep and p != active:
                shutil.rmtree(p)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.queue or self.closing)
                if self.closing:
                    break
                operation = self.queue.popleft()
                self.current = operation
                if operation['action'] == 'ensure':
                    self.cancel.clear()
                operation['state'] = 'starting'
            try:
                if operation['action'] == 'stop':
                    self.state = 'stopping'
                    if self.worker:
                        self.worker.stop()
                        self.worker = None
                    if lock_busy(self.lock_path):
                        self.state = 'blocked'
                        raise LocalError('未知旧工作进程仍持锁；未终止该进程', 409)
                    self.state, self.error = 'stopped', ''
                    operation.update(state='ready', url=self.url)
                else:
                    self._ensure(operation)
            except Exception as exc:
                self.error = str(exc)
                if self.state != 'blocked':
                    self.state = 'ready' if self.worker else 'failed'
                operation.update(state='cancelled' if self.cancel.is_set() else 'failed',
                                 message=str(exc), error_status=getattr(exc, 'status', 503))
                self.manager.logger.warning('operation_failed paper=%s kind=%s operation=%s message=%s',
                                            self.paper_id, self.kind, operation['operation_id'], exc)
            finally:
                if operation.get('candidate'):
                    shutil.rmtree(operation['candidate'], ignore_errors=True)
                with self.condition:
                    self.current = None
                self.manager.save()
        if self.builder:
            self.builder.stop()
        if self.worker:
            self.worker.stop()

    def close(self):
        with self.condition:
            self.closing = True
            self.cancel.set()
            for operation in self.queue:
                operation['state'] = 'cancelled'
                if operation.get('candidate'):
                    shutil.rmtree(operation['candidate'], ignore_errors=True)
            self.queue.clear()
            self.condition.notify_all()


class Manager:
    def __init__(self, state, port, logger):
        self.state, self.port, self.logger = state, port, logger
        self.lock = RLock()
        self.closed = Event()
        self.operations = {}
        self.receipts = {}
        self.receipt_locks = {}
        self.papers = {}
        self.slots = {}
        (state / 'jobs').mkdir(exist_ok=True, mode=0o700)
        (state / 'logs').mkdir(exist_ok=True, mode=0o700)
        try:
            registry = read_json(state / 'registry.json', {'papers': {}})
            if not isinstance(registry, dict) or not isinstance(registry.get('papers'), dict):
                raise ValueError('注册记录格式无效')
            for paper_id, paper in registry['papers'].items():
                if (not valid_id(paper_id) or not Path(paper['root']).is_absolute()
                        or Path(paper['entry']).is_absolute() or '..' in Path(paper['entry']).parts
                        or identity_hash({'root': paper['root'], 'entry': paper['entry']}) != paper_id):
                    raise ValueError('注册论文身份不符')
                self.papers[paper_id] = {k: v for k, v in paper.items() if k != 'slots'}
                for kind, recipe in paper.get('slots', {}).items():
                    if kind not in ('live', 'diff') or not isinstance(recipe, dict):
                        raise ValueError('注册槽位无效')
                    self.slots[(paper_id, kind)] = Slot(self, paper_id, kind, recipe)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.papers.clear()
            for slot in self.slots.values():
                slot.close()
                slot.thread.join()
            self.slots.clear()
            path = state / 'registry.json'
            if path.exists() and not path.is_symlink():
                path.replace(state / f'registry.corrupt-{uuid.uuid4().hex}.json')
            logger.warning('registry_corrupt message=%s', exc)

    def operation(self, slot, action, request_id, *, state='queued'):
        operation = {'operation_id': uuid.uuid4().hex, 'paper_id': slot.paper_id, 'kind': slot.kind,
                     'action': action, 'state': state, 'url': slot.url}
        with self.lock:
            self.operations[operation['operation_id']] = operation
        return operation

    def save(self):
        with self.lock:
            papers = deepcopy(self.papers)
            for (paper_id, kind), slot in self.slots.items():
                if slot.saved:
                    papers[paper_id].setdefault('slots', {})[kind] = deepcopy(slot.saved)
            write_json(self.state / 'registry.json', {'protocol': PROTOCOL_VERSION, 'papers': papers})

    def validate(self, value, paper_id, kind):
        if not isinstance(value, dict) or not valid_id(paper_id) or kind not in ('live', 'diff'):
            raise LocalError('论文或服务槽位无效')
        if value.get('reuse_saved'):
            slot = self.slots.get((paper_id, kind))
            if not slot or not slot.saved:
                raise LocalError('没有可恢复的服务配方', 404)
            value = {**slot.saved, 'request_id': value.get('request_id', uuid.uuid4().hex)}
        allowed = {'paper_id', 'root', 'entry', 'report_project_id', 'kind', 'output', 'directory', 'port',
                   'config', 'config_explicit', 'cache', 'poll_files', 'configuration_fingerprint', 'request_id'}
        if set(value) - allowed:
            raise LocalError('请求包含未知字段')
        if value.get('paper_id') != paper_id or value.get('kind') != kind:
            raise LocalError('路由与请求论文身份不符')
        identity = paper_identity(value['root'], value['entry'], require_entry=kind == 'live')
        if identity['paper_id'] != paper_id:
            raise LocalError('论文身份不符')
        request = {**value, **identity}
        request.pop('request_id', None)
        for key in ('config', 'cache', 'output' if kind == 'live' else 'directory'):
            if not isinstance(request.get(key), str) or not Path(request[key]).is_absolute():
                raise LocalError(f'{key}必须是绝对路径')
            request[key] = str(Path(request[key]).resolve())
        if type(request.get('config_explicit')) is not bool:
            raise LocalError('配置标志无效')
        from .cli import _config
        config = _config(Path(request['config']), request['config_explicit'])
        request['configuration_fingerprint'] = identity_hash(config)
        if kind == 'live':
            from .live import prepare_request
            request = prepare_request(Path(identity['root']) / identity['entry'], root=identity['root'],
                                      output=request['output'], config=request['config'] if request['config_explicit'] or Path(request['config']).exists() else None,
                                      cache=request['cache'], poll_files=request.get('poll_files', False))
            # Default config was resolved in the client cwd, not the daemon cwd.
            request['config'] = value['config']
            request['config_explicit'] = value['config_explicit']
            request['configuration_fingerprint'] = identity_hash(config)
        else:
            if type(request.get('port', 0)) is not int or not 0 <= request.get('port', 0) <= 65535:
                raise LocalError('工作端口无效')
            checked = report_identity(request['directory'], root=identity['root'], entry=identity['entry'])
            if checked['paper_id'] != paper_id:
                raise LocalError('报告与论文身份不符')
            request.update(checked)
        return request, value.get('request_id', uuid.uuid4().hex)

    def register(self, value):
        identity = paper_identity(value['root'], value['entry'])
        if value.get('paper_id') != identity['paper_id']:
            raise LocalError('注册论文身份不符')
        with self.lock:
            self.papers[identity['paper_id']] = identity
            self.save()
        return identity

    def ensure(self, paper_id, kind, value):
        request_id = value.get('request_id', uuid.uuid4().hex)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise LocalError('request_id无效')
        value = {**value, 'request_id': request_id}
        with self.lock:
            receipt_lock = self.receipt_locks.setdefault((paper_id, kind, request_id), RLock())
        with receipt_lock:
            return self._ensure_request(paper_id, kind, value)

    def _ensure_request(self, paper_id, kind, value):
        if self.closed.is_set():
            raise LocalError('入口正在停止', 503)
        request, request_id = self.validate(value, paper_id, kind)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise LocalError('request_id无效')
        candidate = None
        receipt_fingerprint = identity_hash(request)
        try:
            if kind == 'diff':
                candidate = self.state / 'jobs' / ('diff-' + uuid.uuid4().hex)
                content_hash = copy_report(request['directory'], candidate)
                copied_identity = report_identity(candidate, root=request['root'], entry=request['entry'])
                if copied_identity['paper_id'] != paper_id:
                    raise LocalError('候选副本的论文身份已变化', 409)
                fingerprint = identity_hash({'request': request, 'content': content_hash})
            else:
                fingerprint = receipt_fingerprint
            with self.lock:
                receipt_key = (paper_id, kind, request_id)
                if receipt_key in self.receipts:
                    original, operation = self.receipts[receipt_key]
                    if original != fingerprint:
                        raise LocalError('相同request_id的内容不同', 409)
                    return self.public_operation(operation)
                self.papers[paper_id] = {key: request[key] for key in ('paper_id', 'root', 'entry')}
                slot = self.slots.get((paper_id, kind))
                if slot is None:
                    slot = self.slots[(paper_id, kind)] = Slot(self, paper_id, kind, None)
            if self.closed.is_set():
                raise LocalError('入口正在停止', 503)
            operation = slot.submit(request, fingerprint, candidate, request_id)
            candidate = None
            with self.lock:
                self.receipts[receipt_key] = (fingerprint, operation)
            return self.public_operation(operation)
        finally:
            if candidate:
                shutil.rmtree(candidate, ignore_errors=True)

    @staticmethod
    def public_operation(operation):
        return {k: deepcopy(v) for k, v in operation.items()
                if k not in ('request', 'candidate', 'fingerprint')}

    def list_papers(self):
        with self.lock:
            values = deepcopy(list(self.papers.values()))
            slots = list(self.slots.items())
        for paper in values:
            paper['slots'] = {kind: slot.snapshot() for (paper_id, kind), slot in slots if paper_id == paper['paper_id']}
        return {'papers': values}

    def close(self):
        self.closed.set()
        for slot in list(self.slots.values()):
            slot.close()
        for slot in list(self.slots.values()):
            slot.thread.join(timeout=15)
        self.save()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        self.server.manager.logger.info('http %s', format % args)

    def local(self, *, write=False):
        if not local_request(self.headers, self.client_address[0]):
            raise LocalError('仅允许当前本机入口访问', 403)
        if write and not secrets.compare_digest(self.headers.get(HEADER_TOKEN, ''), self.server.token):
            raise LocalError('入口令牌无效', 403)

    def send(self, value, status=200, content_type='application/json; charset=utf-8', *, frame_origin=None):
        data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        policy = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'"
        if frame_origin:
            policy += '; frame-src ' + frame_origin
        self.send_header('Content-Security-Policy', policy)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.local()
            path = urlsplit(self.path).path
            manager = self.server.manager
            if path == '/health':
                return self.send({'protocol': PROTOCOL_VERSION, 'nonce': self.server.nonce, 'pid': os.getpid()})
            if path == '/api/v1/papers':
                return self.send(manager.list_papers())
            if path.startswith('/api/v1/operations/'):
                operation = manager.operations.get(path.rsplit('/', 1)[-1])
                if operation is None:
                    raise LocalError('操作不存在', 404)
                return self.send(manager.public_operation(operation))
            if self.proxy_worker():
                return
            if path.startswith('/papers/'):
                parts = path.strip('/').split('/')
                if len(parts) != 3 or not valid_id(parts[1]) or parts[2] not in ('live', 'diff'):
                    raise LocalError('论文链接无效', 404)
                slot = manager.slots.get((parts[1], parts[2]))
                snapshot = slot.snapshot() if slot else {}
                if snapshot.get('state') in ('ready', 'updating') and snapshot.get('port'):
                    return self.report_frame(parts[1], parts[2], snapshot)
                return self.dashboard()
            if path == '/':
                return self.dashboard()
            raise LocalError('入口不存在', 404)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.send({'message': str(exc)}, getattr(exc, 'status', 400))

    def dashboard(self):
        html = files('latex_review').joinpath('local_dashboard.html').read_text(encoding='utf-8')
        html = html.replace('__TOKEN__', self.server.token)
        self.send(html, content_type='text/html; charset=utf-8')

    def report_frame(self, paper_id, kind, snapshot):
        worker_base = f"/papers/{paper_id}/{kind}/worker"
        html = files('latex_review').joinpath('local_report.html').read_text(encoding='utf-8')
        html = (html.replace('__WORKER_BASE__', worker_base)
                .replace('__PAPER_ID__', paper_id)
                .replace('__KIND__', kind)
                .replace('__GENERATION__', json.dumps(snapshot['generation']))
                .replace('__TITLE__', '持续预览' if kind == 'live' else '差异报告'))
        self.send(html, content_type='text/html; charset=utf-8', frame_origin="'self'")

    def proxy_worker(self):
        parts = urlsplit(self.path).path.split('/')
        if len(parts) < 6 or parts[1] != 'papers' or parts[4] != 'worker':
            return False
        if not valid_id(parts[2]) or parts[3] not in ('live', 'diff'):
            raise LocalError('论文链接无效', 404)
        slot = self.server.manager.slots.get((parts[2], parts[3]))
        snapshot = slot.snapshot() if slot else {}
        if snapshot.get('state') not in ('ready', 'updating') or not snapshot.get('port'):
            raise LocalError('论文服务不可用', 503)
        length = int(self.headers.get('Content-Length', '0'))
        if self.headers.get('Transfer-Encoding') or not 0 <= length <= 8 * 1024 * 1024:
            raise LocalError('请求大小无效', 413)
        body = self.rfile.read(length) if length else None
        prefix = '/'.join(parts[:5])
        target = self.path[len(prefix):]
        # Never use a client-supplied host or forwarded header as a proxy target.
        headers = {key: self.headers[key] for key in (
            'Content-Type', 'Content-Encoding', HEADER_TOKEN, 'X-LaTeX-Review-Delta-Encoding',
            'Range', 'If-Modified-Since') if key in self.headers}
        if self.headers.get('Origin'):
            headers['Origin'] = f"http://127.0.0.1:{snapshot['port']}"
        connection = HTTPConnection('127.0.0.1', snapshot['port'], timeout=60)
        try:
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            metadata = None
            if target == '/api/report' and response.status == 200:
                value = json.loads(response.read())
                value['report_url'] = 'http://' + self.headers['Host'] + prefix + '/report.html'
                metadata = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if metadata is not None and key.lower() == 'content-length':
                    value = str(len(metadata))
                if key.lower() in ('content-type', 'content-length', 'content-encoding',
                                   'content-disposition', 'last-modified', 'server-timing',
                                   'content-range', 'accept-ranges'):
                    self.send_header(key, value)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if metadata is not None:
                self.wfile.write(metadata)
            else:
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
        finally:
            connection.close()
        return True

    def do_POST(self):
        try:
            self.local()
            if self.proxy_worker():
                return
            self.local(write=True)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise LocalError('需要JSON请求')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise LocalError('请求大小无效', 413)
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise LocalError('JSON必须为对象')
            path = urlsplit(self.path).path
            manager = self.server.manager
            if path == '/api/v1/shutdown':
                self.send({'state': 'stopping'})
                Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == '/api/v1/papers/register':
                return self.send(manager.register(value))
            parts = path.strip('/').split('/')
            if len(parts) != 6 or parts[:3] != ['api', 'v1', 'papers']:
                raise LocalError('接口不存在', 404)
            paper_id, kind, action = parts[3:6]
            if not valid_id(paper_id) or kind not in ('live', 'diff'):
                raise LocalError('论文或服务类型无效')
            if action == 'ensure':
                return self.send(manager.ensure(paper_id, kind, value), 202)
            if action == 'stop':
                slot = manager.slots.get((paper_id, kind))
                if slot is None:
                    return self.send({'state': 'ready', 'message': '服务已停止'})
                operation = slot.stop_request(value.get('request_id', uuid.uuid4().hex))
                return self.send(manager.public_operation(operation), 202)
            raise LocalError('接口不存在', 404)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.send({'message': str(exc)}, getattr(exc, 'status', 400))


class EntryServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def server_bind(self):
        from socketserver import TCPServer
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


def run(port):
    state = state_directory()
    logger = logging.getLogger('latex_review.local')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(state / 'entry.log', maxBytes=5 * 1024**2, backupCount=5)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    os.chmod(state / 'entry.log', 0o600)
    try:
        with file_lock(state / 'local.lock', blocking=False):
            with EntryServer(('127.0.0.1', port), Handler) as server:
                server.nonce, server.token = secrets.token_hex(24), secrets.token_urlsafe(32)
                server.manager = Manager(state, server.server_port, logger)
                write_json(state / 'endpoint.json', {'protocol': PROTOCOL_VERSION, 'nonce': server.nonce,
                                                    'token': server.token, 'pid': os.getpid(), 'port': server.server_port})
                previous = {}
                def stop_signal(*_):
                    Thread(target=server.shutdown, daemon=True).start()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous[sig] = signal.signal(sig, stop_signal)
                logger.info('entry_started port=%s nonce=%s', server.server_port, server.nonce)
                try:
                    server.serve_forever(poll_interval=.1)
                finally:
                    server.manager.close()
                    endpoint = read_json(state / 'endpoint.json', {})
                    if endpoint.get('nonce') == server.nonce:
                        (state / 'endpoint.json').unlink(missing_ok=True)
                    for sig, old in previous.items():
                        signal.signal(sig, old)
        return 0
    except (OSError, ValueError) as exc:
        logger.error('entry_failed message=%s', exc)
        print(f'入口启动失败：{exc}', file=sys.stderr)
        return 4
    finally:
        handler.close()
        logger.removeHandler(handler)


def main(argv=None):
    import argparse
    import webbrowser
    from . import local_client
    parser = argparse.ArgumentParser(description='管理常驻本地论文入口')
    parser.add_argument('command', choices=('start', 'run', 'status', 'stop', 'open', 'list', 'stop-paper', 'doctor'))
    parser.add_argument('paper_id', nargs='?')
    parser.add_argument('--kind', choices=('live', 'diff', 'both'), default='both')
    parser.add_argument('--port', '--entry-port', type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            port = args.port or int(os.environ.get('LATEX_REVIEW_LOCAL_PORT', read_json(state_directory() / 'config.json', {}).get('port', 8765)))
            if not 1 <= port <= 65535:
                raise LocalError('入口端口无效')
            return run(port)
        if args.command in ('start', 'open'):
            value = local_client.start(port=args.port)
            url = f"http://127.0.0.1:{value['port']}/"
            print(url)
            if args.command == 'open':
                webbrowser.open(url)
        elif args.command == 'stop':
            print(json.dumps(local_client.stop(), ensure_ascii=False))
        elif args.command in ('status', 'list'):
            print(json.dumps(local_client.request('/api/v1/papers'), ensure_ascii=False, indent=2))
        elif args.command == 'doctor':
            state = state_directory()
            value = {'state_directory': str(state), 'entry_lock_busy': lock_busy(state / 'local.lock'),
                     'endpoint': {k: v for k, v in (read_json(state / 'endpoint.json', {}) or {}).items() if k != 'token'},
                     'legacy_processes': '未知；不扫描或终止无协议进程', 'log': str(state / 'entry.log')}
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            if not valid_id(args.paper_id):
                raise LocalError('stop-paper需要完整论文ID；用list查询')
            for kind in ('live', 'diff') if args.kind == 'both' else (args.kind,):
                result = local_client.request(f'/api/v1/papers/{args.paper_id}/{kind}/stop', {})
                if operation := result.get('operation_id'):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        status = local_client.request('/api/v1/operations/' + operation)
                        if status['state'] in ('ready', 'failed', 'cancelled'):
                            if status['state'] != 'ready':
                                raise LocalError(status.get('message', '停止失败'))
                            break
                        time.sleep(.05)
                    else:
                        raise LocalError('停止槽位超时')
            print('论文服务已停止。')
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'本地入口操作失败：{exc}', file=sys.stderr)
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
