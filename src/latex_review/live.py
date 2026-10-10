"""当前论文的本机 HTML 持续预览，不读取 Git 或生成差异。"""
from __future__ import annotations

import argparse
from functools import partial
from hashlib import sha256
from html import escape
from importlib.resources import files
import json
import os
import re
from pathlib import Path
import sys
from threading import Thread
import time
import webbrowser

from .contract import ComparisonSource
from .preview import render_preview
from .report import _figure_assets, _pdf_converters
from .serve import ReportHandler, ReportServer
from .sources import ProjectSource, SourceError
from .structure import parse_project


def _reader_script(revision: int) -> str:
    script = files('latex_review').joinpath('live_reader.js').read_text(encoding='utf-8')
    return '<script>' + script.replace('LIVE_REVISION', str(revision)) + '</script>'


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
    assets, diagnostics = _figure_assets(None, project, output, _pdf_converters(source, output), 15)
    preview = render_preview(None, project, figure_assets=assets)
    messages = [item.message for item in (*preview.diagnostics, *diagnostics)]
    style = files('latex_review').joinpath('report.css').read_text(encoding='utf-8')
    diagnostic_items = ''.join('<li>' + escape(message) + '</li>' for message in messages)
    header = f'''<header class="report-header">
<div class="report-identity"><div class="report-eyebrow"><span class="report-mark">LaTeX</span>
<span>论文内容预览</span><span class="report-edition">持续预览</span></div>
<p class="report-entry">{escape(source.entry)}</p>
<div class="report-versions"><span class="version-chip version-new"><span>来源</span><strong>当前磁盘论文</strong></span></div></div>
<details class="report-help"><summary>预览说明</summary><div>
<p>内容预览不代表最终编译版式。保存论文后自动更新。</p>
<p id="math-status" role="status">正在加载在线公式排版。</p></div></details></header>
<section class="report-controls live-controls" aria-label="持续预览工具栏">
<details id="live-navigation"><summary>搜索与章节导航</summary><div class="live-navigation-panel">
<label for="live-search">搜索当前论文</label><input type="search" id="live-search" placeholder="输入文字">
<p id="live-search-status" role="status"></p><ol id="live-results"></ol>
<label for="live-sections">章节</label><select id="live-sections"><option value="">选择章节</option></select>
</div></details><p id="live-status" role="status">持续预览已启动。</p>
<a href="#live-diagnostics">预览诊断 · {len(messages)}</a></section>'''
    supplement = ('<details class="report-supplement" id="live-diagnostics"><summary>预览诊断 · '
                  + str(len(messages)) + '</summary><ul>'
                  + (diagnostic_items or '<li>没有额外诊断。</li>') + '</ul></details>')
    html = preview.html.replace('</style>', style + '</style>', 1)
    html = html.replace('<body>', '<body class="reading-view live-view">', 1)
    html = html.replace('<title>LaTeX 内容预览</title>', '<title>当前论文持续预览 · ' + escape(source.entry) + '</title>', 1)
    html = re.sub(r'<header>.*?</header>', lambda _: header, html, count=1, flags=re.S)
    html = html.replace('</main>', '</main>' + supplement, 1)
    html = html.replace('</body>', _reader_script(revision) + '</body>')
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
           + _reader_script(0) + '</html>')
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
