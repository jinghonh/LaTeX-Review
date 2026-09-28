"""命令行入口；审阅流水线由后续票据接入。"""

import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import sys

from .sources import SourceError, resolve_sources


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, "latex-review: 错误：参数无效，请运行 --help 查看用法。\n")


def main(argv: list[str] | None = None) -> int:
    parser = ReviewArgumentParser(
        prog="latex-review",
        description="LaTeX 论文审阅工具。已提供比较来源检查；完整审阅命令尚未实现。",
        epilog="--inspect-sources 只解析来源和依赖，不生成审阅报告或调用编译器。",
    )
    parser.add_argument("paths", nargs="*", help="Git 模式的入口，或两个独立源文件")
    parser.add_argument("--entry", dest="entry_option", help="双目录模式下两侧共同的相对入口")
    parser.add_argument("--old-dir", help="修改前项目目录")
    parser.add_argument("--new-dir", help="修改后项目目录")
    parser.add_argument("--old", dest="old_revision", help="修改前 Git 提交")
    parser.add_argument("--new", dest="new_revision", help="修改后 Git 提交")
    parser.add_argument("--inspect-sources", action="store_true", help="输出双侧展开内容与来源映射的 JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('latex-review')}")
    args = parser.parse_args(argv)
    if not args.inspect_sources and not args.paths and args.entry_option is None and not any((args.old_dir, args.new_dir, args.old_revision, args.new_revision)):
        parser.print_help()
        return 0
    if args.inspect_sources:
        try:
            if args.entry_option is not None:
                if args.paths or args.old_revision or args.new_revision or not (args.old_dir and args.new_dir):
                    raise SourceError("conflicting_modes", None, "--entry 仅可与 --old-dir、--new-dir 并用")
                options = dict(entry=args.entry_option, old_dir=args.old_dir, new_dir=args.new_dir)
            elif len(args.paths) == 2:
                if any((args.old_dir, args.new_dir, args.old_revision, args.new_revision)):
                    raise SourceError("conflicting_modes", None, "独立文件模式不能与目录或 Git 提交参数并用")
                options = dict(old_file=args.paths[0], new_file=args.paths[1])
            elif len(args.paths) == 1:
                if args.old_dir or args.new_dir:
                    raise SourceError("conflicting_modes", None, "双目录模式必须使用 --entry")
                options = dict(entry=args.paths[0], old_revision=args.old_revision, new_revision=args.new_revision,
                               old_dir=args.old_dir, new_dir=args.new_dir)
            else:
                raise SourceError("invalid_arguments", None, "请输入一个项目入口或两个独立源文件")
            with resolve_sources(**options) as pair:
                def view(source):
                    result = source.expand()
                    return {
                        "identity": asdict(source.identity), "entry": source.entry,
                        "text": result.text,
                        "dependencies": [asdict(item) for item in result.dependencies],
                        "segments": [asdict(item) for item in result.source_map.segments],
                        "diagnostics": [asdict(item) for item in result.diagnostics],
                    }
                print(json.dumps({"old": view(pair.old), "new": view(pair.new)}, ensure_ascii=False, sort_keys=True))
            return 0
        except SourceError as exc:
            print(f"latex-review: 错误：{json.dumps(asdict(exc), ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
            return 4
    print("latex-review: 错误：审阅命令尚未实现；可用 --inspect-sources 检查来源。", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
