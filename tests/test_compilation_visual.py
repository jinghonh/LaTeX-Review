"""真实编译、页插入与高级渲染的关键验收样例。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from latex_review import cli
from latex_review import compilation
from latex_review.compilation import _run_bounded, _sandbox_profile, compile_side
from latex_review.contract import ComparisonSource
from latex_review.sources import ProjectSource
from latex_review.visual import align_pages


def _source(root: Path, text: str, side: str = "new") -> ProjectSource:
    root.mkdir(parents=True)
    (root / "main.tex").write_text(text, encoding="utf-8")
    return ProjectSource(side, root, "main.tex", ComparisonSource("directory", str(root)))


def _tex(body: str) -> str:
    return "\\documentclass{article}\n\\pagestyle{empty}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def _cli(tmp_path: Path, old: str, new: str, *options: str) -> tuple[int, Path]:
    before, after = tmp_path / "old", tmp_path / "new"
    _source(before, old, "old")
    _source(after, new)
    output = tmp_path / "output"
    code = cli.main(["--entry", "main.tex", "--old-dir", str(before), "--new-dir", str(after),
                     "--output", str(output), *options])
    return code, output


def _ready() -> bool:
    return sys.platform == "darwin" and all(shutil.which(tool) for tool in ("latexmk", "pdflatex", "pdftoppm", "pdfinfo"))


def test_alignment_keeps_pages_after_insertion() -> None:
    page_a = (2, 2, bytes((0, 255, 255, 255)))
    page_b = (2, 2, bytes((255, 0, 255, 255)))
    inserted = (2, 2, bytes((255, 255, 0, 255)))
    pairs = align_pages([page_a, page_b], [page_a, inserted, page_b])
    assert [(pair.old, pair.new, pair.kind) for pair in pairs] == [
        (1, 1, "unchanged"), (None, 2, "added"), (2, 3, "unchanged")]


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
def test_two_side_compile_page_insertion_and_source_links(tmp_path: Path) -> None:
    old = _tex("\\section{First}\nSame first page.\n\\newpage\n\\section{Last}\nSame last page.")
    new = _tex("\\section{First}\nSame first page.\n\\newpage\n\\section{Inserted}\nNew middle page."
               "\n\\newpage\n\\section{Last}\nSame last page.")
    code, output = _cli(tmp_path, old, new, "--compile")
    assert code in (0, 2)
    visual = json.loads((output / "pages/visual.json").read_text())
    assert visual["comparable"]
    assert len(visual["old"]) == 2 and len(visual["new"]) == 3
    assert any(pair["kind"] == "added" and pair["new"] == 2 for pair in visual["pairs"])
    assert any(pair["old"] == 2 and pair["new"] == 3 for pair in visual["pairs"])
    html = (output / "report.html").read_text()
    assert 'id="page-new-2"' in html and 'href="#page-new-' in html
    assert "页面视觉差异" in html and "主变更" in html
    assert all(json.loads((output / f"compiled/{side}/status.json").read_text())["status"] == "success"
               for side in ("old", "new"))
    assert (output / "pages/new/0002.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
@pytest.mark.parametrize("old,new", [
    (_tex("A short paragraph."), _tex("A longer paragraph with extra words and a second sentence.")),
    (_tex("The formula is $x^2$."), _tex("The formula is $y^2$.")),
])
def test_reflow_and_formula_visual_change(tmp_path: Path, old: str, new: str) -> None:
    _, output = _cli(tmp_path, old, new, "--compile")
    visual = json.loads((output / "pages/visual.json").read_text())
    assert visual["comparable"] and any(pair["kind"] == "changed" for pair in visual["pairs"])
    assert any(pair["image"] for pair in visual["pairs"])


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
def test_one_side_failure_keeps_structural_report(tmp_path: Path) -> None:
    _, output = _cli(tmp_path, _tex("Good text."), _tex("\\undefinedfatalcommand"), "--compile")
    assert (output / "report.html").is_file() and (output / "diff.json").is_file()
    assert json.loads((output / "compiled/old/status.json").read_text())["status"] == "success"
    assert json.loads((output / "compiled/new/status.json").read_text())["status"] == "failed"
    assert not json.loads((output / "pages/visual.json").read_text())["comparable"]
    diagnostics = json.loads((output / "diagnostics.json").read_text())["diagnostics"]
    assert any(item["code"] == "compile_failed" and "修改后" in item["message"] for item in diagnostics)
    assert any(item["code"] == "pages_not_comparable" for item in diagnostics)


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
def test_new_only_keeps_one_side_preview_and_explicit_status(tmp_path: Path) -> None:
    _, output = _cli(tmp_path, _tex("Old text."), _tex("New text."), "--compile", "--compile-new-only")
    old = json.loads((output / "compiled/old/status.json").read_text())
    new = json.loads((output / "compiled/new/status.json").read_text())
    visual = json.loads((output / "pages/visual.json").read_text())
    assert old["status"] == "skipped" and new["status"] == "success"
    assert not visual["comparable"] and not visual["old"] and len(visual["new"]) == 1
    assert "不可比较" in (output / "report.html").read_text()


@pytest.mark.skipif(not _ready() or not shutil.which("bibtex"), reason="需要 macOS、TeX 与 BibTeX 工具")
@pytest.mark.parametrize("sandbox", [False, True])
def test_bibliography_reruns_both_sides_and_compares_pages(tmp_path: Path, sandbox: bool) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for root, word in ((old, "Old"), (new, "New")):
        _source(root, _tex(f"{word} citation \\cite{{demo}}.\n"
                           "\\bibliographystyle{plain}\n\\bibliography{references}"),
                "old" if root == old else "new")
        (root / "references.bib").write_text(
            "@article{demo, author={Ada Lovelace}, title={Demo}, journal={Example}, year={1843}}\n",
            encoding="utf-8")
    output = tmp_path / "output"
    args = ["--entry", "main.tex", "--old-dir", str(old), "--new-dir", str(new),
            "--output", str(output), "--compile"]
    if sandbox:
        args.append("--sandbox-render")
    code = cli.main(args)
    assert code in (0, 2)
    for side in ("old", "new"):
        status = json.loads((output / f"compiled/{side}/status.json").read_text())
        log = (output / f"compiled/{side}/compile.log").read_text(errors="replace")
        assert status["status"] == "success", (side, status, log[-1800:])
        assert "Database file #1: references.bib" in log
        assert "(build/main.bbl)" in log
        assert "undefined" not in log.rsplit("--- TeX log ---", 1)[-1].lower()
        assert (output / f"pages/{side}/0001.png").is_file()
    assert json.loads((output / "pages/visual.json").read_text())["comparable"]


@pytest.mark.skipif(not _ready(), reason="需要 macOS 与 TeX 工具")
def test_advanced_sandbox_blocks_command_and_outside_write(tmp_path: Path) -> None:
    marker = tmp_path / "outside"
    body = (f"\\immediate\\write18{{touch {marker}}}\n"
            "\\openin2=/etc/passwd\\ifeof2\\else\\errmessage{outside-read}\\fi\nSafe text.")
    source = _source(tmp_path / "paper", _tex(body))
    output = tmp_path / "output"; output.mkdir()
    status, issues = compile_side(source, output, sandbox=True, timeout=15)
    assert status.status == "success", (status, (output / status.log).read_text()[:1000])
    assert not marker.exists()
    assert not issues

    source.root.joinpath("main.tex").write_text(_tex(f"\\immediate\\openout1={marker}\nText."), encoding="utf-8")
    blocked_output = tmp_path / "blocked-output"; blocked_output.mkdir()
    blocked, blocked_issues = compile_side(source, blocked_output, sandbox=True, timeout=15)
    assert blocked.status == "failed" and blocked_issues
    assert not marker.exists()


@pytest.mark.skipif(not _ready(), reason="需要 macOS 与 TeX 工具")
def test_advanced_timeout_terminates_loop(tmp_path: Path) -> None:
    source = _source(tmp_path / "paper", _tex("\\loop\\iftrue\\repeat"))
    output = tmp_path / "output"; output.mkdir()
    status, issues = compile_side(source, output, sandbox=True, timeout=1)
    assert status.status == "failed" and "超时" in (status.reason or "")
    assert issues and (output / status.log).is_file()


@pytest.mark.skipif(not _ready(), reason="需要 macOS 与 TeX 工具")
def test_basic_compile_uses_copy_and_blocks_original_write(tmp_path: Path) -> None:
    marker = tmp_path / "original-workspace-target"
    text = _tex(f"\\immediate\\openout1={marker}\nText.")
    source = _source(tmp_path / "paper", text)
    output = tmp_path / "output"; output.mkdir()
    status, issues = compile_side(source, output, timeout=15)
    assert status.status == "failed" and issues
    assert not marker.exists() and (source.root / "main.tex").read_text() == text


def test_missing_tool_is_a_diagnostic_not_an_exception(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path / "paper", _tex("Text."))
    output = tmp_path / "output"; output.mkdir()
    monkeypatch.setattr("latex_review.compilation._program", lambda *_: None)
    status, issues = compile_side(source, output)
    assert status.status == "unavailable" and issues[0].code == "compile_failed"
    assert (output / "compiled/new/status.json").is_file()


@pytest.mark.skipif(not _ready(), reason="需要 macOS 与 TeX 工具")
@pytest.mark.parametrize("sandbox", [False, True])
def test_project_path_tools_never_run_in_host(tmp_path: Path, monkeypatch, sandbox: bool) -> None:
    marker = tmp_path / "outside-sentinel"
    source = _source(tmp_path / "paper", _tex("Safe text."))
    fake_contents = {}
    for name, version in (("latexmk", "Latexmk"), ("pdflatex", "pdfTeX"), ("ps", "")):
        fake = source.root / name
        content = f"#!/bin/sh\nprintf dangerous > '{marker}'\nprintf '{version}\\n'\n"
        fake.write_text(content, encoding="utf-8"); fake.chmod(0o755)
        fake_contents[name] = content
    monkeypatch.setenv("PATH", f".:{source.root}:{tmp_path}:{os.environ['PATH']}")
    monkeypatch.chdir(source.root)
    output = tmp_path / "output"; output.mkdir()
    seen = []
    original = compilation._run_bounded
    def observed(command, cwd, env, timeout, output_limit, *, sandbox_profile=None):
        seen.append((tuple(command), cwd, sandbox_profile))
        return original(command, cwd, env, timeout, output_limit, sandbox_profile=sandbox_profile)
    monkeypatch.setattr(compilation, "_run_bounded", observed)
    status, issues = compile_side(source, output, sandbox=sandbox, timeout=20)
    assert status.status == "success", (status, issues)
    assert len(seen) == 3 and sum("--version" in command for command, _, _ in seen) == 2
    assert all(cwd != source.root for _, cwd, _ in seen)
    assert all((profile is not None) == sandbox for _, _, profile in seen)
    assert len({profile for _, _, profile in seen}) == 1
    assert not marker.exists()
    assert all((source.root / name).read_text() == content for name, content in fake_contents.items())
    assert (source.root / "main.tex").read_text() == _tex("Safe text.")


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
def test_page_tools_from_project_path_are_not_executed(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "outside-sentinel"
    for name in ("pdfinfo", "pdftoppm", "synctex"):
        fake = tmp_path / name
        fake.write_text(f"#!/bin/sh\nprintf dangerous > '{marker}'\n", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    _, output = _cli(tmp_path, _tex("Old text."), _tex("New text."), "--compile")
    visual = json.loads((output / "pages/visual.json").read_text())
    assert visual["comparable"] and visual["old"] and visual["new"]
    assert not marker.exists()


def test_only_project_tools_available_refuses_before_execution(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "outside-sentinel"
    source = _source(tmp_path / "paper", _tex("Text."))
    for name in ("latexmk", "pdflatex"):
        fake = source.root / name
        fake.write_text(f"#!/bin/sh\nprintf dangerous > '{marker}'\n", encoding="utf-8")
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(source.root))
    output = tmp_path / "output"; output.mkdir()
    status, issues = compile_side(source, output, sandbox=True)
    assert status.status == "unavailable" and issues
    assert not marker.exists()


@pytest.mark.skipif(not _ready(), reason="需要 macOS、latexmk 和 Poppler")
@pytest.mark.parametrize("sandbox", [False, True])
def test_other_side_project_tool_is_excluded_from_both_compilations(
        tmp_path: Path, monkeypatch, sandbox: bool) -> None:
    old = _source(tmp_path / "old-parent" / "paper", _tex("Old text."), "old")
    new = _source(tmp_path / "new-parent" / "paper", _tex("New text."), "new")
    neutral = tmp_path / "neutral"; neutral.mkdir()
    marker = tmp_path / "outside-sentinel"
    fake = new.root / "latexmk"
    content = f"#!/bin/sh\nprintf dangerous > '{marker}'\nprintf 'Latexmk\\n'\n"
    fake.write_text(content, encoding="utf-8"); fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{new.root}:{os.environ['PATH']}")
    monkeypatch.chdir(neutral)
    output = tmp_path / "output"
    options = ["--entry", "main.tex", "--old-dir", str(old.root), "--new-dir", str(new.root),
               "--output", str(output), "--compile"]
    if sandbox:
        options.append("--sandbox-render")
    assert cli.main(options) in (0, 2)
    assert not marker.exists()
    assert fake.read_text() == content
    assert (old.root / "main.tex").read_text() == _tex("Old text.")
    assert (new.root / "main.tex").read_text() == _tex("New text.")
    assert all(json.loads((output / f"compiled/{side}/status.json").read_text())["status"] == "success"
               for side in ("old", "new"))


@pytest.mark.skipif(not _ready(), reason="需要 macOS 与 TeX 工具")
def test_unavailable_sandbox_refuses_execution(tmp_path: Path, monkeypatch) -> None:
    source = _source(tmp_path / "paper", _tex("Text."))
    output = tmp_path / "output"; output.mkdir()
    monkeypatch.setattr("latex_review.compilation._sandbox_ready", lambda *_: False)
    status, issues = compile_side(source, output, sandbox=True)
    assert status.status == "sandbox_rejected" and status.pdf is None
    assert issues[0].code == "sandbox_unavailable"
    assert not (output / "compiled/new/document.pdf").exists()


def test_command_output_limit_terminates_process(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import sys,time; print('x' * 1000000); sys.stdout.flush(); time.sleep(10)"]
    _, log, reason = _run_bounded(command, tmp_path, dict(PATH=str(Path(sys.executable).parent)), 5, 1024)
    assert reason == "编译输出超过限制" and len(log) == 1024


@pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file(),
                    reason="需要 macOS 沙箱")
def test_sandbox_policy_denies_network_and_unlisted_process(tmp_path: Path) -> None:
    profile = tmp_path / "probe.sb"
    _sandbox_profile(profile, tmp_path, (Path("/usr/bin/nc"),))
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0)); server.listen(1)
        port = server.getsockname()[1]
        blocked = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), "/usr/bin/nc",
                                  "-z", "-w", "1", "127.0.0.1", str(port)],
                                 cwd=tmp_path, capture_output=True, timeout=4)
    assert blocked.returncode != 0
    unlisted = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), "/usr/bin/id"],
                              cwd=tmp_path, capture_output=True, timeout=4)
    assert unlisted.returncode != 0
