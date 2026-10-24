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
from .serve import create_server
from .translation import TranslationConfig, build_current_units
from .sources import ProjectSource, SourceError
from .structure import parse_project


def fingerprint(root: Path, output: Path, *, cache_directory: Path | None = None) -> str:
    """内容指纹包括新增、删除文件；服务产物不会触发刷新。"""
    digest = sha256()
    from .local_paths import state_directory
    excluded = (output, state_directory()) + ((cache_directory,) if cache_directory else ())
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
    from .project_profile import settings
    from .macros import validate_macros
    configuration = settings(source)
    project = parse_project(source.expand(), macros=validate_macros(configuration.get("macros", {})), include_title=True)
    compile_diagnostics = []
    compiled_link = ""
    if configuration.get("compile", {}).get("enabled", False):
        from .compilation import compile_side
        options = configuration["compile"]
        status, issues = compile_side(source, output, engine=options.get("engine", "pdflatex"),
                                     timeout=options.get("timeout", 90),
                                     sequence=tuple(options.get("sequence", ())),
                                     sandbox=options.get("sandbox_render", False))
        compile_diagnostics = list(issues)
        if status.pdf:
            compiled_link = f'<p><a href="{status.pdf}" target="_blank">打开编译 PDF</a></p>'
            from dataclasses import replace
            numbering = output / "compiled" / source.side / "numbering.json"
            if numbering.is_file():
                project = replace(project, compiled_numbering=json.loads(numbering.read_text()))
    units = build_current_units(project)
    _write(output / "translation-units.json", json.dumps(units, ensure_ascii=False))
    assets, diagnostics = _figure_assets(None, project, output, _pdf_converters(source, output), 15)
    preview = render_preview(None, project, figure_assets=assets,
                             sentence_highlights={(unit['side'], unit['node_id']): frozenset() for unit in units})
    from .annotations import MANIFEST, build_content, content_meta
    annotation_content = build_content(project, preview.html)
    _write(output / MANIFEST, json.dumps(annotation_content, ensure_ascii=False))
    all_diagnostics = [*compile_diagnostics, *preview.diagnostics, *diagnostics]
    messages = [item.message for item in all_diagnostics]
    from .reading import build_reading
    reading = build_reading(project, units, revision=revision)
    style = files('latex_review').joinpath('report.css').read_text(encoding='utf-8')
    diagnostic_items = ''
    for item in all_diagnostics:
        severity = {'info': '信息', 'warning': '警告', 'error': '错误'}.get(item.severity, item.severity)
        location = item.source_new or item.source_old
        position = location.file if location and location.file else '位置未知'
        if location and location.start_line is not None:
            position += f':{location.start_line}'
            if location.start_column is not None:
                position += f':{location.start_column}'
        diagnostic_items += ('<li data-severity="' + escape(item.severity, quote=True) + '">'
                             + '<strong>' + escape(severity) + '</strong> · <code>'
                             + escape(position) + '</code> · ' + escape(item.message) + '</li>')
    header = f'''<header class="report-header">
<div class="report-identity"><div class="report-eyebrow"><span class="report-mark">LaTeX</span>
<span>论文内容预览</span><span class="report-edition">持续预览</span></div>
<p class="report-entry">{escape(source.entry)}</p>
<div class="report-versions"><span class="version-chip version-new"><span>来源</span><strong>当前磁盘论文</strong></span></div></div>
<details class="report-help"><summary>预览说明</summary><div>
<p>内容预览不代表最终编译版式。保存论文后自动更新。</p>{compiled_link}
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


def prepare_request(entry, *, root=None, output=None, config=None, cache=None, poll_files=False):
    from .cli import _source_roots, _config
    from .identity import paper_identity, identity_hash
    entry = Path(entry).expanduser().resolve()
    root = Path(root).expanduser().resolve() if root else _source_roots({'entry': str(entry)})[-1]
    identity = paper_identity(root, entry, require_entry=True)
    from .project_profile import load, output as profile_output
    profile = load(root, identity["entry"])
    default_output = profile_output(root, identity['entry'], 'live') if profile else root / '.latex-review' / 'live'
    output = Path(output).expanduser().resolve() if output else default_output
    storage = root / '.latex-review' / 'annotations'
    cache = Path(cache).expanduser().resolve() if cache else (output.parent / 'translation-cache' if profile else root / '.latex-review' / 'translation-cache')
    for path in (output, cache):
        if profile.get("project", {}).get("read_only") and path.is_relative_to(root):
            raise ValueError("只读项目的输出和缓存必须位于论文目录之外")
        if root.is_relative_to(path) or path == storage or path in storage.parents or storage in path.parents:
            raise ValueError('输出和缓存不可覆盖论文或共享批注存储')
    if output == cache or output in cache.parents or cache in output.parents:
        raise ValueError('输出和缓存不可重叠')
    from .project_profile import directory
    config_path = Path(config).expanduser().resolve() if config else (directory(root) / 'project.toml' if profile else Path('.latex-review.toml').resolve())
    values = _config(config_path, bool(config))
    return {**identity, 'kind': 'live', 'output': str(output), 'config': str(config_path),
            'config_explicit': bool(config), 'cache': str(cache), 'poll_files': bool(poll_files),
            'configuration_fingerprint': identity_hash(values)}


def run_worker(recipe, stop):
    """Internal runtime; public main always uses the resident service."""
    from .file_events import ProjectEvents
    from .output_leases import output_lease
    from .report_rebuild import rebuild_lock
    from .local_state import write_json
    from .cli import _config
    from .project_profile import directory as profile_directory
    from contextlib import ExitStack
    root, output = Path(recipe['root']), Path(recipe['output'])
    cache = Path(recipe['cache'])
    config = _config(Path(recipe['config']), recipe['config_explicit'])
    source = ProjectSource('new', root, recipe['entry'], ComparisonSource('directory', str(root)), configuration=config)
    with ExitStack() as stack:
        stack.enter_context(output_lease(output, wait=False, persistent=True))
        output.mkdir(parents=True, exist_ok=True)
        events = stack.enter_context(ProjectEvents(root, output, cache, force_polling=recipe['poll_files'],
                                                   extra_files=(Path(recipe['config']), profile_directory(root) / 'project.toml')))
        initial = events.generation
        revision = 1
        from .local_reports import copy_report
        from .report_directory import report_directory
        with rebuild_lock(output), report_directory(output) as candidate:
            if recipe.get('candidate'):
                copy_report(Path(recipe['candidate']), candidate.directory, allow_empty=True)
                messages = []
            else:
                messages = build(source, candidate.directory)
        config = _config(Path(recipe['config']), recipe['config_explicit'])
        translation = TranslationConfig.from_mapping(config['translation']) if 'translation' in config else None
        server = create_server(output, translation_config=translation, cache_directory=cache,
                               annotation_project_root=root)
        stack.callback(server.server_close)
        server.local_nonce = recipe['nonce']
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        thread.start()
        _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': '持续预览已启动。'}, ensure_ascii=False))
        write_json(recipe['ready'], {'nonce': recipe['nonce'], 'pid': os.getpid(),
                                    'port': server.server_port, 'directory': str(output)})
        previous = initial
        # A source save between candidate generation and watcher creation must
        # trigger a refresh, even if the new watcher has not seen an event.
        pending = bool(recipe.get('candidate')) and recipe.get('source_fingerprint') != fingerprint(root, output, cache_directory=cache)
        warning = ''
        try:
            while not stop.is_set():
                current = events.wait(previous, timeout=.2)
                if stop.is_set():
                    break
                if current != previous or pending:
                    previous, pending = current, False
                    try:
                        with rebuild_lock(output):
                            with report_directory(output) as candidate:
                                from dataclasses import replace
                                source = replace(source, configuration=_config(Path(recipe["config"]), recipe["config_explicit"]))
                                messages = build(source, candidate.directory, revision + 1)
                        revision += 1
                        message = events.warning + f'已更新当前论文。预览诊断：{len(messages)} 项。'
                    except (OSError, ValueError, RuntimeError, SourceError) as exc:
                        message = f'更新失败，保留上次预览：{exc}'
                    _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': message}, ensure_ascii=False))
                elif events.warning != warning:
                    warning = events.warning
                    _write(output / 'live-status.json', json.dumps({'revision': revision, 'message': warning}, ensure_ascii=False))
        finally:
            server.shutdown()
            thread.join()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='启动或复用受管持续预览，命令返回后服务继续运行')
    parser.add_argument('entry', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--poll-files', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--translation-cache-dir', type=Path)
    parser.add_argument('--wait', action='store_true', help='附着状态；Ctrl+C只断开客户端')
    parser.add_argument('--startup-timeout', type=float, default=120)
    args = parser.parse_args(argv)
    try:
        request = prepare_request(args.entry, root=args.root, output=args.output, config=args.config,
                                  cache=args.translation_cache_dir, poll_files=args.poll_files)
        from .local_client import ensure, follow
        result = ensure(request, timeout=args.startup_timeout)
        print(result['url'], flush=True)
        print('持续预览已就绪；使用latex-review-local stop-paper显式停止。', file=sys.stderr, flush=True)
        if args.open_browser:
            try:
                opened = webbrowser.open(result['url'])
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                print('无法自动打开浏览器；请手动打开上述地址。', file=sys.stderr)
        if args.wait:
            follow(request['paper_id'], 'live')
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'持续预览启动失败：{exc}', file=sys.stderr)
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
