"""通过本机 HTTP 预览已生成的报告目录。"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import secrets
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
from socketserver import TCPServer
import sys
from urllib.parse import urlsplit

from .translation import RULE_VERSION, TranslationConfig, TranslationError, TranslationManager


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
            from .report import _single_file_html
            try:
                root = Path(self.directory).resolve()
                entry = root / "report.html"
                if entry.is_symlink():
                    raise ValueError("报告入口不可为符号链接")
                html = entry.read_text(encoding="utf-8")
                if manager:
                    results = json.dumps(manager.snapshot()["results"], ensure_ascii=False).replace("<", "\\u003c")
                    html = re.sub(r'(<script type="application/json" id="translation-results">).*?(</script>)',
                                  lambda m: m[1] + results + m[2], html, flags=re.S)
                content = _single_file_html(html, root).encode()
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
                manager.submit(body.get("units"))
            elif path == "/api/translation/cancel":
                manager.cancel()
            else:
                return self._json({"message": "未知的翻译接口"}, 404)
            self._json(manager.snapshot())
        except (ValueError, UnicodeError) as exc:
            self._json({"message": str(exc) if isinstance(exc, TranslationError) else "翻译请求格式无效"}, 400)

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
    translation: TranslationManager | None = None

    def server_close(self):
        if self.translation:
            self.translation.close()
        super().server_close()

    def server_bind(self):
        # HTTPServer 默认反查监听地址的主机名；部分 macOS 环境会在这里长期等待 DNS。
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def create_server(directory: Path, port: int = 0, *, translation_config: TranslationConfig | None = None,
                  cache_directory: Path | None = None) -> ReportServer:
    root = directory.expanduser().resolve()
    entry = root / "report.html"
    if not root.is_dir() or not entry.is_file() or not entry.resolve().is_relative_to(root):
        raise ValueError("报告目录必须包含目录内的 report.html 文件")
    units = []
    if translation_config:
        manifest = root / "translation-units.json"
        if manifest.is_symlink():
            raise ValueError("翻译清单不可为符号链接")
        if manifest.is_file():
            units = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(units, list) or any(not isinstance(unit, dict)
                    or unit.get("rule_version") != RULE_VERSION for unit in units)):
                raise ValueError("翻译清单的保护规则已更新或格式无效，请重新生成报告后启动预览服务")
        cache_directory = (cache_directory or root.parent / "translation-cache").expanduser().absolute()
        if (cache_directory == root or cache_directory in root.parents or root in cache_directory.parents
                or any(p.is_symlink() for p in (cache_directory, *cache_directory.parents))):
            raise ValueError("翻译缓存目录须独立于报告目录，且不可包含符号链接")
    server = ReportServer(("127.0.0.1", port), partial(ReportHandler, directory=str(root)))
    server.translation_token = secrets.token_urlsafe(32)
    try:
        if translation_config:
            server.translation = TranslationManager(units, translation_config, cache_directory)
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
    parser.add_argument("--config", type=Path, help="项目配置文件，默认当前目录 .latex-review.toml；翻译设置覆盖用户全局默认值")
    parser.add_argument("--translation-cache-dir", type=Path, help="翻译缓存目录，默认报告目录同级的 translation-cache")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 至 65535 之间")
    try:
        from .cli import _config
        config = _config(args.config or Path(".latex-review.toml"), bool(args.config))
        translation = TranslationConfig.from_mapping(config["translation"]) if "translation" in config else None
        directory = _report_directory(args.directory, config)
        server = create_server(directory, args.port, translation_config=translation,
                               cache_directory=args.translation_cache_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"预览启动失败：{exc}", file=sys.stderr)
        return 4

    def stop(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with server:
            print(f"http://127.0.0.1:{server.server_port}/report.html", flush=True)
            print("预览已启动；按 Ctrl+C 或发送终止信号停止。", file=sys.stderr, flush=True)
            server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
