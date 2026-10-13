"""当前论文的本机 HTML 持续预览，不读取 Git 或生成差异。"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
from threading import Thread
import webbrowser

from .contract import ComparisonSource
from .preview import render_preview
from .report import _figure_assets, _pdf_converters
from .serve import ReportServer, create_server
from .translation import TranslationConfig, build_current_units
from .sources import ProjectSource, SourceError
from .structure import parse_project


def fingerprint(root: Path, output: Path, *, cache_directory: Path | None = None) -> str:
    """内容指纹包括新增、删除文件；服务产物不会触发刷新。"""
    digest = sha256()
    excluded = (output,) + ((cache_directory,) if cache_directory else ())
    for current, dirs, files in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in {
            '.git', '.venv', 'venv', '__pycache__', '.latex-review'
        } and not (folder / name).is_symlink()
            and not any((folder / name).resolve().is_relative_to(path) for path in excluded))
        for name in sorted(files):
            path = folder / name
            if path.is_symlink() or any(path.resolve().is_relative_to(excluded_path) for excluded_path in excluded):
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def build(source: ProjectSource, output: Path, revision: int = 1) -> list[str]:
    project = parse_project(source.expand(), include_title=True)
    units = build_current_units(project)
    _write(output / "translation-units.json", json.dumps(units, ensure_ascii=False))
    assets, diagnostics = _figure_assets(None, project, output, _pdf_converters(source, output), 15)
    preview = render_preview(None, project, figure_assets=assets,
                             sentence_highlights={(unit['side'], unit['node_id']): frozenset() for unit in units})
    from .annotations import MANIFEST, build_content, content_meta
    annotation_content = build_content(project, preview.html)
    _write(output / MANIFEST, json.dumps(annotation_content, ensure_ascii=False))
    messages = [item.message for item in (*preview.diagnostics, *diagnostics)]
    from .reading import build_reading
    reading = build_reading(project, units, revision=revision)
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
<div class="live-toolbar-navigation">
<details id="live-navigation"><summary>搜索</summary><div class="live-navigation-panel">
<label for="live-search">搜索当前论文</label><input type="search" id="live-search" placeholder="输入文字">
<p id="live-search-status" role="status"></p><ol id="live-results"></ol>
</div></details>
<a href="#live-diagnostics">预览诊断 · {len(messages)}</a></div>
<div class="live-toolbar-reading">
{reading.translation_mode}
{reading.translation_actions}</div>
<div class="live-toolbar-status"><p id="live-status" role="status" hidden></p>
{reading.translation_status}</div></section>'''
    supplement = ('<details class="report-supplement" id="live-diagnostics"><summary>预览诊断 · '
                  + str(len(messages)) + '</summary><ul>'
                  + (diagnostic_items or '<li>没有额外诊断。</li>') + '</ul></details>')
    html = preview.compose(
        title='当前论文持续预览 · ' + source.entry, style=style,
        head=content_meta(annotation_content), body_class='reading-view live-view',
        before_main=header + reading.section_toggle + reading.section_directory, after_main=supplement,
        scripts_after_math=reading.scripts)
    from .delivery import local_report_html
    html = local_report_html(html)
    _write(output / 'report.html', html)
    from .report_rebuild import save_live_recipe
    save_live_recipe(output, source)
    return messages


def refresh_translation(server: ReportServer, output: Path) -> None:
    """重建后仅恢复当前内容的缓存，不自动提交翻译请求。"""
    if server.translation is not None:
        server.translation.refresh()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='持续预览当前论文的 HTML 内容，不比较版本')
    parser.add_argument('entry', type=Path, help='当前论文入口 .tex 文件')
    parser.add_argument('--root', type=Path, help='论文项目根目录，默认入口文件所在目录')
    parser.add_argument('--output', type=Path, help='预览目录，默认项目内 .latex-review/live')
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--poll-files', action='store_true', help='使用元数据轮询，适用于不支持原生事件的文件系统')
    parser.add_argument('--config', type=Path, help='项目配置文件，默认当前目录 .latex-review.toml')
    parser.add_argument('--translation-cache-dir', type=Path, help='独立翻译缓存目录')
    args = parser.parse_args(argv)
    entry = args.entry.resolve()
    from .cli import _source_roots
    root = args.root.resolve() if args.root else _source_roots({'entry': str(entry)})[-1]
    output = args.output.resolve() if args.output else root / '.latex-review' / 'live'
    if not entry.is_file() or not entry.is_relative_to(root) or root.is_relative_to(output):
        parser.error('入口必须位于项目根目录内，输出目录不能包含项目根目录')
    storage = root / '.latex-review' / 'annotations'
    if output == storage or output in storage.parents or storage in output.parents:
        parser.error('输出目录不能覆盖项目共享批注存储')
    source = ProjectSource('new', root, entry.relative_to(root).as_posix(), ComparisonSource('directory', str(root)))
    output.mkdir(parents=True, exist_ok=True)
    cache_directory = (args.translation_cache_dir or root / '.latex-review' / 'translation-cache').expanduser().absolute()
    if root.is_relative_to(cache_directory):
        parser.error('翻译缓存目录不能包含论文项目根目录')
    if cache_directory == storage or cache_directory in storage.parents or storage in cache_directory.parents:
        parser.error('翻译缓存目录不能覆盖项目共享批注存储')
    from .file_events import ProjectEvents
    events = ProjectEvents(root, output, cache_directory, force_polling=args.poll_files,
                           extra_files=((args.config or Path('.latex-review.toml')).absolute(),))
    events.__enter__()
    try:
        from .cli import _config
        config = _config(args.config or Path('.latex-review.toml'), bool(args.config))
        translation = TranslationConfig.from_mapping(config['translation']) if 'translation' in config else None
        initial = events.generation
        messages = build(source, output)
        server = create_server(output, translation_config=translation, cache_directory=cache_directory,
                               annotation_project_root=root)
    except (OSError, ValueError, RuntimeError, SourceError) as exc:
        events.__exit__()
        print(f'预览启动失败：{exc}', file=sys.stderr)
        return 4
    _write(output / 'live-status.json', json.dumps({'revision': 1, 'message': f'持续预览已启动。预览诊断：{len(messages)} 项。'}, ensure_ascii=False))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/report.html'
    revision = 1
    previous = initial
    try:
        if args.open_browser:
            webbrowser.open(url)
        print(f'持续预览：{url}\n按 Ctrl+C 停止。', flush=True)
        warning = ''
        while True:
            try:
                current = events.wait(previous)
                if events.warning != warning:
                    warning = events.warning
                    print(warning, file=sys.stderr, flush=True)
                    _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': warning}, ensure_ascii=False))
                if current != previous:
                    # Consume only events seen before building; later saves remain pending.
                    previous = current
                    from .report_rebuild import rebuild_lock
                    with rebuild_lock(output):
                        messages = build(source, output, revision + 1)
                        refresh_translation(server, output)
                    revision += 1
                    message = events.warning + f'已更新当前论文。预览诊断：{len(messages)} 项。'
                    print(message, flush=True)
                    _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': message}, ensure_ascii=False))
            except (OSError, ValueError, SourceError) as exc:
                message = f'更新失败，保留上次预览：{exc}'
                print(message, file=sys.stderr, flush=True)
                _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': message}, ensure_ascii=False))
    except KeyboardInterrupt:
        return 0
    finally:
        events.__exit__()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
