"""无编译审阅命令行。"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib

from .cache import ParseCache
from .comparison import compare_projects
from .contract import Diagnostic, SourceLocation
from .report import ReportPathError, write_cache_metadata, write_failure_diagnostics, write_report, write_source_fallback
from .sources import SourceError, resolve_sources
from .structure import parse_project


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, f"latex-review: 错误：{message}；请运行 --help 查看用法。\n")


class ConfigurationError(ValueError):
    pass


def _config(path: Path, explicit: bool) -> dict:
    if not path.exists() and not explicit:
        return {}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"配置文件 {path} 无法读取或解析：{exc}") from exc
    allowed = {"entry", "ignore", "render", "diff", "git", "output"}
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
                "git": {"default_old": str, "default_new": str}}
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


def main(argv: list[str] | None = None) -> int:
    parser = ReviewArgumentParser(prog="latex-review", description="比较论文并生成无编译审阅报告。",
                                  epilog="默认比较 Git HEAD 与磁盘工作区；--compile 尚未支持。")
    parser.add_argument("paths", nargs="*", help="Git 模式入口，或两个独立源文件")
    parser.add_argument("--entry", dest="entry_option", help="双目录模式共同的相对入口")
    parser.add_argument("--old-dir", help="修改前项目目录")
    parser.add_argument("--new-dir", help="修改后项目目录")
    parser.add_argument("--old", dest="old_revision", help="修改前 Git 提交")
    parser.add_argument("--new", dest="new_revision", help="修改后 Git 提交或 worktree")
    parser.add_argument("--output", help="报告目录，默认 .latex-review/latest")
    parser.add_argument("--cache-dir", help="解析缓存目录，默认位于报告目录旁")
    parser.add_argument("--no-cache", action="store_true", help="本次绕过解析缓存")
    parser.add_argument("--clear-cache", action="store_true", help="本次运行前清理解析缓存")
    parser.add_argument("--math", help="公式排版依赖，首版仅支持 mathjax")
    parser.add_argument("--format", help="输出格式，首版仅支持 html,json")
    parser.add_argument("--config", help="配置文件，默认当前目录 .latex-review.toml")
    parser.add_argument("--comments", action=argparse.BooleanOptionalAction, default=None,
                        help="审阅源码注释变化；--no-comments 可覆盖配置")
    parser.add_argument("--inspect-sources", action="store_true", help="仅输出双侧展开来源 JSON")
    parser.add_argument("--compile", action="store_true", help="保留选项；真实编译将在第二版支持")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('latex-review')}")
    args = parser.parse_args(argv)
    if args.compile:
        parser.error("--compile 尚未支持，不能执行真实编译")
    if not args.paths and not args.entry_option and not args.old_dir and not args.new_dir and not args.config and not Path(".latex-review.toml").exists():
        parser.print_help()
        return 0
    output = None
    output_ready = False
    try:
        config = _config(Path(args.config).expanduser() if args.config else Path(".latex-review.toml"), bool(args.config))
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
        if args.clear_cache and cache_dir.exists():
            if not cache_dir.is_dir():
                raise ConfigurationError("缓存路径已存在且不是目录")
            shutil.rmtree(cache_dir)
        _prepare_output(output)
        output_ready = True
        cache = ParseCache(cache_dir, enabled=not args.no_cache,
                           config={"config": config, "math": args.math or "mathjax",
                                   "comments": args.comments if args.comments is not None else config.get("diff", {}).get("comments", False)})
        with resolve_sources(**options, excluded_paths=(output, cache_dir), ignore_patterns=tuple(config.get("ignore", ()))) as pair:
            old_expanded, new_expanded = pair.old.expand(), pair.new.expand()
            failures = []
            parsed = []
            for expanded in (old_expanded, new_expanded):
                try:
                    parsed.append(cache.parse(expanded, parser=parse_project))
                except SourceError:
                    raise
                except Exception as exc:
                    parsed.append(None)
                    failures.append((expanded.source.side, str(exc)))
            if failures or any(item and any(d.code == "plastex_parse_error" for d in item.diagnostics) for item in parsed):
                report = write_source_fallback(old_expanded, new_expanded, output, parse_failures=failures,
                                               parsed=tuple(parsed))
            else:
                comments = args.comments if args.comments is not None else config.get("diff", {}).get("comments", False)
                result = compare_projects(parsed[0], parsed[1], review_comments=comments)
                report = write_report(parsed[0], parsed[1], result, output)
            write_cache_metadata(output, cache.metadata())
        print(report.html)
        return 2 if any(d.severity in {"warning", "error"} for d in report.document.diagnostics) else 0
    except ConfigurationError as exc:
        _record_failure(output if output_ready else _known_output_after_config_error(args),
                        Diagnostic("configuration_error", "error", str(exc)))
        print(f"latex-review: 配置错误：{exc}", file=sys.stderr)
        return 64
    except SourceError as exc:
        if exc.code in {"invalid_arguments", "conflicting_modes"}:
            print(f"latex-review: 参数错误：{exc.message}", file=sys.stderr)
            return 64
        if output_ready:
            unknown = SourceLocation(None, None, None, confidence=0, uncertainty_reason="来源无法读取")
            _record_failure(output, Diagnostic(exc.code, "error", exc.message,
                                               source_old=unknown if exc.side == "old" else None,
                                               source_new=unknown if exc.side == "new" else None))
        print(f"latex-review: 来源错误：{json.dumps(asdict(exc), ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
        return 4
    except Exception as exc:
        if output_ready:
            _record_failure(output, Diagnostic("internal_error", "error", f"{type(exc).__name__}: {exc}"))
        print(f"latex-review: 内部错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 8


if __name__ == "__main__":
    raise SystemExit(main())
