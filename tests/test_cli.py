import json
import os
from pathlib import Path
import subprocess
import sys

from latex_review import cli


ROOT = Path(__file__).resolve().parents[1]


def run_cli(cwd: Path, *args: str, env: dict | None = None):
    variables = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    if env:
        variables.update(env)
    return subprocess.run([sys.executable, "-m", "latex_review.cli", *args], cwd=cwd,
                          env=variables, capture_output=True, text=True)


def git(cwd: Path, *args: str):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def project(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "review@example.invalid")
    git(tmp_path, "config", "user.name", "Review")
    (tmp_path / "main.tex").write_text("\\section{A}\nOld text.\n", encoding="utf-8")
    git(tmp_path, "add", "main.tex")
    git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "main.tex").write_text("\\section{A}\nNew text.\n", encoding="utf-8")
    return tmp_path


def test_default_head_worktree_and_no_tex_engine(tmp_path):
    project(tmp_path)
    marker = tmp_path / "engine-called"
    fake = tmp_path / "latexmk"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    fake.chmod(0o755)
    result = run_cli(tmp_path, "main.tex", env={"PATH": f"{tmp_path}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    output = tmp_path / ".latex-review/latest"
    assert {p.name for p in output.iterdir()} >= {"report.html", "diff.json", "diagnostics.json"}
    data = json.loads((output / "diff.json").read_text())
    assert data["old"]["kind"] == "git" and len(data["old"]["identifier"]) == 40
    assert data["new"]["kind"] == "worktree"
    assert "Old text" in json.dumps(data) and "New text" in json.dumps(data)
    assert json.loads((output / "diagnostics.json").read_text())["diagnostics"] == []
    assert not marker.exists()
    assert run_cli(tmp_path, "main.tex", "--compile-new-only").returncode == 64
    assert not marker.exists()


def test_config_precedence_and_output_exclusion(tmp_path):
    project(tmp_path)
    (tmp_path / "main.tex").write_text("\\section{A}\nNew text.\n% review comment\n", encoding="utf-8")
    (tmp_path / ".latex-review.toml").write_text(
        'entry = "wrong.tex"\noutput = "configured-report"\n'
        '[diff]\ncomments = true\n[git]\ndefault_old = "HEAD"\ndefault_new = "worktree"\n',
        encoding="utf-8")
    first = run_cli(tmp_path, "main.tex", "--output", "explicit-report", "--no-comments")
    assert first.returncode == 0, first.stderr
    output = tmp_path / "explicit-report"
    assert output.joinpath("report.html").is_file()
    assert not tmp_path.joinpath("configured-report").exists()
    # 即使论文引用了上次的报告目录，它也不能成为下次工作区输入。
    output.joinpath("injected.tex").write_text("forged", encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        "\\section{A}\nNew text.\n% review comment\n\\input{explicit-report/injected}\n",
        encoding="utf-8")
    second = run_cli(tmp_path, "main.tex", "--output", "explicit-report", "--no-comments")
    assert second.returncode == 2, second.stderr
    data = json.loads(output.joinpath("diff.json").read_text())
    assert all("forged" not in node["raw_latex"] for node in data["nodes_new"])
    assert data["summary"]["category_hits"].get("comment", 0) == 0
    assert any(item["code"] == "missing_dependency" for item in data["diagnostics"])
    assert not output.joinpath("injected.tex").exists()
    (tmp_path / "main.tex").write_text("\\section{A}\nNew text.\n% review comment\n", encoding="utf-8")
    (tmp_path / ".latex-review.toml").write_text(
        'entry = "wrong.tex"\n[git]\ndefault_old = "bad-revision"\ndefault_new = "HEAD"\n',
        encoding="utf-8")
    overridden = run_cli(tmp_path, "main.tex", "--old", "HEAD", "--new", "worktree")
    assert overridden.returncode == 0, overridden.stderr
    assert run_cli(tmp_path, "--config", "missing.toml").returncode == 64
    (tmp_path / ".latex-review.toml").write_text("[render]\nmath='unsafe'\n", encoding="utf-8")
    bad = run_cli(tmp_path, "main.tex")
    assert bad.returncode == 64 and "配置错误" in bad.stderr
    assert run_cli(tmp_path, "main.tex", "--math", "mathjax", "--format", "html,json").returncode == 0


def test_diagnostics_exit_codes_and_stale_report(tmp_path):
    project(tmp_path)
    output = tmp_path / ".latex-review/latest"
    assert run_cli(tmp_path, "main.tex").returncode == 0
    (tmp_path / "main.tex").write_text("\\section{A}\n\\odd{<script>alert(1)</script>}\n", encoding="utf-8")
    degraded = run_cli(tmp_path, "main.tex")
    assert degraded.returncode == 2, degraded.stderr
    diagnostics = json.loads(output.joinpath("diagnostics.json").read_text())["diagnostics"]
    assert any(item["code"] == "unknown_latex" and item["source_new"]["file"] == "main.tex"
               for item in diagnostics)
    html = output.joinpath("report.html").read_text()
    assert "&lt;script&gt;" not in html and "<script>alert(1)</script>" not in html
    assert "此处暂无法预览" in html
    (tmp_path / "main.tex").unlink()
    failed = run_cli(tmp_path, "main.tex")
    assert failed.returncode == 4 and not output.joinpath("report.html").exists()
    assert json.loads(output.joinpath("diagnostics.json").read_text())["diagnostics"][0]["severity"] == "error"


def test_info_diagnostic_does_not_degrade_exit(tmp_path):
    project(tmp_path)
    (tmp_path / "main.tex").write_text("\\section{A}\nSee \\ref{missing}.\n", encoding="utf-8")
    git(tmp_path, "add", "main.tex")
    git(tmp_path, "commit", "-qm", "unresolved reference")
    result = run_cli(tmp_path, "main.tex")
    assert result.returncode == 0, result.stderr
    diagnostics = json.loads((tmp_path / ".latex-review/latest/diagnostics.json").read_text())["diagnostics"]
    assert any(item["code"] == "unresolved_reference" and item["severity"] == "info"
               for item in diagnostics)


def test_document_commands_and_links_are_inert(tmp_path):
    project(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "main.tex").write_text(
        "\\section{A}\n"
        + rf"\write18{{touch {marker}}}" + "\n"
        + r"\href{javascript:alert(1)}{click} and $\href{javascript:alert(2)}{x}$." + "\n"
        + r"\input{|touch executed}" + "\n", encoding="utf-8")
    result = run_cli(tmp_path, "main.tex")
    assert result.returncode == 2, result.stderr
    assert not marker.exists()
    html = (tmp_path / ".latex-review/latest/report.html").read_text()
    assert 'href="javascript:' not in html
    assert "javascript:alert(1)" not in html
    assert any(item["code"] == "unknown_latex" for item in
               json.loads((tmp_path / ".latex-review/latest/diagnostics.json").read_text())["diagnostics"])


def test_source_fallback_and_internal_error(tmp_path, monkeypatch):
    project(tmp_path)
    (tmp_path / "main.tex").write_text("\\section{A}\n\\input{part}\n", encoding="utf-8")
    (tmp_path / "part.tex").write_text("Old text.\n", encoding="utf-8")
    git(tmp_path, "add", "main.tex", "part.tex")
    git(tmp_path, "commit", "-qm", "included source")
    (tmp_path / "part.tex").write_text("New text.\n", encoding="utf-8")
    original = cli.parse_project
    monkeypatch.setattr(cli, "parse_project", lambda expanded: (_ for _ in ()).throw(ValueError("坏结构")))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["main.tex"]) == 2
    output = tmp_path / ".latex-review/latest"
    html = output.joinpath("report.html").read_text()
    data = json.loads(output.joinpath("diff.json").read_text())
    assert "此处暂无法预览" in html and "Old text" not in html and "New text" not in html
    assert r"\input{part}" not in html and "===== part.tex =====" not in html
    assert data["nodes_old"][0]["raw_latex"] and data["nodes_new"][0]["raw_latex"]
    assert any(item["code"] == "source_fallback" for item in data["diagnostics"])
    monkeypatch.setattr(cli, "parse_project", original)
    monkeypatch.setattr(cli, "compare_projects", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("内部故障")))
    assert cli.main(["main.tex"]) == 8
    assert not output.joinpath("report.html").exists()
    assert json.loads(output.joinpath("diagnostics.json").read_text())["diagnostics"][0]["code"] == "internal_error"


def test_help_and_invalid_option(tmp_path):
    assert run_cli(tmp_path, "--help").returncode == 0
    result = run_cli(tmp_path, "--missing")
    assert result.returncode == 64 and result.stderr.startswith("latex-review: 错误：")


def test_output_cannot_replace_project_source(tmp_path):
    project(tmp_path)
    original = (tmp_path / "main.tex").read_text()
    result = run_cli(tmp_path, "main.tex", "--output", str(tmp_path))
    assert result.returncode == 64
    assert (tmp_path / "main.tex").read_text() == original


def test_html_graphic_is_never_copied_or_linked(tmp_path):
    project(tmp_path)
    (tmp_path / "fig").mkdir()
    (tmp_path / "fig/attack.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        r"\begin{figure}\includegraphics{fig/attack.html}\caption{Figure}\end{figure}", encoding="utf-8")
    git(tmp_path, "add", "main.tex", "fig/attack.html")
    git(tmp_path, "commit", "-qm", "graphic fixture")
    result = run_cli(tmp_path, "main.tex")
    assert result.returncode == 2, result.stderr
    output = tmp_path / ".latex-review/latest"
    html = output.joinpath("report.html").read_text()
    assert "图资源格式未支持" in html
    assert 'href="assets/old/fig/attack.html"' not in html
    assert 'href="assets/new/fig/attack.html"' not in html
    assert not output.joinpath("assets/old/fig/attack.html").exists()
    assert not output.joinpath("assets/new/fig/attack.html").exists()
    diagnostics = json.loads(output.joinpath("diagnostics.json").read_text())["diagnostics"]
    assert any(item["code"] == "unsupported_dependency" for item in diagnostics)
    assert any(item["code"] == "report_asset_unsupported" for item in diagnostics)

    (tmp_path / "fig/attack.png").write_text("<script>alert(2)</script>", encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        r"\begin{figure}\includegraphics{fig/attack.png}\caption{Figure}\end{figure}", encoding="utf-8")
    git(tmp_path, "add", "main.tex", "fig/attack.png")
    git(tmp_path, "commit", "-qm", "disguised graphic fixture")
    disguised = run_cli(tmp_path, "main.tex")
    assert disguised.returncode == 2, disguised.stderr
    html = output.joinpath("report.html").read_text()
    assert 'href="assets/old/fig/attack.png"' not in html
    assert not output.joinpath("assets/old/fig/attack.png").exists()


def test_invalid_config_invalidates_previous_report(tmp_path):
    project(tmp_path)
    assert run_cli(tmp_path, "main.tex").returncode == 0
    default = tmp_path / ".latex-review/latest"
    assert default.joinpath("report.html").exists()
    (tmp_path / ".latex-review.toml").write_text("[render]\nmath='unsafe'\n", encoding="utf-8")
    failed = run_cli(tmp_path, "main.tex")
    assert failed.returncode == 64
    assert not default.joinpath("report.html").exists()
    assert not default.joinpath("diff.json").exists()
    assert json.loads(default.joinpath("diagnostics.json").read_text())["diagnostics"][0]["code"] == "configuration_error"

    custom = tmp_path / "custom-output"
    assert run_cli(tmp_path, "main.tex", "--output", str(custom), "--math", "mathjax").returncode == 0
    assert custom.joinpath("report.html").exists()
    failed = run_cli(tmp_path, "main.tex", "--output", str(custom))
    assert failed.returncode == 64
    assert not custom.joinpath("report.html").exists()
    assert not custom.joinpath("diff.json").exists()
    assert json.loads(custom.joinpath("diagnostics.json").read_text())["diagnostics"][0]["code"] == "configuration_error"

    (tmp_path / ".latex-review.toml").write_text("", encoding="utf-8")
    assert run_cli(tmp_path, "main.tex", "--output", str(custom)).returncode == 0
    (tmp_path / ".latex-review.toml").write_text("git = 'invalid-table'\n", encoding="utf-8")
    failed = run_cli(tmp_path, "main.tex", "--output", str(custom))
    assert failed.returncode == 64
    assert not custom.joinpath("report.html").exists()
    assert not custom.joinpath("diff.json").exists()
    assert json.loads(custom.joinpath("diagnostics.json").read_text())["diagnostics"][0]["code"] == "configuration_error"

    source_before = (tmp_path / "main.tex").read_text()
    assert run_cli(tmp_path, "main.tex", "--output", str(tmp_path)).returncode == 64
    assert (tmp_path / "main.tex").read_text() == source_before
    external = tmp_path / "external"
    external.mkdir()
    (external / "report.html").write_text("outside", encoding="utf-8")
    linked = tmp_path / "linked-output"
    linked.symlink_to(external, target_is_directory=True)
    assert run_cli(tmp_path, "main.tex", "--output", str(linked)).returncode == 64
    assert (external / "report.html").read_text() == "outside"
