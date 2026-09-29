import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(cwd, *args):
    return subprocess.run([sys.executable, "-m", "latex_review.cli", "main.tex", *args], cwd=cwd,
                          env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), capture_output=True, text=True)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


def meta(root, name="latest"):
    return json.loads((root / ".latex-review" / name / "cache-meta.json").read_text())


def diff(root, name="latest"):
    return json.loads((root / ".latex-review" / name / "diff.json").read_text())


def states(root, name="latest"):
    return [side["state"] for side in meta(root, name)["sides"]]


def fixture(root):
    git(root, "init", "-q")
    git(root, "config", "user.email", "review@example.invalid")
    git(root, "config", "user.name", "Review")
    write(root, ".gitignore", ".latex-review/\n")
    write(root, "main.tex", "\\begin{document}\n\\input{chapters/one}\n\\input{chapters/two}\n\\bibliography{refs}\n\\end{document}\n")
    write(root, "chapters/one.tex", "\\section{One}\nBefore.\n")
    write(root, "chapters/two.tex", "\\section{Two}\nStable.\n\\begin{figure}\\includegraphics{fig/p.png}\\caption{Sample}\\end{figure}\n")
    write(root, "fig/p.png", b"\x89PNG\r\n\x1a\nold")
    write(root, "refs.bib", "@article{a,title={Old}}\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")


def test_cache_cold_warm_incremental_corrupt_bypass_and_clear(tmp_path):
    fixture(tmp_path)
    write(tmp_path, "chapters/one.tex", "\\section{One}\nAfter.\n")
    assert run(tmp_path).returncode in (0, 2)
    cold = diff(tmp_path)
    assert states(tmp_path) == ["miss", "miss"]
    assert run(tmp_path).returncode in (0, 2)
    assert states(tmp_path) == ["hit", "hit"]
    assert diff(tmp_path) == cold
    write(tmp_path, "chapters/one.tex", "\\section{One}\nAfter again.\n")
    assert run(tmp_path).returncode in (0, 2)
    assert states(tmp_path) == ["hit", "miss"]
    assert diff(tmp_path) != cold
    cache_key = meta(tmp_path)["sides"][0]["parse_key"]
    cache_file = tmp_path / ".latex-review/cache" / f"{cache_key}.json"
    cache_file.write_text("{broken", encoding="utf-8")
    assert run(tmp_path).returncode in (0, 2)
    assert states(tmp_path) == ["corrupt", "hit"]
    assert run(tmp_path, "--no-cache").returncode in (0, 2)
    assert states(tmp_path) == ["bypass", "bypass"]
    assert run(tmp_path, "--clear-cache").returncode in (0, 2)
    assert states(tmp_path) == ["miss", "miss"]


def test_same_path_image_replaced_between_commits_and_resource_invalidation(tmp_path):
    fixture(tmp_path)
    before = git(tmp_path, "rev-parse", "HEAD")
    write(tmp_path, "fig/p.png", b"\x89PNG\r\n\x1a\nnew")
    git(tmp_path, "add", "fig/p.png")
    git(tmp_path, "commit", "-qm", "replace image")
    after = git(tmp_path, "rev-parse", "HEAD")
    args = ("--old", before, "--new", after)
    assert run(tmp_path, *args).returncode == 0
    data = diff(tmp_path)
    figures = [change for change in data["changes"] if change["node_type"] == "figure"]
    assert len(figures) == data["summary"]["changes"] == 1
    assert any("图资源内容变化" in detail["summary"] for detail in figures[0]["details"])
    assert "图资源内容变化" in (tmp_path / ".latex-review/latest/report.html").read_text()
    first_meta = meta(tmp_path)
    assert first_meta["sides"][0]["dependencies"]["fig/p.png"] != first_meta["sides"][1]["dependencies"]["fig/p.png"]
    assert run(tmp_path, *args).returncode == 0
    assert states(tmp_path) == ["hit", "hit"]
    assert diff(tmp_path) == data
    assert len([detail for detail in diff(tmp_path)["changes"][0]["details"] if "图资源内容变化" in detail["summary"]]) == 1


def test_shared_image_each_figure_locatable_without_extra_primary_count(tmp_path):
    fixture(tmp_path)
    main = (tmp_path / "chapters/two.tex").read_text()
    write(tmp_path, "chapters/two.tex", main + "\\begin{figure}\\includegraphics{fig/p.png}\\caption{Second}\\end{figure}\n")
    git(tmp_path, "add", "chapters/two.tex")
    git(tmp_path, "commit", "-qm", "second figure")
    before = git(tmp_path, "rev-parse", "HEAD")
    write(tmp_path, "fig/p.png", b"\x89PNG\r\n\x1a\nreplaced")
    git(tmp_path, "add", "fig/p.png")
    git(tmp_path, "commit", "-qm", "replace shared image")
    after = git(tmp_path, "rev-parse", "HEAD")
    assert run(tmp_path, "--old", before, "--new", after).returncode == 0
    changes = diff(tmp_path)["changes"]
    assert len(changes) == 2
    assert all(change["node_type"] == "figure" and
               sum("图资源内容变化" in detail["summary"] for detail in change["details"]) == 1
               and change["source_old"]["file"] == "chapters/two.tex" for change in changes)


def test_worktree_macro_bibliography_and_image_refresh_review_key(tmp_path):
    fixture(tmp_path)
    write(tmp_path, "macros.tex", "\\newcommand{\\term}{before}\n")
    write(tmp_path, "main.tex", "\\input{macros}\n" + (tmp_path / "main.tex").read_text())
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "macro")
    assert run(tmp_path).returncode == 0
    assert run(tmp_path).returncode == 0
    assert states(tmp_path) == ["hit", "hit"]
    initial = meta(tmp_path)["sides"][1]
    write(tmp_path, "refs.bib", "@article{a,title={Changed}}\n")
    assert run(tmp_path).returncode == 0
    bibliography = meta(tmp_path)["sides"][1]
    assert bibliography["state"] == "hit"
    assert bibliography["review_key"] != initial["review_key"]
    write(tmp_path, "fig/p.png", b"\x89PNG\r\n\x1a\nchanged")
    assert run(tmp_path).returncode == 0
    image = meta(tmp_path)["sides"][1]
    assert image["state"] == "hit"
    assert image["review_key"] != bibliography["review_key"]
    assert any("图资源内容变化" in detail["summary"] for change in diff(tmp_path)["changes"]
               for detail in change["details"])
    write(tmp_path, "macros.tex", "\\newcommand{\\term}{after}\n")
    assert run(tmp_path).returncode == 0
    assert states(tmp_path) == ["hit", "miss"]
    assert meta(tmp_path)["sides"][1]["parse_key"] != image["parse_key"]


def test_concurrent_cache_writers_produce_complete_results(tmp_path):
    for side in ("old", "new"):
        root = tmp_path / side
        root.mkdir()
        write(root, "main.tex", f"\\section{{Shared}}\n{side} text.\n")
    cache_dir = tmp_path / "shared-cache"
    processes = []
    for index in range(2):
        output = tmp_path / f"report-{index}"
        args = [sys.executable, "-m", "latex_review.cli", "--old-dir", str(tmp_path / "old"),
                "--new-dir", str(tmp_path / "new"), "--entry", "main.tex", "--output", str(output),
                "--cache-dir", str(cache_dir)]
        processes.append((subprocess.Popen(args, cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True), output))
    documents = []
    for process, output in processes:
        _, error = process.communicate(timeout=30)
        assert process.returncode in (0, 2), error
        documents.append(json.loads((output / "diff.json").read_text()))
        assert len(json.loads((output / "cache-meta.json").read_text())["sides"]) == 2
    assert documents[0] == documents[1]
    assert len(list(cache_dir.glob("*.json"))) == 2


def test_same_bytes_new_path_keeps_path_change_and_missing_is_distinct(tmp_path):
    fixture(tmp_path)
    original = (tmp_path / "chapters/two.tex").read_text()
    write(tmp_path, "fig/copy.png", (tmp_path / "fig/p.png").read_bytes())
    write(tmp_path, "chapters/two.tex", original.replace("fig/p.png", "fig/copy.png"))
    assert run(tmp_path).returncode == 0
    details = [detail for change in diff(tmp_path)["changes"] for detail in change["details"]]
    assert any("图资源路径变化" in detail["summary"] for detail in details)
    assert not any("图资源内容变化" in detail["summary"] for detail in details)
    write(tmp_path, "chapters/two.tex", original.replace("fig/p.png", "fig/missing.png"))
    assert run(tmp_path).returncode == 2
    data = diff(tmp_path)
    assert any(item["code"] == "missing_dependency" for item in data["diagnostics"])
    assert not any("图资源内容变化" in detail["summary"] for change in data["changes"]
                   for detail in change["details"])


def test_clear_cache_rejects_source_directory_and_keeps_files(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        root.mkdir()
        write(root, "main.tex", "\\section{A}\nSource remains.\n")
        write(root, "chapters/part.tex", "Source chapter remains.\n")
    for cache_dir in (old, old / "chapters"):
        result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
                                 "--new-dir", str(new), "--entry", "main.tex", "--output", str(tmp_path / "report"),
                                 "--cache-dir", str(cache_dir), "--clear-cache"],
                                cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                                capture_output=True, text=True)
        assert result.returncode == 64, result.stderr
        assert (old / "main.tex").read_text() == "\\section{A}\nSource remains.\n"
        assert (old / "chapters/part.tex").read_text() == "Source chapter remains.\n"
    git_root = tmp_path / "git-paper"
    git_root.mkdir()
    fixture(git_root)
    assert run(git_root, "--cache-dir", str(git_root), "--clear-cache").returncode == 64
    assert (git_root / "main.tex").is_file()
    assert (git_root / "chapters/one.tex").is_file()


def test_clear_cache_removes_interrupted_writes_but_keeps_foreign_files(tmp_path):
    fixture(tmp_path)
    assert run(tmp_path).returncode == 0
    cache_dir = tmp_path / ".latex-review/cache"
    orphans = []
    for suffix in ("", ".tmp"):
        with tempfile.NamedTemporaryFile("wb", dir=cache_dir, prefix=".write-", suffix=suffix,
                                         delete=False) as stream:
            stream.write(b"interrupted")
            orphans.append(Path(stream.name))
    assert all(path.is_file() for path in orphans)
    assert run(tmp_path, "--clear-cache").returncode == 0
    assert all(not path.exists() for path in orphans)
    foreign = cache_dir / "notes.txt"
    foreign.write_text("source data", encoding="utf-8")
    assert run(tmp_path, "--clear-cache").returncode == 64
    assert foreign.read_text(encoding="utf-8") == "source data"
    foreign.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    link = cache_dir / ".write-symlink.tmp"
    link.symlink_to(outside)
    assert run(tmp_path, "--clear-cache").returncode == 64
    assert link.is_symlink() and outside.read_text(encoding="utf-8") == "keep"


def test_graphicspath_changes_resolved_path_with_same_bytes(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for root, folder in ((old, "fig/old"), (new, "fig/new")):
        root.mkdir()
        write(root, "main.tex", "\\graphicspath{{" + folder + "/}}\n"
              "\\begin{document}\\begin{figure}\\includegraphics{image.png}"
              "\\caption{Same}\\end{figure}\\end{document}\n")
        write(root, folder + "/image.png", b"\x89PNG\r\n\x1a\nsame")
    output = tmp_path / "report"
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
                             "--new-dir", str(new), "--entry", "main.tex", "--output", str(output)],
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads((output / "diff.json").read_text())
    assert data["summary"]["changes"] == 1
    details = data["changes"][0]["details"]
    assert len([detail for detail in details if "图资源路径变化" in detail["summary"]]) == 1
    path_detail = next(detail for detail in details if "图资源路径变化" in detail["summary"])
    assert path_detail["old_text"] == "fig/old/image.png"
    assert path_detail["new_text"] == "fig/new/image.png"
    assert not any("图资源内容变化" in detail["summary"] for detail in details)
    assert "图资源路径变化" in (output / "report.html").read_text()
    write(new, "fig/new/renamed.png", b"\x89PNG\r\n\x1a\nsame")
    write(new, "main.tex", (new / "main.tex").read_text().replace("image.png", "renamed.png"))
    rerun = subprocess.run(result.args, cwd=tmp_path,
                           env=dict(os.environ, PYTHONPATH=str(ROOT / "src")), capture_output=True, text=True)
    assert rerun.returncode == 0, rerun.stderr
    details = json.loads((output / "diff.json").read_text())["changes"][0]["details"]
    assert sum("图资源路径变化" in detail["summary"] for detail in details) == 1
