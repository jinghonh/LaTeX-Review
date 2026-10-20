"""通过本机 HTTP 预览已生成的报告目录。"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
import time
import gzip
import io
import zlib
from functools import partial
from threading import RLock
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
from socketserver import TCPServer
import sys
import webbrowser
from urllib.parse import parse_qs, urlsplit

from .translation import TranslationConfig, TranslationError
from .translation_session import TranslationSession


class ReportHandler(SimpleHTTPRequestHandler):
    def _local_request(self) -> bool:
        expected = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != expected or (origin and origin != "http://" + expected):
            self._json({"message": "仅允许当前本机报告访问翻译服务"}, 403)
            return False
        return True

    def _json(self, value: dict, status: int = 200):
        content = json.dumps(value, ensure_ascii=False).encode()
        compressed = len(content) > 16384 and 'gzip' in self.headers.get('Accept-Encoding', '')
        if compressed:
            content = gzip.compress(content, compresslevel=1)
        self.send_response(status)
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        if hasattr(self, "annotation_duration"):
            self.send_header("Server-Timing", f"annotations;dur={self.annotation_duration:.3f}")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/local-worker/health" and getattr(self.server, "local_nonce", None):
            if not self._local_request():
                return
            return self._json({"nonce": self.server.local_nonce, "pid": __import__("os").getpid()})
        if path == "/api/report":
            if not self._local_request():
                return
            from .annotations import load_content
            value = {"report_directory": str(Path(self.directory).resolve()),
                     "report_url": f"http://127.0.0.1:{self.server.server_port}/report.html"}
            try:
                content = load_content(self.directory)
                from .annotation_output import report_context
                value['report_context'] = report_context(self.directory, content)
                value.update({key: content[key] for key in
                              ("project_root", "project_id", "version_id", "entry") if key in content})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                value["annotation_content_error"] = str(exc)
            return self._json(value)
        if path.startswith("/api/annotations"):
            return self._annotation_get(path)
        if not path.startswith("/api/translation/"):
            return super().do_GET()
        if not self._local_request():
            return
        manager = self.server.translation
        if path == "/api/translation/status":
            value = manager.snapshot() if manager else {"enabled": False, "states": {}, "results": {},
                "message": "请在全局或项目配置中设置 [translation] 的接口地址和模型，然后重新启动本机预览服务。"}
            value["token"] = self.server.translation_token
            self._json(value)
        elif path == "/api/translation/export":
            from .delivery import publication_html, single_file_html
            try:
                root = Path(self.directory).resolve()
                snapshot = manager.snapshot() if manager else {}
                html = publication_html(root, results=snapshot.get("results"),
                                        warnings=tuple(snapshot.get("delivery_warnings", ())))
                content = single_file_html(html, root).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="report-translated.html"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (OSError, ValueError):
                self._json({"message": "导出失败，请检查报告资源是否完整"}, 400)
        else:
            self._json({"message": "未知的翻译接口"}, 404)

    def do_POST(self):
        if urlsplit(self.path).path.startswith("/api/annotations"):
            return self._annotation_post()
        if not self._local_request():
            return
        if (not hmac.compare_digest(self.headers.get("X-LaTeX-Review-Token", "").encode(), self.server.translation_token.encode())
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
            return self._json({"message": "翻译请求校验失败，请重新打开报告"}, 403)
        manager = self.server.translation
        if manager is None:
            return self._json({"message": "尚未配置翻译接口，请配置后重新启动服务"}, 400)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 128 * 1024:
                raise ValueError("请求大小无效")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("请求须为对象")
            path = urlsplit(self.path).path
            if path == "/api/translation/start":
                if not isinstance(body.get("retry", False), bool):
                    raise ValueError("重试标记须为布尔值")
                manager.submit(body.get("units"), retry=body.get("retry", False))
            elif path == "/api/translation/cancel":
                manager.cancel()
            else:
                return self._json({"message": "未知的翻译接口"}, 404)
            self._json(manager.snapshot())
        except (ValueError, UnicodeError) as exc:
            self._json({"message": str(exc) if isinstance(exc, TranslationError) else "翻译请求格式无效"}, 400)

    def _annotation_session(self):
        return self.server.annotation_session(self.directory)

    def _annotation_get(self, path):
        if not self._local_request():
            return
        try:
            if path == '/api/annotations/jobs':
                return self._json(self.server.annotation_jobs.active())
            if path.startswith('/api/annotations/jobs/'):
                return self._json(self.server.annotation_jobs.snapshot(path.rsplit('/', 1)[-1]))
            session = self._annotation_session()
            if path == "/api/annotations/content":
                self._json(session.content)
            elif path == "/api/annotations/status":
                from .annotation_output import page_projection, thread_summary
                view = parse_qs(urlsplit(self.path).query).get('view', ['summary'])[0]
                if view == 'changes':
                    since = int(parse_qs(urlsplit(self.path).query).get('since', ['0'])[0])
                    value = page_projection(session.changes(since))
                elif view == 'revision':
                    value = session.metadata()
                else:
                    value = session.read()
                    if view == 'page':
                        value = page_projection(value)
                    elif view != 'full':
                        value['threads'] = [thread_summary(t) for t in value['threads']]
                self._json({**value, "token": self.server.annotation_token})
            elif path == '/api/annotations/history':
                thread_id = parse_qs(urlsplit(self.path).query).get('thread_id', [''])[0]
                thread = session.export()['threads'].get(thread_id)
                if not thread:
                    return self._json({'message': '批注不存在'}, 404)
                self._json({'thread_id': thread_id, 'history': thread['history']})
            elif path == "/api/annotations/export":
                self._json(session.export())
            else:
                self._json({"message": "未知批注接口"}, 404)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._json({"message": str(exc)}, 400)

    def _annotation_post(self):
        from .annotations import AnnotationConflict
        if not self._local_request():
            return
        if (not hmac.compare_digest(self.headers.get("X-LaTeX-Review-Token", "").encode(),
                                   self.server.annotation_token.encode())
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
            return self._json({"message": "批注请求校验失败，请重新打开报告"}, 403)
        if urlsplit(self.path).path != "/api/annotations/update":
            return self._json({"message": "未知批注接口"}, 404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8 * 1024 * 1024:
                raise ValueError("请求大小无效")
            raw = self.rfile.read(size)
            encoding = self.headers.get('Content-Encoding', 'identity')
            if encoding == 'gzip':
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('解压请求超过 8 MiB')
            elif encoding != 'identity':
                raise ValueError('请求编码无效')
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and isinstance(envelope.get('request'), dict) and envelope['request'].get('operation') == 'batch' and len(raw) > 1024 * 1024:
                raise ValueError('批量请求最多 1 MiB')
            if parse_qs(urlsplit(self.path).query).get('async') == ['1']:
                jobs = self.server.annotation_jobs
                directory = self.directory
                server = self.server
                # Resolve content inside the worker so queued tasks cannot use a stale manifest.
                def run(progress):
                    return self._execute_annotation(envelope, progress, directory, server)
                return self._json(jobs.submit(run), 202)
            start = time.perf_counter()
            value = self._execute_annotation(envelope, None, self.directory, self.server)
            if self.headers.get('X-LaTeX-Review-Delta-Encoding') == 'request-bodies':
                from .annotation_output import request_body_projection
                value = request_body_projection(value, envelope['request'])
            self.annotation_duration = (time.perf_counter() - start) * 1000
            self._json(value)
        except AnnotationConflict as exc:
            self._json({"message": str(exc), "error": "conflict", **exc.details}, 409)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, EOFError, zlib.error) as exc:
            self._json({"message": str(exc), "error": "invalid_request"}, 400)

    @staticmethod
    def _execute_annotation(envelope, progress, directory, server):
        from .annotation_output import page_projection
        session = server.annotation_session(directory)
        content = session.content
        if progress:
            progress('validate', 0, 0)
        from .report_rebuild import execute_on_report
        value = execute_on_report(session, envelope, directory, progress=progress, incremental=True)
        if 'threads' in value:
            value = page_projection(value)
        value.setdefault('project_id', content['project_id'])
        value.setdefault('version_id', content['version_id'])
        return value

    def send_head(self):
        root = Path(self.directory).resolve()
        try:
            target = Path(self.translate_path(self.path)).resolve()
            if not target.is_relative_to(root):
                self.send_error(403, explain="报告目录之外的资源不可访问")
                return None
            if target.is_dir():
                self.send_error(404, explain="请访问 report.html")
                return None
        except (OSError, ValueError, RuntimeError):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


class ReportServer(ThreadingHTTPServer):
    translation: TranslationSession | None = None
    annotation_project_root: Path | None = None

    def __init__(self, *args, **kwargs):
        from .annotation_jobs import AnnotationJobs
        self.annotation_jobs = AnnotationJobs()
        self.annotation_content_lock = RLock()
        self.annotation_content_key = None
        self.annotation_cached_session = None
        self.annotation_token = secrets.token_urlsafe(32)
        super().__init__(*args, **kwargs)

    def annotation_session(self, directory):
        """One kept-alive annotation session per current report content; a rebuilt report replaces it."""
        from .annotations import AnnotationStore, MANIFEST, PreparedContent, load_content
        path = Path(directory) / MANIFEST
        with self.annotation_content_lock:
            stat = path.stat()
            key = (str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if key != self.annotation_content_key or self.annotation_cached_session is None:
                content = PreparedContent(load_content(directory))
                if self.annotation_cached_session:
                    self.annotation_cached_session.close()
                self.annotation_cached_session = None
                self.annotation_cached_session = AnnotationStore.open(
                    content, self.annotation_project_root, keep_alive=True)
                self.annotation_content_key = key
            return self.annotation_cached_session

    def server_close(self):
        self.annotation_jobs.close()
        if self.annotation_cached_session:
            self.annotation_cached_session.close()
        if self.translation:
            self.translation.close()
        super().server_close()

    def server_bind(self):
        # HTTPServer 默认反查监听地址的主机名；部分 macOS 环境会在这里长期等待 DNS。
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def create_server(directory: Path, port: int = 0, *, translation_config: TranslationConfig | None = None,
                  cache_directory: Path | None = None, annotation_project_root: Path | None = None) -> ReportServer:
    root = directory.expanduser().resolve()
    entry = root / "report.html"
    if not root.is_dir() or not entry.is_file() or not entry.resolve().is_relative_to(root):
        raise ValueError("报告目录必须包含目录内的 report.html 文件")
    if translation_config:
        cache_directory = (cache_directory or root.parent / "translation-cache").expanduser().absolute()
        if (cache_directory == root or cache_directory in root.parents or root in cache_directory.parents
                or any(p.is_symlink() for p in (cache_directory, *cache_directory.parents))):
            raise ValueError("翻译缓存目录须独立于报告目录，且不可包含符号链接")
    server = ReportServer(("127.0.0.1", port), partial(ReportHandler, directory=str(root)))
    server.translation_token = secrets.token_urlsafe(32)
    server.annotation_project_root = annotation_project_root
    try:
        if translation_config:
            server.translation = TranslationSession(root, translation_config, cache_directory)
    except Exception:
        server.server_close()
        raise
    return server


def _report_directory(path: Path, config: dict) -> Path:
    """论文入口复用启动目录的输出配置；不重新生成审阅报告。"""
    path = path.expanduser()
    if path.is_file() and path.name == "report.html":
        return path.parent
    if path.suffix.lower() == ".tex" and not path.is_dir():
        if not path.is_file():
            raise ValueError(f"论文入口不存在：{path}")
        directory = Path(config.get("output", ".latex-review/latest")).expanduser()
        if not (directory / "report.html").is_file():
            raise ValueError(f"尚未找到已有报告：{directory / 'report.html'}；请先运行 latex-review，"
                             "使用 --output 生成的报告请直接传入报告目录")
        return directory
    return path


def preview_report(directory: Path, *, config: Path | None = None,
                   translation_cache_dir: Path | None = None, root: Path | None = None,
                   entry: Path | None = None, port: int = 0, open_browser: bool = False,
                   wait: bool = False, startup_timeout: float = 120) -> int:
    """Open a managed report directly; CLI parsing stays in main."""
    directory = Path(directory)
    try:
        from .cli import _config
        from .identity import identity_hash
        from .local_reports import report_identity
        config_path = (config or Path('.latex-review.toml')).expanduser().resolve()
        config_explicit = config is not None
        values = _config(config_path, config_explicit)
        shortcut = directory if directory.suffix.lower() == '.tex' else None
        directory = _report_directory(directory, values).resolve()
        identity = report_identity(directory, root=root,
                                   entry=entry or shortcut)
        request = {**identity, 'kind': 'diff', 'directory': str(directory), 'port': port,
                   'config': str(config_path), 'config_explicit': config_explicit,
                   'cache': str((translation_cache_dir or directory.parent / 'translation-cache').expanduser().resolve()),
                   'configuration_fingerprint': identity_hash(values)}
        from .local_client import ensure, follow
        result = ensure(request, timeout=startup_timeout)
        print(result['url'], flush=True)
        print('差异报告服务已就绪；使用latex-review-local stop-paper显式停止。', file=sys.stderr, flush=True)
        if open_browser:
            try:
                opened = webbrowser.open(result['url'])
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                print('无法自动打开浏览器；请手动打开上述地址。', file=sys.stderr)
        if wait:
            follow(request['paper_id'], 'diff')
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'预览启动失败：{exc}', file=sys.stderr)
        return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='启动或复用受管差异报告服务；命令返回后服务继续运行')
    parser.add_argument('directory', type=Path, help='已有报告目录、report.html或论文入口')
    parser.add_argument('--port', type=int, default=0, help='工作进程端口；0自动分配')
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--translation-cache-dir', type=Path)
    parser.add_argument('--annotation-project-root', type=Path)
    parser.add_argument('--entry', type=Path, help='旧报告缺少身份时的论文入口')
    parser.add_argument('--root', type=Path, help='旧报告的真实论文根目录')
    parser.add_argument('--wait', action='store_true', help='附着状态；Ctrl+C只断开客户端')
    parser.add_argument('--startup-timeout', type=float, default=120)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535 or args.startup_timeout <= 0:
        parser.error('端口或启动超时无效')
    return preview_report(args.directory, config=args.config, translation_cache_dir=args.translation_cache_dir,
                          root=args.root or args.annotation_project_root, entry=args.entry, port=args.port,
                          open_browser=args.open_browser, wait=args.wait, startup_timeout=args.startup_timeout)


if __name__ == '__main__':
    raise SystemExit(main())
