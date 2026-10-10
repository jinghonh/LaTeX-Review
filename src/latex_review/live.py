"""当前论文的本机 HTML 持续预览，不读取 Git 或生成差异。"""
from __future__ import annotations

import argparse
from functools import partial
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import sys
from threading import Thread
import time
import webbrowser

from .contract import ComparisonSource
from .preview import render_preview
from .report import _figure_assets
from .serve import ReportHandler, ReportServer
from .sources import ProjectSource, SourceError
from .structure import parse_project


_RELOAD = '''<script>
let revision = LIVE_REVISION;
async function check() {
  try {
    const state = await (await fetch('live-status.json', {cache:'no-store'})).json();
    document.getElementById('live-status').textContent = state.message;
    if (revision !== null && state.revision !== revision) {
      sessionStorage.setItem('latex-live-scroll', String(window.scrollY));
      location.reload();
    }
    revision = state.revision;
  } catch (_) { document.getElementById('live-status').textContent = '预览服务连接中断。'; }
}
window.addEventListener('load', () => {
  window.scrollTo(0, Number(sessionStorage.getItem('latex-live-scroll') || 0));
});
check(); setInterval(check, 1000);
</script>'''


def fingerprint(root: Path, output: Path) -> str:
    """内容指纹包括新增、删除文件；服务产物不会触发刷新。"""
    digest = sha256()
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in {
            '.git', '.venv', 'venv', '__pycache__', '.latex-review'
        } and not (folder / name).is_symlink()
            and not (folder / name).resolve().is_relative_to(output))
        for name in sorted(files):
            path = folder / name
            if path.is_symlink() or path.resolve().is_relative_to(output):
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def build(source: ProjectSource, output: Path, revision: int = 1) -> list[str]:
    project = parse_project(source.expand())
    assets, diagnostics = _figure_assets(None, project, output, (), 15)
    preview = render_preview(None, project, figure_assets=assets)
    messages = [item.message for item in (*preview.diagnostics, *diagnostics)]
    notice = '<style>.preview-side{max-height:none;overflow:visible}img{max-width:100%;height:auto}</style><p id="live-status" role="status">持续预览已启动。</p>'
    if messages:
        notice += '<details><summary>预览诊断</summary><ul>' + ''.join(
            '<li>' + escape(message) + '</li>' for message in messages) + '</ul></details>'
    html = preview.html.replace('</header>', notice + '</header>').replace('</body>', _RELOAD.replace('LIVE_REVISION', str(revision)) + '</body>')
    _write(output / 'report.html', html)
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='持续预览当前论文的 HTML 内容，不比较版本')
    parser.add_argument('entry', type=Path, help='当前论文入口 .tex 文件')
    parser.add_argument('--root', type=Path, help='论文项目根目录，默认入口文件所在目录')
    parser.add_argument('--output', type=Path, help='预览目录，默认项目内 .latex-review/live')
    parser.add_argument('--open-browser', action='store_true')
    args = parser.parse_args(argv)
    entry = args.entry.resolve()
    root = args.root.resolve() if args.root else entry.parent
    output = args.output.resolve() if args.output else root / '.latex-review' / 'live'
    if not entry.is_file() or not entry.is_relative_to(root) or root.is_relative_to(output):
        parser.error('入口必须位于项目根目录内，输出目录不能包含项目根目录')
    source = ProjectSource('new', root, entry.relative_to(root).as_posix(), ComparisonSource('directory', str(root)))
    output.mkdir(parents=True, exist_ok=True)
    _write(output / 'report.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
           '<title>当前论文持续预览</title><p id="live-status">正在读取当前论文。</p>'
           + _RELOAD.replace('LIVE_REVISION', '0') + '</html>')
    _write(output / 'live-status.json', json.dumps({'revision': 0, 'message': '正在读取当前论文。'}))
    server = ReportServer(('127.0.0.1', 0), partial(ReportHandler, directory=str(output)))
    server.translation_token = ''
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/report.html'
    revision = 0
    previous = None
    try:
        print(f'持续预览：{url}\n按 Ctrl+C 停止。', flush=True)
        while True:
            try:
                current = fingerprint(root, output)
                if current != previous:
                    # 合并连续保存；解析期间的后续保存将在下一轮处理。
                    time.sleep(0.3)
                    current = fingerprint(root, output)
                    previous = current
                    messages = build(source, output, revision + 1)
                    revision += 1
                    message = f'已更新当前论文。预览诊断：{len(messages)} 项。'
                    if revision == 1 and args.open_browser:
                        webbrowser.open(url)
                    print(message, flush=True)
                    _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': message}, ensure_ascii=False))
            except (OSError, ValueError, SourceError) as exc:
                message = f'更新失败，保留上次预览：{exc}'
                print(message, file=sys.stderr, flush=True)
                _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': message}, ensure_ascii=False))
            time.sleep(0.7)
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
