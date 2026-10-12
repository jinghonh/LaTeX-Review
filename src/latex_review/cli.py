"""无编译审阅命令行。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

from .cache import OWNER_FILE, ParseCache, is_managed_cache_file, is_owned_stage, read_owner_secret, stage_prefix
from .comparison import compare_projects
from .compilation import compile_side, skipped_side
from .contract import Diagnostic, SourceLocation
from .report import ReportPathError, write_cache_metadata, write_failure_diagnostics, write_report, write_source_fallback
from .sources import SourceError, resolve_sources
from .structure import parse_project
from .macros import validate_macros
from .visual import build_visual
from .run_manifest import baseline, new_manifest, finish


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, f"latex-review: 错误：{message}；请运行 --help 查看用法。\n")


class ConfigurationError(ValueError):
    pass


def global_config_path() -> Path:
    """用户级翻译默认配置；项目位置和比较设置继续由项目选择。"""
    configured = os.environ.get("XDG_CONFIG_HOME")
    directory = Path(configured).expanduser() if configured else Path.home() / ".config"
    if not directory.is_absolute():
        raise ConfigurationError("XDG_CONFIG_HOME 必须是绝对路径")
    return directory / "latex-review" / "config.toml"


def _read_config(path: Path, explicit: bool) -> dict:
    if not path.exists() and not explicit:
        return {}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"配置文件 {path} 无法读取或解析：{exc}") from exc
    return data


def _merge_translation(global_values: dict, project_values: dict) -> dict:
    for values in (global_values, project_values):
        if not isinstance(values, dict):
            raise ConfigurationError("配置 [translation] 必须是表")
        if "glossary" in values and not isinstance(values["glossary"], dict):
            raise ConfigurationError("翻译术语表必须是表")
    merged = {**global_values, **project_values}
    if "glossary" in global_values or "glossary" in project_values:
        merged["glossary"] = {**global_values.get("glossary", {}), **project_values.get("glossary", {})}
    return merged


def _config(path: Path, explicit: bool) -> dict:
    global_path = global_config_path()
    shared = _read_config(global_path, False)
    if extra := set(shared) - {"translation"}:
        raise ConfigurationError(f"全局配置 {global_path} 仅支持 [translation]，含未知字段：{', '.join(sorted(extra))}")
    data = _read_config(path.expanduser(), explicit)
    if "translation" in shared or "translation" in data:
        data["translation"] = _merge_translation(shared.get("translation", {}), data.get("translation", {}))
    allowed = {"entry", "ignore", "render", "diff", "git", "output", "macros", "compile", "translation"}
    if extra := set(data) - allowed:
        raise ConfigurationError(f"配置含未知字段：{', '.join(sorted(extra))}")
    for key in ("entry", "output"):
        if key in data and (not isinstance(data[key], str) or not data[key]):
            raise ConfigurationError(f"配置 {key} 必须是非空字符串")
    if "ignore" in data and (not isinstance(data["ignore"], list) or
                             any(not isinstance(item, str) or not item for item in data["ignore"])):
        raise ConfigurationError("配置 ignore 必须是字符串数组")
    sections = {"render": {"math": str, "copy_assets": bool, "show_unknown_macros": bool},
                "diff": {"comments": bool, "move_detection": bool, "citation_semantics": bool,
                         "formula_token_diff": bool},
                "git": {"default_old": str, "default_new": str},
                "compile": {"enabled": bool, "new_only": bool, "engine": str,
                            "timeout": int, "sandbox_render": bool}}
    for section, fields in sections.items():
        values = data.get(section, {})
        if not isinstance(values, dict):
            raise ConfigurationError(f"配置 [{section}] 必须是表")
        if extra := set(values) - set(fields):
            raise ConfigurationError(f"配置 [{section}] 含未知字段：{', '.join(sorted(extra))}")
        for key, value in values.items():
            if type(value) is not fields[key] or (isinstance(value, str) and not value):
                raise ConfigurationError(f"配置 [{section}].{key} 类型或内容无效")
    fixed = (("render", "copy_assets", True), ("render", "show_unknown_macros", True),
             ("diff", "move_detection", False),
             ("diff", "citation_semantics", True), ("diff", "formula_token_diff", True))
    for section, key, expected in fixed:
        if data.get(section, {}).get(key, expected) != expected:
            raise ConfigurationError(f"首版不支持 [{section}].{key} 的该值")
    try:
        validate_macros(data.get("macros", {}))
        if "translation" in data:
            from .translation import TranslationConfig
            TranslationConfig.from_mapping(data["translation"])
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    return data


def _source_options(args, config: dict) -> dict:
    if args.entry_option is not None:
        if args.paths or args.old_revision or args.new_revision or not (args.old_dir and args.new_dir):
            raise SourceError("conflicting_modes", None, "--entry 仅可与 --old-dir、--new-dir 并用")
        return dict(entry=args.entry_option, old_dir=args.old_dir, new_dir=args.new_dir)
    if len(args.paths) == 2:
        if any((args.old_dir, args.new_dir, args.old_revision, args.new_revision)):
            raise SourceError("conflicting_modes", None, "独立文件模式不能与目录或 Git 提交参数并用")
        return dict(old_file=args.paths[0], new_file=args.paths[1])
    if len(args.paths) > 2 or args.old_dir or args.new_dir:
        raise SourceError("invalid_arguments", None, "目录模式须指定 --entry；其他模式请输入一个入口或两个文件")
    entry = args.paths[0] if args.paths else config.get("entry")
    if not entry:
        raise SourceError("invalid_arguments", None, "请输入论文入口或在配置中指定 entry")
    git = config.get("git", {})
    old = args.old_revision if args.old_revision is not None else git.get("default_old", "HEAD")
    new = args.new_revision if args.new_revision is not None else git.get("default_new", "worktree")
    if old == "worktree" or not old or not new:
        raise SourceError("invalid_arguments", None, "修改前须为 Git 提交，修改后须为提交或 worktree")
    return dict(entry=entry, old_revision=old, new_revision=new)


def _prepare_output(path: Path) -> None:
    # 首版威胁模型不含并发替换路径的对手。旧结果在本轮运行前失效。
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ReportPathError(f"输出路径含符号链接：{parent}")
    if path.exists():
        if not path.is_dir():
            raise ReportPathError("输出路径已存在且不是目录")
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)


def _check_output_separate(path: Path, options: dict) -> None:
    def parent_of(value: str, directory: bool = False) -> Path:
        normalized = Path(os.path.abspath(Path(value).expanduser()))
        return normalized if directory else normalized.parent
    if "old_file" in options:
        roots = (parent_of(options["old_file"]), parent_of(options["new_file"]))
    elif "old_dir" in options:
        roots = (parent_of(options["old_dir"], True), parent_of(options["new_dir"], True))
    else:
        roots = (parent_of(options["entry"]),)
    if any(path == root or path in root.parents for root in roots):
        raise ConfigurationError("输出目录不能覆盖论文来源目录或其上级目录")
    for root in _source_roots(options):
        storage = root / ".latex-review" / "annotations"
        if path == storage or path in storage.parents or storage in path.parents:
            raise ConfigurationError("输出目录不能覆盖项目共享批注存储")


def _source_roots(options: dict) -> tuple[Path, ...]:
    if "old_dir" in options:
        return tuple(Path(options[key]).expanduser().resolve() for key in ("old_dir", "new_dir"))
    if "old_file" in options:
        return tuple(Path(options[key]).expanduser().resolve().parent for key in ("old_file", "new_file"))
    entry = Path(options["entry"]).expanduser().resolve()
    try:
        probe = subprocess.run(("git", "-C", str(entry.parent), "rev-parse", "--show-toplevel"),
                               capture_output=True, check=False)
    except OSError:
        return (entry.parent,)
    return (Path(os.fsdecode(probe.stdout.strip())).resolve() if probe.returncode == 0 else entry.parent,)


def _clear_cache(directory: Path, options: dict, *, managed_default: bool) -> None:
    """只删除缓存格式文件；任何来源根目录或非缓存内容都拒绝递归清理。"""
    roots = _source_roots(options)
    if any(directory == root or directory in root.parents for root in roots):
        raise ConfigurationError("缓存清理目录不能覆盖论文来源目录")
    if not managed_default and any(root in directory.parents for root in roots):
        raise ConfigurationError("指定缓存目录位于论文来源内，拒绝清理")
    if directory.exists() and not directory.is_dir():
        raise ConfigurationError("缓存路径已存在且不是目录")
    try:
        secret = read_owner_secret(directory)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigurationError(f"缓存所有权标记无效：{exc}") from exc
    prefix = stage_prefix(directory)
    stages = tuple(entry for entry in directory.parent.iterdir() if entry.name.startswith(prefix)
                   and entry.name.endswith(".tmp")) if directory.parent.is_dir() else ()
    if any(entry.is_symlink() or not entry.is_file() or not is_owned_stage(directory, entry, secret)
           for entry in stages):
        raise ConfigurationError("缓存暂存目录含无法确认归属的文件，拒绝清理")
    entries = tuple(directory.iterdir()) if directory.exists() else ()
    if any((secret is None or not (entry.is_symlink() or entry.is_file())) if entry.name == OWNER_FILE else
           (entry.is_symlink() or not entry.is_file() or
            not ((re.fullmatch(r"[0-9a-f]{64}\.json", entry.name) and is_managed_cache_file(entry)) or
                 (re.fullmatch(r"\.write-[A-Za-z0-9_-]+(?:\.tmp)?", entry.name) and
                  is_managed_cache_file(entry, temporary=True)))) for entry in entries):
        raise ConfigurationError("缓存目录含非缓存文件，拒绝清理")
    for entry in (*entries, *stages):
        entry.unlink()
    if directory.exists():
        directory.rmdir()


def _inspect(options: dict) -> int:
    with resolve_sources(**options) as pair:
        def view(source):
            result = source.expand()
            return {"identity": asdict(source.identity), "entry": source.entry, "text": result.text,
                    "dependencies": [asdict(item) for item in result.dependencies],
                    "segments": [asdict(item) for item in result.source_map.segments],
                    "diagnostics": [asdict(item) for item in result.diagnostics]}
        print(json.dumps({"old": view(pair.old), "new": view(pair.new)}, ensure_ascii=False, sort_keys=True))
    return 0


def _record_failure(output: Path | None, diagnostic: Diagnostic) -> None:
    if output is None:
        return
    try:
        _prepare_output(output)
        write_failure_diagnostics(output, diagnostic)
    except OSError:
        pass  # 输出路径本身不可写时，仅保留标准错误输出。


def _known_output_after_config_error(args) -> Path | None:
    """配置无效时只清除可安全确定的本轮报告目录。"""
    config_path = Path(args.config).expanduser() if args.config else Path(".latex-review.toml")
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    configured = raw.get("output")
    if not args.output and configured is not None and (not isinstance(configured, str) or not configured):
        return None
    target = Path(os.path.abspath(Path(args.output or configured or ".latex-review/latest").expanduser()))
    if target in (Path.cwd(), Path("/")):
        return None
    try:
        if args.old_dir or args.new_dir:
            roots = [Path(value).expanduser().resolve() for value in (args.old_dir, args.new_dir) if value]
        elif len(args.paths) >= 2:
            roots = [Path(value).expanduser().resolve().parent for value in args.paths]
        else:
            entry = args.paths[0] if args.paths else raw.get("entry")
            if not isinstance(entry, str) or not entry:
                return None
            roots = [Path(entry).expanduser().resolve().parent]
        if any(target == root or target in root.parents for root in roots):
            return None
    except (OSError, TypeError, ValueError):
        return None
    return target


def _progress(message: str) -> None:
    print(f"latex-review: {message}", file=sys.stderr, flush=True)


def _source_recovery(code: str) -> None:
    advice = {
        "entry_missing": "检查入口文件是否存在。目录模式使用 --entry 指定相对入口。",
        "entry_unreadable": "检查入口文件的读取权限后重新运行。",
        "invalid_revision": "运行 git log --oneline 查看提交，再修正 --old 或 --new。",
        "root_missing": "检查 --old-dir 和 --new-dir 指向的目录是否存在。",
        "explicit_sources_required": "使用 --old-dir A --new-dir B --entry main.tex，或提供两个独立文件。",
        "invalid_arguments": "运行 latex-review --help 查看对应来源模式的必填参数。",
        "conflicting_modes": "选择 Git、双目录或双文件中的一种来源模式，再运行命令。",
        "entry_outside_root": "把入口放在项目根目录内，并使用相对入口路径。",
        "dependency_outside_root": "检查依赖路径，使依赖保持在项目根目录内。",
        "source_changed": "等待文件保存完成，再重新运行审阅。",
        "git_failed": "检查 Git 错误详情，并运行 git status 确认仓库状态。",
    }
    _progress("恢复操作：" + advice.get(code, "检查上述来源错误和文件读取权限后重新运行。"))


def main(argv: list[str] | None = None, *, configuration: dict | None = None,
         source_exclusions: tuple[Path, ...] = ()) -> int:
    parser = ReviewArgumentParser(prog="latex-review", description="比较论文并生成结构化审阅报告。",
                                  epilog="默认比较 Git HEAD 与磁盘工作区；仅显式 --compile 才运行 TeX。")
    parser.add_argument("paths", nargs="*", help="Git 模式入口，或两个独立源文件")
    parser.add_argument("--entry", dest="entry_option", help="双目录模式共同的相对入口")
    parser.add_argument("--old-dir", help="修改前项目目录")
    parser.add_argument("--new-dir", help="修改后项目目录")
    parser.add_argument("--old", dest="old_revision", help="修改前 Git 提交")
    parser.add_argument("--new", dest="new_revision", help="修改后 Git 提交或 worktree")
    parser.add_argument("--output", help="报告目录，默认 .latex-review/latest")
    parser.add_argument("--open-browser", action="store_true", help="生成报告后启动本机预览并打开默认浏览器；按 Ctrl+C 停止")
    parser.add_argument("--upload", action="store_true", help="生成后通过 SSH 发布静态报告；使用用户级 upload.toml")
    parser.add_argument("--cache-dir", help="解析缓存目录，默认位于报告目录旁")
    parser.add_argument("--no-cache", action="store_true", help="本次绕过解析缓存")
    parser.add_argument("--clear-cache", action="store_true", help="本次运行前清理解析缓存")
    parser.add_argument("--math", help="公式排版依赖，首版仅支持 mathjax")
    parser.add_argument("--format", help="输出格式，首版仅支持 html,json")
    parser.add_argument("--config", help="项目配置文件，默认当前目录 .latex-review.toml；翻译设置覆盖用户全局默认值")
    parser.add_argument("--comments", action=argparse.BooleanOptionalAction, default=None,
                        help="审阅源码注释变化；--no-comments 可覆盖配置")
    parser.add_argument("--inspect-sources", action="store_true", help="仅输出双侧展开来源 JSON")
    parser.add_argument("--compile", action="store_true", help="在隔离副本中用 latexmk 编译双侧论文")
    parser.add_argument("--compile-new-only", action="store_true", help="仅编译修改后版本，须同时启用编译")
    parser.add_argument("--tex-engine", choices=("pdflatex", "xelatex", "lualatex"), help="真实编译引擎")
    parser.add_argument("--compile-timeout", type=int, help="每侧编译超时秒数，默认 90")
    parser.add_argument("--sandbox-render", action="store_true", help="在可验证的 macOS 沙箱中执行复杂宏编译")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('latex-review')}")
    args = parser.parse_args(argv)
    if not args.paths and not args.entry_option and not args.old_dir and not args.new_dir and not args.config and not Path(".latex-review.toml").exists():
        parser.print_help()
        return 0
    output = None
    output_ready = False
    run = new_manifest()
    active_stage = "sources"
    try:
        config = configuration if configuration is not None else _config(
            Path(args.config).expanduser() if args.config else Path(".latex-review.toml"), bool(args.config))
        compile_config = config.get("compile", {})
        compile_enabled = args.compile or compile_config.get("enabled", False)
        new_only = args.compile_new_only or compile_config.get("new_only", False)
        sandbox = args.sandbox_render or compile_config.get("sandbox_render", False)
        engine = args.tex_engine or compile_config.get("engine", "pdflatex")
        timeout = args.compile_timeout if args.compile_timeout is not None else compile_config.get("timeout", 90)
        if engine not in {"pdflatex", "xelatex", "lualatex"} or not 1 <= timeout <= 600:
            raise ConfigurationError("编译引擎或超时无效")
        if (new_only or sandbox) and not compile_enabled:
            raise ConfigurationError("仅编译新版本及沙箱渲染须启用 --compile 或 [compile].enabled")
        if (args.math if args.math is not None else config.get("render", {}).get("math", "mathjax")) != "mathjax":
            raise ConfigurationError("首版仅支持 mathjax 公式排版")
        if args.format is not None and args.format != "html,json":
            raise ConfigurationError("首版固定生成 report.html、diff.json 和 diagnostics.json")
        options = _source_options(args, config)
        if args.inspect_sources:
            return _inspect(options)
        output = Path(os.path.abspath(Path(args.output or config.get("output", ".latex-review/latest")).expanduser()))
        if output in (Path.cwd(), Path("/")):
            raise ConfigurationError("输出目录不能是当前目录或文件系统根目录")
        _check_output_separate(output, options)
        cache_dir = Path(os.path.abspath(Path(args.cache_dir).expanduser())) if args.cache_dir else output.parent / (
            "cache" if output.name == "latest" else ".latex-review-cache")
        if cache_dir == output or cache_dir in output.parents or output in cache_dir.parents:
            raise ConfigurationError("缓存目录不能与报告目录重叠")
        if cache_dir.is_symlink() or any(parent.is_symlink() for parent in cache_dir.parents):
            raise ConfigurationError("缓存路径不能包含符号链接")
        if args.clear_cache:
            _clear_cache(cache_dir, options, managed_default=args.cache_dir is None)
        _prepare_output(output)
        output_ready = True
        if compile_enabled:
            output.mkdir(parents=True, exist_ok=True)
        cache = ParseCache(cache_dir, enabled=not args.no_cache,
                           config={"config": {key: value for key, value in config.items() if key != "translation"}, "math": args.math or "mathjax",
                                   "comments": args.comments if args.comments is not None else config.get("diff", {}).get("comments", False),
                                   "include_title": True})
        _progress("正在读取比较来源")
        with resolve_sources(**options, excluded_paths=(output, cache_dir, output.parent / "translation-cache",
                                                           *source_exclusions,
                                                           *tuple(root / ".latex-review" / "annotations" for root in _source_roots(options))),
                             ignore_patterns=tuple(config.get("ignore", ()))) as pair:
            _progress("正在展开两版来源")
            old_expanded, new_expanded = pair.old.expand(), pair.new.expand()
            run["baselines"] = {"old": baseline(old_expanded), "new": baseline(new_expanded)}
            run["stages"]["sources"] = "complete"
            active_stage = "parse"
            macros = validate_macros(config.get("macros", {}))
            failures = []
            parsed = []
            for expanded in (old_expanded, new_expanded):
                _progress("正在解析" + ("旧稿" if expanded.source.side == "old" else "新稿"))
                try:
                    parsed.append(cache.parse(expanded, parser=lambda source: parse_project(source, macros=macros, include_title=True)))
                except SourceError:
                    raise
                except Exception as exc:
                    parsed.append(None)
                    failures.append((expanded.source.side, str(exc)))
            source_fallback = bool(failures or any(item and any(d.code == "plastex_parse_error" for d in item.diagnostics) for item in parsed))
            run["stages"]["parse"] = "degraded" if source_fallback else "complete"
            active_stage = "compare"
            comparison = None
            if not source_fallback:
                comments = args.comments if args.comments is not None else config.get("diff", {}).get("comments", False)
                _progress("正在比较结构化内容")
                comparison = compare_projects(parsed[0], parsed[1], review_comments=comments)
                run["stages"]["compare"] = "complete"
            statuses = None
            rendering = None
            compile_diagnostics: tuple[Diagnostic, ...] = ()
            if compile_enabled:
                active_stage = "compile"
                built = []
                for source in (pair.old, pair.new):
                    if source.side == "old" and new_only:
                        built.append(skipped_side(output, engine))
                        continue
                    _progress("正在编译" + ("旧稿" if source.side == "old" else "新稿"))
                    peer = pair.new if source.side == "old" else pair.old
                    status, issues = compile_side(source, output, engine=engine, timeout=timeout, sandbox=sandbox,
                                                  related_sources=(peer,))
                    built.append(status)
                    compile_diagnostics += issues
                statuses = tuple(built)
                _progress("正在生成编译页面预览")
                rendering, visual_issues = build_visual(statuses[0], statuses[1], output,
                                                        comparison.document if comparison else None,
                                                        sources=(pair.old, pair.new))
                compile_diagnostics += visual_issues
                run["stages"]["compile"] = "degraded" if any(d.severity in {"warning", "error"} for d in compile_diagnostics) else "complete"
            active_stage = "report"
            _progress("正在生成内容预览并写入报告")
            if source_fallback:
                report = write_source_fallback(old_expanded, new_expanded, output, parse_failures=failures,
                                               parsed=tuple(parsed), extra_diagnostics=compile_diagnostics,
                                               rendering=rendering, statuses=statuses)
            else:
                report = write_report(parsed[0], parsed[1], comparison, output,
                                      extra_diagnostics=compile_diagnostics,
                                      rendering=rendering, statuses=statuses,
                                      annotation_project_root=_source_roots(options)[-1])
            write_cache_metadata(output, cache.metadata())
            from copy import deepcopy
            from .report_rebuild import save_recipe
            effective = deepcopy(config)
            effective['compile'] = dict(enabled=compile_enabled, new_only=new_only, sandbox_render=sandbox,
                                        engine=engine, timeout=timeout)
            effective.setdefault('diff', {})['comments'] = (
                args.comments if args.comments is not None else config.get('diff', {}).get('comments', False))
            save_recipe(output, old_expanded, new_expanded, effective)
            run["stages"]["report"] = "degraded" if source_fallback else "complete"
            run["degradation_reasons"] = [{"code": d.code, "severity": d.severity, "message": d.message}
                                          for d in report.document.diagnostics
                                          if d.rule_status is None and d.severity in {"warning", "error"}]
        print(report.html)
        _progress(f"生成完成：{report.document.summary.changes} 项主变更；报告：{report.html}")
        warnings = [d for d in report.document.diagnostics if d.severity in {"warning", "error"}]
        if warnings:
            _progress(f"需要检查 {len(warnings)} 条诊断；详情：{output / 'diagnostics.json'}")
        if source_fallback:
            _progress("恢复操作：检查结构解析诊断后重新生成；源码降级报告暂不支持全文搜索、审阅记录和续读。")
        if any(d.code == "compile_failed" for d in compile_diagnostics):
            _progress("恢复操作：查看 compiled/ 下的编译日志；确认 latexmk 和所选 TeX 引擎已安装，再修正编译错误。")
        if any(d.code == "sandbox_unavailable" for d in compile_diagnostics):
            _progress("恢复操作：检查沙箱诊断并修复本机隔离环境后重新运行。")
        result = 2 if any(d.rule_status is None and d.severity in {"warning", "error"}
                         for d in report.document.diagnostics) else 0
    except ConfigurationError as exc:
        if output_ready:
            _record_failure(output, Diagnostic("configuration_error", "error", str(exc)))
            run["stages"][active_stage] = "failed"
            run["degradation_reasons"] = [{"code": "configuration_error", "message": str(exc)}]
            finish(output, run, 64)
        print(f"latex-review: 配置错误：{exc}", file=sys.stderr)
        _progress("恢复操作：检查错误中指出的配置文件或选项；运行 latex-review --help 查看用法。")
        return 64
    except SourceError as exc:
        if exc.code in {"invalid_arguments", "conflicting_modes"}:
            print(f"latex-review: 参数错误：{exc.message}", file=sys.stderr)
            _source_recovery(exc.code)
            return 64
        if output_ready:
            unknown = SourceLocation(None, None, None, confidence=0, uncertainty_reason="来源无法读取")
            _record_failure(output, Diagnostic(exc.code, "error", exc.message,
                                               source_old=unknown if exc.side == "old" else None,
                                               source_new=unknown if exc.side == "new" else None))
        print(f"latex-review: 来源错误：{json.dumps(asdict(exc), ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
        _source_recovery(exc.code)
        if output_ready:
            run["stages"][active_stage] = "failed"
            run["degradation_reasons"] = [{"code": exc.code, "message": exc.message}]
            finish(output, run, 4)
        return 4
    except Exception as exc:
        if output_ready:
            _record_failure(output, Diagnostic("internal_error", "error", f"{type(exc).__name__}: {exc}"))
        print(f"latex-review: 内部错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        _progress("恢复操作：保留错误信息和 diagnostics.json，运行 latex-review --version，并向项目提交问题。")
        if output_ready:
            run["stages"][active_stage] = "failed"
            run["degradation_reasons"] = [{"code": "internal_error", "message": str(exc)}]
            finish(output, run, 8)
        return 8
    finish(output, run, result)
    if args.upload:
        from .upload import UploadError, upload_report
        try:
            entry = options.get("entry") or options.get("new_file")
            if options.get("new_dir"):
                entry = str(Path(options["new_dir"]) / entry)
            _progress("正在发布报告")
            _progress(f"已发布：{upload_report(output, Path(entry), config.get('translation'))}")
            run["stages"]["upload"] = "complete"
        except (UploadError, OSError, ValueError) as exc:
            print(f"latex-review: 上传失败，本地报告已保留：{exc}", file=sys.stderr)
            _progress("恢复操作：检查 SSH 连接和 upload.toml；修复后重新发布，本地报告可继续阅读。")
            result = 16
            run["stages"]["upload"] = "failed"
            run["degradation_reasons"].append({"code": "upload_failed", "message": str(exc)})
    if args.open_browser:
        from .serve import main as preview
        preview_args = [str(output), "--open-browser"]
        if args.config:
            preview_args.extend(["--config", args.config])
        run["stages"]["preview"] = "running"
        finish(output, run, result)
        preview_result = preview(preview_args)
        run["stages"]["preview"] = "failed" if preview_result else "complete"
        if preview_result:
            finish(output, run, preview_result)
            return preview_result
    finish(output, run, result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
