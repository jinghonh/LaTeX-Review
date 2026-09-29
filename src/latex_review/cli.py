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

from .comparison import compare_projects
from .compilation import _program, _safe_path, compile_side, skipped_side
from .contract import Diagnostic, SourceLocation
from .report import ReportPathError, write_failure_diagnostics, write_report, write_source_fallback
from .sources import SourceError, resolve_sources
from .structure import parse_project
from .visual import build_visual


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
    allowed = {"entry", "ignore", "render", "diff", "git", "output", "compile"}
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
    parser = ReviewArgumentParser(prog="latex-review", description="比较论文并生成结构化审阅报告。",
                                  epilog="默认比较 Git HEAD 与磁盘工作区；仅显式 --compile 才运行 TeX。")
    parser.add_argument("paths", nargs="*", help="Git 模式入口，或两个独立源文件")
    parser.add_argument("--entry", dest="entry_option", help="双目录模式共同的相对入口")
    parser.add_argument("--old-dir", help="修改前项目目录")
    parser.add_argument("--new-dir", help="修改后项目目录")
    parser.add_argument("--old", dest="old_revision", help="修改前 Git 提交")
    parser.add_argument("--new", dest="new_revision", help="修改后 Git 提交或 worktree")
    parser.add_argument("--output", help="报告目录，默认 .latex-review/latest")
    parser.add_argument("--math", help="公式排版依赖，首版仅支持 mathjax")
    parser.add_argument("--format", help="输出格式，首版仅支持 html,json")
    parser.add_argument("--config", help="配置文件，默认当前目录 .latex-review.toml")
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
    try:
        config = _config(Path(args.config).expanduser() if args.config else Path(".latex-review.toml"), bool(args.config))
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
        _prepare_output(output)
        output_ready = True
        if compile_enabled:
            output.mkdir(parents=True, exist_ok=True)
        with resolve_sources(**options, excluded_paths=(output,), ignore_patterns=tuple(config.get("ignore", ()))) as pair:
            old_expanded, new_expanded = pair.old.expand(), pair.new.expand()
            failures = []
            parsed = []
            for expanded in (old_expanded, new_expanded):
                try:
                    parsed.append(parse_project(expanded))
                except Exception as exc:
                    parsed.append(None)
                    failures.append((expanded.source.side, str(exc)))
            source_fallback = bool(failures or any(item and any(d.code == "plastex_parse_error" for d in item.diagnostics) for item in parsed))
            comparison = None
            if not source_fallback:
                comments = args.comments if args.comments is not None else config.get("diff", {}).get("comments", False)
                comparison = compare_projects(parsed[0], parsed[1], review_comments=comments)
            statuses = None
            rendering = None
            compile_diagnostics: tuple[Diagnostic, ...] = ()
            if compile_enabled:
                built = []
                for source in (pair.old, pair.new):
                    if source.side == "old" and new_only:
                        built.append(skipped_side(output, engine))
                        continue
                    peer = pair.new if source.side == "old" else pair.old
                    status, issues = compile_side(source, output, engine=engine, timeout=timeout, sandbox=sandbox,
                                                  related_sources=(peer,))
                    built.append(status)
                    compile_diagnostics += issues
                statuses = tuple(built)
                rendering, visual_issues = build_visual(statuses[0], statuses[1], output,
                                                        comparison.document if comparison else None,
                                                        sources=(pair.old, pair.new))
                compile_diagnostics += visual_issues
            if source_fallback:
                report = write_source_fallback(old_expanded, new_expanded, output, parse_failures=failures,
                                               parsed=tuple(parsed), extra_diagnostics=compile_diagnostics,
                                               rendering=rendering, statuses=statuses)
            else:
                safe_path, forbidden = _safe_path((pair.old, pair.new), output)
                pdf_converter = _program("pdftoppm", safe_path, forbidden) or ""
                report = write_report(parsed[0], parsed[1], comparison, output,
                                      pdf_converter=pdf_converter, extra_diagnostics=compile_diagnostics,
                                      rendering=rendering, statuses=statuses)
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
