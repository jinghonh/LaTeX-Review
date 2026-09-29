import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from latex_review import compare_projects, parse_project, render_preview, resolve_sources, write_report
from latex_review.cli import ConfigurationError, _config
from latex_review.macros import validate_macros
from latex_review.macros import expand_call
from latex_review.table_model import parse_table


def _write(root: Path, file: str, content: str) -> None:
    target = root / file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _roots(tmp_path: Path, source: str) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        root.mkdir()
        _write(root, "main.tex", source)
    return old, new


def test_bibliography_two_sides_missing_duplicate_and_no_semantic_change(tmp_path):
    source = r"\begin{document}见 \cite{same,partial,absent}.\bibliography{refs}\end{document}"
    old, new = _roots(tmp_path, source)
    _write(old, "refs.bib", "@article{same, author={旧作者}, title={旧题目}, year={2020}}\n"
           "@book{partial, title={仅有题目}}\n@misc{same, title={重复条目}}\n")
    _write(new, "refs.bib", "@article{same, author={新作者}, title={新题目}, year={2025}}\n"
           "@book{partial, title={仅有题目}}\n@misc{same, title={重复条目}}\n")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        comparison = compare_projects(before, after)
        report = write_report(before, after, comparison, tmp_path / "report", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    assert "旧作者" in html and "旧题目" in html and "2020" in html
    assert "新作者" in html and "新题目" in html and "2025" in html
    assert "partial" in html and "作者：缺失" in html and "年份：缺失" in html
    assert "absent" in html and "文献元数据未解析，保留引用键" in html
    assert comparison.document.summary.changes == 0
    codes = [item.code for item in report.document.diagnostics]
    assert "bibliography_duplicate_key" in codes
    assert "bibliography_missing_field" in codes
    assert "bibliography_key_unresolved" in codes
    assert json.loads(report.diff_json.read_text(encoding="utf-8"))["summary"]["changes"] == 0


def test_citation_optional_note_braces_do_not_replace_the_citation_key(tmp_path):
    old, new = _roots(tmp_path, r"\begin{document}\citep[compare {KeyA}]{ref}\bibliography{refs}\end{document}")
    for root in (old, new):
        _write(root, "refs.bib", "@article{ref, author={实际作者}, title={实际题目}, year={2024}}")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = render_preview(before, after)
    assert next(node.citations for node in before.nodes if node.review.type == "citation") == ("ref",)
    assert "实际作者" in result.html and "实际题目" in result.html
    assert "KeyA<small class=\"citation-metadata\"" not in result.html
    assert not any(item.code == "bibliography_key_unresolved" for item in result.diagnostics)


def test_parenthesized_bibtex_entry_ignores_protected_closing_parentheses(tmp_path):
    source = r"\begin{document}\cite{braced,quoted}\bibliography{refs}\end{document}"
    old, new = _roots(tmp_path, source)
    content = ('@article(braced, author={甲}, title={A closing ) inside a braced field}, year={2024})\n'
               '@article(quoted, author={乙}, title="A closing ) inside a quoted field", year={2025})\n')
    for root in (old, new):
        _write(root, "refs.bib", content)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = render_preview(before, after)
    assert before.bibliography["braced"].title == "A closing ) inside a braced field"
    assert before.bibliography["braced"].year == "2024"
    assert before.bibliography["quoted"].title == "A closing ) inside a quoted field"
    assert before.bibliography["quoted"].year == "2025"
    assert "A closing ) inside a braced field" in result.html
    assert not any(item.code == "bibliography_missing_field" for item in result.diagnostics)


def test_braced_bibtex_field_treats_unpaired_quote_as_literal(tmp_path):
    source = r"\begin{document}\cite{ref}\bibliography{refs}\end{document}"
    old, new = _roots(tmp_path, source)
    for root in (old, new):
        _write(root, "refs.bib", '@article{ref, author={作者}, title={A 5" monitor}, year=2024}')
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = render_preview(before, after)
    assert before.bibliography["ref"].title == 'A 5" monitor'
    assert before.bibliography["ref"].year == "2024"
    assert 'A 5&quot; monitor' in result.html
    assert not any(item.code == "bibliography_missing_field" for item in result.diagnostics)


def test_table_cells_render_citations_and_configured_macros_with_coordinates(tmp_path):
    source = (r"\begin{document}\begin{tabular}{cc}"
              r"\citep[compare {KeyA}]{ref}&\badge{重点}\end{tabular}\bibliography{refs}\end{document}")
    old, new = _roots(tmp_path, source)
    for root in (old, new):
        _write(root, "refs.bib", "@article{ref, author={作者}, title={题目}, year={2024}}")
    macros = validate_macros({"badge": {"arguments": 1, "strategy": "replace", "template": "【#1】"}})
    assert parse_table(r"\begin{tabular}{c}\badge{重点}\end{tabular}")[0] is None
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand(), macros=macros), parse_project(pair.new.expand(), macros=macros)
        result = render_preview(before, after)
    assert re.search(r'<td data-row="1" data-column="1">.*?作者：作者；题目：题目；年份：2024', result.html)
    assert 'KeyA<small class="citation-metadata"' not in result.html
    assert re.search(r'<td data-row="1" data-column="2">.*?【重点】', result.html)
    assert not any(item.code == "preview_table_fallback" for item in result.diagnostics)


def test_unsupported_table_cell_has_local_diagnostic(tmp_path):
    old, new = _roots(tmp_path, r"\begin{document}\begin{tabular}{c}\mystery{A}\end{tabular}\end{document}")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        result = render_preview(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    assert any(item.code == "preview_table_fallback" and item.source_old for item in result.diagnostics)


def test_table_cell_with_missing_reference_key_keeps_key_and_diagnostic(tmp_path):
    old, new = _roots(tmp_path, r"\begin{document}\begin{tabular}{c}\cite{absent}\end{tabular}\end{document}")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        result = render_preview(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    assert re.search(r'<td data-row="1" data-column="1">.*?absent.*?文献元数据未解析', result.html)
    assert any(item.code == "bibliography_key_unresolved" and item.source_old for item in result.diagnostics)


def test_nested_table_citation_missing_key_reaches_report_diagnostics(tmp_path):
    source = r"\begin{document}\begin{tabular}{c}\textbf{\cite{absent}}\end{tabular}\end{document}"
    old, new = _roots(tmp_path, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        comparison = compare_projects(before, after)
        report = write_report(before, after, comparison, tmp_path / "report", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    diff = json.loads(report.diff_json.read_text(encoding="utf-8"))
    diagnostics = json.loads(report.directory.joinpath("diagnostics.json").read_text(encoding="utf-8"))
    assert re.search(r'<td data-row="1" data-column="1">.*?absent.*?文献元数据未解析', html)
    assert any(item["code"] == "bibliography_key_unresolved" for item in diff["diagnostics"])
    assert any(item["code"] == "bibliography_key_unresolved" for item in diagnostics["diagnostics"])


def test_unsupported_bibliography_keeps_key_and_preview(tmp_path):
    old, new = _roots(tmp_path, r"\begin{document}\cite{handmade}\begin{thebibliography}{9}"
                      r"\bibitem{handmade} 原始条目。\end{thebibliography}\end{document}")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        result = render_preview(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    assert "handmade" in result.html and "文献元数据未解析" in result.html
    assert any(item.code == "bibliography_unsupported" for item in result.diagnostics)


def test_macro_placeholder_replace_raw_mismatch_recursion_and_original(tmp_path):
    source = (r"\begin{document}前 \badge{重点}；\rawbox{原词}；\badge；\loop{甲}。"
              r"\end{document}")
    old, new = _roots(tmp_path, source)
    macros = validate_macros({
        "badge": {"arguments": 1, "strategy": "replace", "template": "【#1】"},
        "rawbox": {"arguments": 1, "strategy": "raw"},
        "loop": {"arguments": 1, "strategy": "replace", "template": r"\loop{#1}"},
    })
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand(), macros=macros), parse_project(pair.new.expand(), macros=macros)
        result = render_preview(before, after)
    assert "【重点】" in result.html and "data-source-approximate" in result.html
    assert r"\rawbox{原词}" in result.html and "查看 LaTeX 原文" in result.html
    assert "宏调用参数数量不匹配" in " ".join(d.message for d in result.diagnostics)
    assert "递归宏定义" in " ".join(d.message for d in result.diagnostics)
    assert any(d.code == "preview_macro_fallback" and d.source_old for d in result.diagnostics)
    assert not any(node.review.type == "fallback" and "badge" in node.review.raw_latex for node in before.nodes)


def test_macro_configuration_rejects_executable_and_invalid_placeholders(tmp_path):
    with pytest.raises(ValueError, match="只能调用已配置"):
        validate_macros({"bad": {"arguments": 1, "strategy": "replace", "template": r"\input{#1}"}})
    with pytest.raises(ValueError, match="超出声明"):
        validate_macros({"bad": {"arguments": 1, "strategy": "replace", "template": "#2"}})
    with pytest.raises(ValueError, match="宏名无效"):
        validate_macros({"input": {"arguments": 1, "strategy": "raw"}})
    config = tmp_path / "config.toml"
    config.write_text('[macros.bad]\narguments = 1\nstrategy = "replace"\ntemplate = "#2"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="占位符"):
        _config(config, True)
    huge = "x" * 4100
    macros = validate_macros({"badge": {"arguments": 1, "strategy": "replace", "template": "#1"}})
    rendered, _, problem = expand_call(r"\badge{" + huge + "}", 0, "badge", macros)
    assert rendered.startswith(r"\badge{") and "4096" in problem


def test_cli_macro_configuration_and_html_escaping(tmp_path):
    old, new = _roots(tmp_path, r"\begin{document}\badge{<img src=x onerror=alert(1)>}\end{document}")
    config = tmp_path / "macros.toml"
    config.write_text('[macros.badge]\narguments = 1\nstrategy = "replace"\ntemplate = "【#1】"\n', encoding="utf-8")
    output = tmp_path / "output"
    command = [sys.executable, "-m", "latex_review.cli", "--old-dir", str(old), "--new-dir", str(new),
               "--entry", "main.tex", "--config", str(config), "--output", str(output)]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    html = output.joinpath("report.html").read_text(encoding="utf-8")
    assert "【&lt;img src=x onerror=alert(1)&gt;】" in html
    assert "<img src=x onerror=alert(1)>" not in html
    config.write_text('[macros.badge]\narguments = 1\nstrategy = "replace"\ntemplate = "#2"\n', encoding="utf-8")
    failed = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert failed.returncode == 64
    diagnostics = json.loads(output.joinpath("diagnostics.json").read_text(encoding="utf-8"))["diagnostics"]
    assert diagnostics[0]["code"] == "configuration_error" and "占位符" in diagnostics[0]["message"]


def test_macro_placeholder_in_inline_and_display_math(tmp_path):
    source = r"\begin{document}公式 $\mark{x}$。\[\mark{y}\]\end{document}"
    old, new = _roots(tmp_path, source)
    macros = validate_macros({"mark": {"arguments": 1, "strategy": "replace", "template": "#1"}})
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        result = render_preview(parse_project(pair.old.expand(), macros=macros),
                                parse_project(pair.new.expand(), macros=macros))
    assert "\\(x\\)" in result.html and "\\[y\\]" in result.html
    assert result.html.count('data-source-approximate="true"') >= 4
