"""在临时合成论文上记录冷、热和单章增量运行；不设跨设备门槛。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]


def command(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def measure(root: Path, label: str) -> dict:
    start = perf_counter()
    result = subprocess.run((sys.executable, "-m", "latex_review.cli", "main.tex"), cwd=root,
                            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), capture_output=True, text=True)
    seconds = round(perf_counter() - start, 3)
    if result.returncode not in (0, 2):
        raise RuntimeError(f"{label} 运行失败：{result.stderr}")
    output = root / ".latex-review/latest"
    metadata = json.loads((output / "cache-meta.json").read_text())
    semantic = json.loads((output / "diff.json").read_text())
    return {"name": label, "seconds": seconds, "cache_states": [side["state"] for side in metadata["sides"]],
            "changes": semantic["summary"]["changes"], "diff": semantic}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="latex-review-benchmark-") as temp:
        root = Path(temp)
        command(root, "git", "init", "-q")
        command(root, "git", "config", "user.email", "benchmark@example.invalid")
        command(root, "git", "config", "user.name", "Benchmark")
        (root / ".gitignore").write_text(".latex-review/\n")
        (root / "chapters").mkdir()
        chapters = []
        for index in range(12):
            name = f"chapters/chapter-{index:02d}.tex"
            chapters.append(name)
            paragraphs = "\n\n".join(f"Paragraph {number}: a reproducible sentence for comparison."
                                     for number in range(20))
            (root / name).write_text(f"\\section{{Chapter {index}}}\n{paragraphs}\n")
        (root / "main.tex").write_text("\\begin{document}\n" + "".join(f"\\input{{{name[:-4]}}}\n" for name in chapters)
                                       + "\\end{document}\n")
        command(root, "git", "add", ".")
        command(root, "git", "commit", "-qm", "baseline")
        (root / chapters[3]).write_text((root / chapters[3]).read_text().replace("Paragraph 4:", "Paragraph 4: edited"))
        cold = measure(root, "冷运行")
        warm = measure(root, "热运行")
        (root / chapters[3]).write_text((root / chapters[3]).read_text().replace("Paragraph 5:", "Paragraph 5: edited"))
        incremental = measure(root, "单章增量")
        print(json.dumps({"device": {"platform": platform.platform(), "machine": platform.machine(),
                                     "python": platform.python_version(), "processor": platform.processor()},
                          "sample": "12 个章节文件，每章 20 段；工作区仅修改第 4 章",
                          "runs": [{key: value for key, value in run.items() if key != "diff"}
                                   for run in (cold, warm, incremental)],
                          "cold_warm_semantically_equal": cold["diff"] == warm["diff"]},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
