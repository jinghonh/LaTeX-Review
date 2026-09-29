import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.rules import check_rules


ROOT = Path(__file__).resolve().parents[1]


def _pair(tmp_path: Path, before: str, after: str) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "main.tex").write_text(before, encoding="utf-8")
    (new / "main.tex").write_text(after, encoding="utf-8")
    return old, new


def test_rule_status_evidence_sources_and_report(tmp_path):
    old, new = _pair(tmp_path, r"""\begin{document}
\cite{stale,fixed} and \cite{valid}. \ref{lost}.
\label{dup}
\label{dup}
\begin{figure}\caption{Stable}\label{fig:stable}\end{figure}
\begin{table}\caption{Resolved}\label{tab:resolved}\end{table}
\begin{equation}a=b\tag{1}\label{eq:known}\end{equation}
\end{document}
""", r"""\begin{document}
\cite{stale,new} and \ref{lost} and \ref{tab:resolved}.
\label{dup}
\label{dup}
\label{lost}
\begin{figure}\caption{Stable}\label{fig:stable}\end{figure}
\begin{figure}\caption{New}\label{fig:new}\end{figure}
\begin{table}\caption{Resolved}\label{tab:resolved}\end{table}
\begin{equation}a=b\tag{2}\label{eq:known}\end{equation}
\end{document}
""")
    for root in old, new:
        (root / "refs.bib").write_text("@article{valid, title={Valid}}\n", encoding="utf-8")
        source = (root / "main.tex").read_text(encoding="utf-8")
        (root / "main.tex").write_text("\\bibliography{refs}\n" + source, encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        rules = check_rules(before, after)
        keyed = {(item.code, item.evidence[0]): item for item in rules}
        assert keyed[("bibliography_key_unresolved", "键：stale")].rule_status == "existing"
        assert keyed[("bibliography_key_unresolved", "键：fixed")].rule_status == "resolved"
        assert keyed[("bibliography_key_unresolved", "键：new")].rule_status == "new"
        assert all("valid" not in item.message for item in rules)
        assert keyed[("unresolved_reference", "键：lost")].rule_status == "resolved"
        duplicate = keyed[("duplicate_label", "键：dup")]
        assert duplicate.rule_status == "existing"
        assert [item.start_line for item in duplicate.related_sources_old] == [4, 5]
        assert [item.start_line for item in duplicate.related_sources_new] == [4, 5]
        assert len(duplicate.evidence) == 6
        assert keyed[("figure_unreferenced", "键：fig:stable")].rule_status == "existing"
        assert keyed[("figure_unreferenced", "键：fig:new")].rule_status == "new"
        assert keyed[("table_unreferenced", "键：tab:resolved")].rule_status == "resolved"
        changed = next(item for item in rules if item.code == "equation_number_changed")
        assert changed.rule_status == "new" and "1" in changed.message and "2" in changed.message
        assert changed.source_old.start_line == 8 and changed.source_new.start_line == 10
        report = write_report(before, after, compare_projects(before, after), tmp_path / "report")
    machine = json.loads(report.diff_json.read_text(encoding="utf-8"))
    separate = json.loads((report.directory / "diagnostics.json").read_text(encoding="utf-8"))
    assert machine["diagnostics"] == separate["diagnostics"]
    assert "规则状态：既有" not in report.html.read_text(encoding="utf-8")
    assert any(item["code"] == "duplicate_label" and len(item["related_sources_new"]) == 2
               for item in machine["diagnostics"])
    assert not any(item["code"] == "bibliography_key_unresolved" and "valid" in item["message"]
                   for item in machine["diagnostics"])


def test_number_unknown_and_deleted_valid_citation_do_not_invent_error(tmp_path):
    old, new = _pair(tmp_path, r"""\begin{document}
\cite{valid}
\begin{equation}x=1\label{eq:x}\end{equation}
\end{document}""", r"""\begin{document}
\begin{equation}y=2\end{equation}
\begin{equation}x=1\label{eq:x}\end{equation}
\end{document}""")
    for root in old, new:
        (root / "refs.bib").write_text("@article{valid, title={Valid}}\n", encoding="utf-8")
        source = (root / "main.tex").read_text(encoding="utf-8")
        (root / "main.tex").write_text("\\bibliography{refs}\n" + source, encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        rules = check_rules(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    assert [item.code for item in rules] == ["equation_number_unknown"]
    assert rules[0].evidence[1:3] == ("旧侧显式编号：未知", "新侧显式编号：未知")


def test_rule_warning_does_not_set_cli_exit_code(tmp_path):
    source = "\\begin{document}\\cite{missing}\\label{x}\\label{x}\\end{document}"
    old, new = _pair(tmp_path, source, source)
    output = tmp_path / "out"
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
                             "--new-dir", str(new), "--entry", "main.tex", "--output", str(output)],
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert {item["code"] for item in diagnostics} >= {"bibliography_key_unresolved", "duplicate_label"}
    assert all(item["rule_status"] == "existing" for item in diagnostics)


def test_comments_verbatim_and_unlabelled_figure_are_outside_rule_scope(tmp_path):
    source = r"""\begin{document}
% \cite{missing}\label{dup}\label{dup}
\begin{verbatim}\cite{missing}\label{dup}\label{dup}\end{verbatim}
\begin{figure}\caption{No label}\end{figure}
\end{document}"""
    old, new = _pair(tmp_path, source, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        assert check_rules(parse_project(pair.old.expand()), parse_project(pair.new.expand())) == ()


def test_duplicate_label_reports_each_original_file(tmp_path):
    source = "\\begin{document}\n\\input{a}\n\\input{b}\n\\end{document}"
    old, new = _pair(tmp_path, source, source)
    for root in old, new:
        (root / "a.tex").write_text("\\label{shared}\n", encoding="utf-8")
        (root / "b.tex").write_text("\\label{shared}\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        diagnostics = check_rules(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    duplicate = next(item for item in diagnostics if item.code == "duplicate_label")
    assert [(site.file, site.start_line) for site in duplicate.related_sources_new] == [
        ("a.tex", 1), ("b.tex", 1)]


def test_unreadable_bibliography_does_not_invent_new_missing_key(tmp_path):
    source = "\\bibliography{refs}\n\\begin{document}\\cite{known}\\end{document}"
    old, new = _pair(tmp_path, source, source)
    (old / "refs.bib").write_text("@article{known, title={Known}}\n", encoding="utf-8")
    (new / "refs.bib").write_bytes(b"\xff\xfe")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        assert any(item.code == "bibliography_unreadable" for item in after.diagnostics)
        assert not any(item.code == "bibliography_key_unresolved" for item in check_rules(before, after))
        report = write_report(before, after, compare_projects(before, after), tmp_path / "out")
    codes = {item["code"] for item in json.loads((report.directory / "diagnostics.json").read_text())["diagnostics"]}
    assert "bibliography_unreadable" in codes
    assert "bibliography_key_unresolved" not in codes


def test_inline_verb_and_verb_star_do_not_produce_rule_findings(tmp_path):
    source = r"""\begin{document}
\verb|\cite{missing} \ref{y}| and \verb*+\label{dup}\label{dup}+
\verb!% \cite{also-missing}! followed by \ref{x} and \label{x}.
\end{document}"""
    old, new = _pair(tmp_path, source, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        assert check_rules(parse_project(pair.old.expand()), parse_project(pair.new.expand())) == ()


def test_comma_is_only_a_separator_for_citation_keys(tmp_path):
    source = r"""\begin{document}
\begin{figure}\caption{Comma label}\label{fig:a,b}\end{figure}
\ref{fig:a,b} and \ref{missing,a}
\cite{first,second}
\end{document}"""
    old, new = _pair(tmp_path, source, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        diagnostics = check_rules(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    keys = {(item.code, item.evidence[0]) for item in diagnostics}
    assert keys == {("unresolved_reference", "键：missing,a"),
                    ("bibliography_key_unresolved", "键：first"),
                    ("bibliography_key_unresolved", "键：second")}


@pytest.mark.parametrize("unavailable_side,resource_code", [
    ("old", "bibliography_unreadable"),
    ("new", "bibliography_unreadable"),
    ("new", "missing_dependency"),
])
def test_unavailable_bibliography_cannot_prove_rule_status(tmp_path, unavailable_side, resource_code):
    source = "\\bibliography{refs}\n\\begin{document}\\cite{missing}\\end{document}"
    old, new = _pair(tmp_path, source, source)
    unavailable = old if unavailable_side == "old" else new
    reliable = new if unavailable_side == "old" else old
    (reliable / "refs.bib").write_text("@article{other, title={Other}}\n", encoding="utf-8")
    if resource_code == "bibliography_unreadable":
        (unavailable / "refs.bib").write_bytes(b"\xff\xfe")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        assert not any(item.code == "bibliography_key_unresolved" for item in check_rules(before, after))
        report = write_report(before, after, compare_projects(before, after), tmp_path / "report")
    diff = json.loads(report.diff_json.read_text(encoding="utf-8"))["diagnostics"]
    separate = json.loads((report.directory / "diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert diff == separate
    assert resource_code in {item["code"] for item in diff}
    assert not any(item["code"] == "bibliography_key_unresolved" for item in diff)
    output = tmp_path / "cli-report"
    cli = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
                          "--new-dir", str(new), "--entry", "main.tex", "--output", str(output)],
                         cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                         capture_output=True, text=True)
    assert cli.returncode == 2, cli.stderr
    cli_diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert resource_code in {item["code"] for item in cli_diagnostics}
    assert not any(item["code"] == "bibliography_key_unresolved" for item in cli_diagnostics)


def test_unrelated_missing_graphic_does_not_hide_known_missing_citation(tmp_path):
    source = ("\\bibliography{refs}\n\\begin{document}\\cite{missing}"
              "\\begin{figure}\\includegraphics{absent.png}\\end{figure}\\end{document}")
    old, new = _pair(tmp_path, source, source)
    for root in old, new:
        (root / "refs.bib").write_text("@article{other, title={Other}}\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        citation = next(item for item in check_rules(before, after)
                        if item.code == "bibliography_key_unresolved")
    assert citation.rule_status == "existing"


@pytest.mark.parametrize("command", ["input", "include"])
def test_missing_handwritten_bibliography_dependency_cannot_prove_new_missing_key(tmp_path, command):
    source = ("\\begin{document}\\cite{known}"
              f"\\begin{{thebibliography}}{{9}}\\{command}{{refs}}"
              "\\end{thebibliography}\\end{document}")
    old, new = _pair(tmp_path, source, source)
    (old / "refs.tex").write_text("\\bibitem{known} Old source.\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        assert "bib:known" in before.labels
        assert any(item.code == "missing_dependency" for item in after.diagnostics)
        assert not any(item.code == "bibliography_key_unresolved" for item in check_rules(before, after))
        report = write_report(before, after, compare_projects(before, after), tmp_path / "report")
    diff = json.loads(report.diff_json.read_text(encoding="utf-8"))["diagnostics"]
    separate = json.loads((report.directory / "diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert diff == separate
    assert "missing_dependency" in {item["code"] for item in diff}
    assert not any(item["code"] == "bibliography_key_unresolved" for item in diff)
    cli_output = tmp_path / "cli-report"
    cli = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
                          "--new-dir", str(new), "--entry", "main.tex", "--output", str(cli_output)],
                         cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
                         capture_output=True, text=True)
    assert cli.returncode == 2, cli.stderr
    cli_diagnostics = json.loads((cli_output / "diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert "missing_dependency" in {item["code"] for item in cli_diagnostics}
    assert not any(item["code"] == "bibliography_key_unresolved" for item in cli_diagnostics)


def test_missing_input_outside_bibliography_does_not_hide_citation_rule(tmp_path):
    source = "\\begin{document}\\cite{missing}\\input{unrelated}\\end{document}"
    old, new = _pair(tmp_path, source, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        citation = next(item for item in check_rules(before, after)
                        if item.code == "bibliography_key_unresolved")
    assert citation.rule_status == "existing"
