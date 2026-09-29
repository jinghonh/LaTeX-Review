"""公共合成论文：多文件、五类差异、降级、历史版本和较长正文。"""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/e2e"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9V7ZkAAAAASUVORK5CYII="
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_synthetic_end_to_end(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    shutil.copytree(FIXTURE / "old", paper, dirs_exist_ok=True)
    (paper / "fig").mkdir()
    (paper / "fig/shape.png").write_bytes(PNG)
    long_text = "\n\n" + "\n\n".join(f"Stable paragraph {i} describes a fixed result." for i in range(150)) + "\n"
    old_intro = (paper / "sections/intro.tex").read_text()
    (paper / "sections/intro.tex").write_text(old_intro + long_text)
    git(paper, "init", "-q")
    git(paper, "config", "user.email", "review@example.invalid")
    git(paper, "config", "user.name", "Review")
    git(paper, "add", ".")
    git(paper, "commit", "-qm", "fixed old sample")
    old_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=paper, text=True).strip()
    shutil.copytree(FIXTURE / "new", paper, dirs_exist_ok=True)
    new_intro = (paper / "sections/intro.tex").read_text()
    (paper / "sections/intro.tex").write_text(new_intro + long_text)

    private = tmp_path / "private-run"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify-e2e.py"), "--project-root", str(paper),
         "--entry", "main.tex", "--old-revision", old_revision, "--private-dir", str(private)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    data = json.loads((private / "report/diff.json").read_text())
    expected = json.loads((FIXTURE / "expectations.json").read_text())
    def shape(change: dict) -> dict:
        def position(side: str):
            source = change[f"source_{side}"]
            return [source["file"], source["start_line"]] if source else None
        return {"kind": change["kind"], "node_type": change["node_type"],
                "category": change["categories"][0], "old": position("old"), "new": position("new")}
    assert sorted(map(shape, data["changes"]), key=lambda item: json.dumps(item, sort_keys=True)) == sorted(
        expected["changes"], key=lambda item: json.dumps(item, sort_keys=True))
    categories = {category for change in data["changes"] for category in
                  (*change["categories"], *(detail["category"] for detail in change["details"]))}
    assert {"text", "equation", "figure", "table", "citation"} <= categories
    assert data["summary"]["changes"] < 20, "插入段落不应引起大量级联误报"
    assert any(change["source_new"] and change["source_new"]["file"] == "sections/intro.tex"
               for change in data["changes"])
    assert any(change["source_new"] and change["source_new"]["file"] == "sections/results.tex"
               for change in data["changes"])
    diagnostics = {item["code"] for item in data["diagnostics"]}
    assert set(expected["diagnostics"]) <= diagnostics
    assert json.loads((private / "run.json").read_text())["exit_code"] == 2

    # 文献元数据变化自身不生成首版语义主变更。
    git(paper, "restore", ".")
    (paper / "references.bib").write_text(
        (paper / "references.bib").read_text().replace("Old Study", "Updated Study"))
    second = tmp_path / "bib-only-run"
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify-e2e.py"), "--project-root", str(paper),
         "--entry", "main.tex", "--old-revision", old_revision, "--private-dir", str(second)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads((second / "report/diff.json").read_text())["summary"]["changes"] == expected["bib_only_changes"]
