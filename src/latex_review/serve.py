"""通过本机 HTTP 预览已生成的报告目录。"""

from __future__ import annotations

import argparse
import hmac
import json
import secrets
from functools import partial
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
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/report":
            if not self._local_request():
                return
            from .annotations import load_content
            value = {"report_directory": str(Path(self.directory).resolve()),
                     "report_url": f"http://127.0.0.1:{self.server.server_port}/report.html"}
            try:
                content = load_content(self.directory)
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

    def _annotation_context(self):
        from .annotations import AnnotationStore, load_content
        content = load_content(self.directory)
        store = AnnotationStore.for_content(content, getattr(self.server, "annotation_project_root", None))
        return content, store

    def _annotation_get(self, path):
        if not self._local_request():
            return
        try:
            if path == '/api/annotations/jobs':
                return self._json(self.server.annotation_jobs.active())
            if path.startswith('/api/annotations/jobs/'):
                return self._json(self.server.annotation_jobs.snapshot(path.rsplit('/', 1)[-1]))
            content, store = self._annotation_context()
            if path == "/api/annotations/content":
                self._json(content)
            elif path == "/api/annotations/status":
                from .annotation_output import page_projection, thread_summary
                view = parse_qs(urlsplit(self.path).query).get('view', ['summary'])[0]
                if view == 'revision':
                    value = store.metadata(content)
                else:
                    value = store.read(content)
                    if view == 'page':
                        value = page_projection(value)
                    elif view != 'full':
                        value['threads'] = [thread_summary(t) for t in value['threads']]
                self._json({**value, "token": self.server.annotation_token})
            elif path == '/api/annotations/history':
                thread_id = parse_qs(urlsplit(self.path).query).get('thread_id', [''])[0]
                thread = store.export()['threads'].get(thread_id)
                if not thread:
                    return self._json({'message': '批注不存在'}, 404)
                self._json({'thread_id': thread_id, 'history': thread['history']})
            elif path == "/api/annotations/export":
                self._json(store.export())
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
            envelope = json.loads(self.rfile.read(size))
            if parse_qs(urlsplit(self.path).query).get('async') == ['1']:
                jobs = self.server.annotation_jobs
                directory = self.directory
                server = self.server
                # Resolve content inside the worker so queued tasks cannot use a stale manifest.
                def run(progress):
                    return self._execute_annotation(envelope, progress, directory, server)
                return self._json(jobs.submit(run), 202)
            value = self._execute_annotation(envelope, None, self.directory, self.server)
            self._json(value)
        except AnnotationConflict as exc:
            self._json({"message": str(exc), "error": "conflict"}, 409)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            self._json({"message": str(exc), "error": "invalid_request"}, 400)

    @staticmethod
    def _execute_annotation(envelope, progress, directory, server):
        from .annotations import AnnotationStore, execute_request, load_content
        from .annotation_output import page_projection
        content = load_content(directory)
        store = AnnotationStore.for_content(content, server.annotation_project_root)
        if progress:
            progress('validate', 0, 0)
        value = (execute_request(store, content, envelope, progress=progress) if progress
                 else execute_request(store, content, envelope))
        if (value.get('result') or {}).get('source_changed'):
            from .report_rebuild import rebuild_report
            if progress:
                progress('rebuild', 0, 0)
            value['result']['rebuild'] = rebuild_report(directory)
            if value['result']['rebuild']['status'] == 'updated' and server.translation:
                warning = server.translation.refresh()
                if warning:
                    value['result']['rebuild']['translation_warning'] = warning
        return page_projection(value) if 'threads' in value else value

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
        self.annotation_token = secrets.token_urlsafe(32)
        super().__init__(*args, **kwargs)

    def server_close(self):
        self.annotation_jobs.close()
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在本机预览已生成的报告，按 Ctrl+C 停止。")
    parser.add_argument("directory", type=Path, help="报告目录、report.html，或论文 .tex 入口（预览当前目录的已有报告）")
    parser.add_argument("--port", type=int, default=0, help="监听端口，默认 0 表示自动选择")
    parser.add_argument("--open-browser", action="store_true", help="启动预览后自动打开默认浏览器")
    parser.add_argument("--config", type=Path, help="项目配置文件，默认当前目录 .latex-review.toml；翻译设置覆盖用户全局默认值")
    parser.add_argument("--translation-cache-dir", type=Path, help="翻译缓存目录，默认报告目录同级的 translation-cache")
    parser.add_argument("--annotation-project-root", type=Path, help="批注所属论文项目根目录；须与报告项目身份一致")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 至 65535 之间")
    try:
        from .cli import _config
        config = _config(args.config or Path(".latex-review.toml"), bool(args.config))
        translation = TranslationConfig.from_mapping(config["translation"]) if "translation" in config else None
        directory = _report_directory(args.directory, config)
        server = create_server(directory, args.port, translation_config=translation,
                               cache_directory=args.translation_cache_dir, annotation_project_root=args.annotation_project_root)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"预览启动失败：{exc}", file=sys.stderr)
        return 4

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with server:
            address = f"http://127.0.0.1:{server.server_port}/report.html"
            print(address, flush=True)
            print("预览已启动；按 Ctrl+C 或发送终止信号停止。", file=sys.stderr, flush=True)
            if args.open_browser:
                try:
                    opened = webbrowser.open(address)
                except (OSError, webbrowser.Error):
                    opened = False
                if not opened:
                    print("无法自动打开浏览器；请手动打开上述地址。", file=sys.stderr, flush=True)
            server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
