"""通过本机 HTTP 预览已生成的报告目录。"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
from socketserver import TCPServer
import sys


class ReportHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        root = Path(self.directory).resolve()
        try:
            target = Path(self.translate_path(self.path)).resolve()
            if not target.is_relative_to(root):
                self.send_error(403, "报告目录之外的资源不可访问")
                return None
            if target.is_dir():
                self.send_error(404, "请访问 report.html")
                return None
        except (OSError, ValueError, RuntimeError):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class ReportServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer 默认反查监听地址的主机名；部分 macOS 环境会在这里长期等待 DNS。
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def create_server(directory: Path, port: int = 0) -> ReportServer:
    root = directory.expanduser().resolve()
    entry = root / "report.html"
    if not root.is_dir() or not entry.is_file() or not entry.resolve().is_relative_to(root):
        raise ValueError("报告目录必须包含目录内的 report.html 文件")
    return ReportServer(("127.0.0.1", port), partial(ReportHandler, directory=str(root)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在本机预览已生成的报告，按 Ctrl+C 停止。")
    parser.add_argument("directory", type=Path, help="包含 report.html 的报告目录")
    parser.add_argument("--port", type=int, default=0, help="监听端口，默认 0 表示自动选择")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 至 65535 之间")
    try:
        server = create_server(args.directory, args.port)
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
