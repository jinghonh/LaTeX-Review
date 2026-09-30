"""预览片段中的引用命令和公式保留原文，不产生解析器日志。"""

import json
import logging
import re

import pytest

from latex_review import parse_project, render_preview, resolve_sources
from latex_review import compare_projects, write_report
from latex_review.text_diff import scan_latex, split_sentences
from latex_review.preview import _inline_html


@pytest.mark.parametrize("formula", [
    r"\(\widehat c=\operatorname{Proj}(x).\)",
    r"\[\|v\|^2=v_0^2+\frac12\sum_{k=1}^N v_k^2.\]",
    r"\(x+\\)y.\)",
    r"$x+\$y.$",
])
def test_math_delimiters_keep_formula_atomic(formula):
    text = "公式 " + formula + " 保持完整。后句。"
    assert [sentence.text for sentence in split_sentences(text)] == [
        "公式 " + formula + " 保持完整。", "后句。"]
    tokens = scan_latex(text)[0]
    assert [token.text for token in tokens if token.kind == "math"] == [formula]


def test_report_translation_candidates_preserve_parenthesized_math(tmp_path, monkeypatch):
    formula = "\\(\n\\widehat c_m(t)=\\operatorname{Proj}(v_m).\n\\)"
    records = []
    monkeypatch.setattr(logging.Logger, "handle", lambda logger, record: records.append(record))
    for side, word in (("old", "旧句"), ("new", "新句")):
        root = tmp_path / side
        root.mkdir()
        (root / "main.tex").write_text(r"\begin{document}" + "\n" + word + " " + formula
                                      + " 后文。\n" + r"\end{document}", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(before, after, compare_projects(before, after), tmp_path / "report", pdf_converter="")
    units = json.loads((report.directory / "translation-units.json").read_text())
    assert len(units) == 2
    for unit in units:
        assert len(unit["sentences"]) == 1
        protected = unit["sentences"][0]["protected"]
        assert [part["latex"] for part in protected] == [formula]
        assert 'class="math-tex"' in protected[0]["html"]
        assert "此处暂无法预览" not in protected[0]["html"]
    assert not [record.getMessage() for record in records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("fragment", [
    r"关键词 \sep 其他关键词",
    r"\textbf{参见 \citep[见][第 2 页]{key} 和 \eqref{eq:x}}。",
    r"\textbf{预测 $\widehat c$ 和 $\operatorname{Lift}(x)$}。",
    r"预测 $\widehat{\mathcal P}(t)$，见 \citep{key}。",
])
def test_supported_fragments_keep_content_without_parser_warnings(tmp_path, monkeypatch, fragment):
    records = []
    original = logging.Logger.handle

    def capture(logger, record):
        records.append(record)
        original(logger, record)

    monkeypatch.setattr(logging.Logger, "handle", capture)
    source = (r"\begin{document}" + "\n" + fragment + "\n"
              r"\begin{equation}x=1\label{eq:x}\end{equation}" + "\n"
              r"\begin{thebibliography}{9}\bibitem{key}资料。\end{thebibliography}" + "\n"
              r"\end{document}")
    (tmp_path / "main.tex").write_text(source, encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path, new_dir=tmp_path) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = render_preview(before, after)
    assert not [record.getMessage() for record in records if record.levelno >= logging.WARNING]
    assert "preview-unavailable\">此处暂无法预览" not in result.html
    if r"\sep" in fragment:
        assert re.search(r"关键词\s+·\s+其他关键词", result.html)
    if r"\citep" in fragment:
        assert 'class="citation"' in result.html
        assert "[<a " in result.html
        assert "{key}" not in result.html
    if r"\widehat" in fragment:
        assert r"\widehat" in result.html
    if r"\operatorname" in fragment:
        assert r"\operatorname{Lift}(x)" in result.html


@pytest.mark.parametrize("fragment", [r"\citep[见][第 2 页]{key}", r"\citet*{key}", r"\eqref{eq:x}"])
def test_translation_token_preview_handles_literal_citation_and_reference(tmp_path, monkeypatch, fragment):
    records = []
    monkeypatch.setattr(logging.Logger, "handle", lambda logger, record: records.append(record))
    (tmp_path / "main.tex").write_text(r"\begin{document}\begin{equation}x\label{eq:x}\end{equation}"
                                      r"\begin{thebibliography}{9}\bibitem{key}资料\end{thebibliography}"
                                      r"\end{document}", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path, new_dir=tmp_path) as pair:
        project = parse_project(pair.old.expand())
        html = _inline_html(fragment, project, "old", [])
    assert "此处暂无法预览" not in html
    assert "{key}" not in html
    assert "cross-ref" in html if "eqref" in fragment else "citation" in html
    assert not [record.getMessage() for record in records if record.levelno >= logging.WARNING]


def test_malformed_fragment_becomes_diagnostic_and_restores_logging(tmp_path, capfd):
    from plasTeX.Logging import getLogger
    logs = (getLogger(), getLogger("status"), getLogger("(status)"))
    previous = [(log.level, tuple(log.filters)) for log in logs]
    (tmp_path / "main.tex").write_text(r"\begin{document}正文\end{document}", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path, new_dir=tmp_path) as pair:
        project = parse_project(pair.old.expand())
        diagnostics = []
        html = _inline_html(r"\widehat", project, "old", [], diagnostics)
    assert "此处暂无法预览" in html
    assert [item.code for item in diagnostics] == ["preview_fallback"]
    assert capfd.readouterr().err == ""
    assert [(log.level, tuple(log.filters)) for log in logs] == previous
