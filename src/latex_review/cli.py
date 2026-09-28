"""命令行入口；审阅流水线由后续票据接入。"""

import argparse
from importlib.metadata import version
import sys


class ReviewArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(64, "latex-review: 错误：参数无效，请运行 --help 查看用法。\n")


def main(argv: list[str] | None = None) -> int:
    parser = ReviewArgumentParser(
        prog="latex-review",
        description="LaTeX 论文审阅工具。当前版本仅提供工程骨架与公共数据契约。",
        epilog="审阅命令尚未实现；--help 与 --version 不读取论文或调用编译器。",
    )
    parser.add_argument("entry", nargs="?", help="论文入口文件（审阅能力尚未实现）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('latex-review')}")
    args = parser.parse_args(argv)
    if args.entry is None:
        parser.print_help()
        return 0
    print("latex-review: 错误：审阅命令尚未实现。当前仅支持 --help 与 --version。", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
