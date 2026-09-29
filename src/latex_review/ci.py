"""只读 GitHub Actions 审阅入口；只将固定字段写入状态产物。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def _summary(report: Path) -> dict[str, object] | None:
    try:
        data = json.loads((report / "diff.json").read_text(encoding="utf-8"))
        summary = data["summary"]
        changes = summary["changes"]
        added = summary["added_words"]
        removed = summary["removed_words"]
        hits = summary["category_hits"]
        if not all(type(value) is int and value >= 0 for value in (changes, added, removed)):
            return None
        if not isinstance(hits, dict):
            return None
        categories = {key: value for key, value in hits.items()
                      if key in {"text", "equation", "figure", "table", "citation", "comment", "move"}
                      and type(value) is int and value >= 0}
        return {"changes": changes, "added_words": added, "removed_words": removed,
                "category_hits": categories}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 CI 报告与不含论文原文的状态产物")
    parser.add_argument("--paper-dir", type=Path, required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--status-file", type=Path, required=True)
    args = parser.parse_args(argv)
    command = [sys.executable, "-m", "latex_review.cli", args.entry,
               "--old", args.old, "--new", args.new, "--output", str(args.report_dir.absolute())]
    try:
        result = subprocess.run(command, cwd=args.paper_dir, capture_output=True, text=True, check=False)
        code = result.returncode
    except OSError:
        code = 8
    if code not in (0, 2, 4, 8, 64):
        code = 8
    report_available = (args.report_dir / "report.html").is_file() and (args.report_dir / "diagnostics.json").is_file()
    summary = _summary(args.report_dir) if report_available and code in (0, 2) else None
    status = {"schema_version": 1, "exit_code": code,
              "report_available": bool(summary is not None), "summary": summary}
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    args.status_file.write_text(json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"exit_code={code}\n")
            stream.write(f"report_available={'true' if status['report_available'] else 'false'}\n")
    print(f"审阅退出码：{code}；完整报告目录：{'有' if status['report_available'] else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
