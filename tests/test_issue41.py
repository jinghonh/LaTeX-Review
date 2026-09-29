"""#41：阅读界面与同键文献变更的最小回归样例。"""

from __future__ import annotations

import json
import re

from latex_review import compare_projects, parse_project, render_preview, resolve_sources, write_report
from latex_review.contract import validate_document


def _pair(tmp_path, old_tex: str, new_tex: str, old_bib: str = "", new_bib: str = ""):
    old, new = tmp_path / "old", tmp_path / "new"
    for root, tex, bib in ((old, old_tex, old_bib), (new, new_tex, new_bib)):
        root.mkdir()
        (root / "main.tex").write_text(tex, encoding="utf-8")
        if bib:
            (root / "refs.bib").write_text(bib, encoding="utf-8")
    return old, new


def test_same_key_bibliography_fields_count_once_without_exposing_values(tmp_path):
    tex = r"\begin{document}见 \cite{k}，再见 \cite{k}。\bibliography{refs}\end{document}"
    old, new = _pair(tmp_path, tex, tex,
                     "@article{k, author={秘密旧作者}, title={秘密旧题名}, year={2020}}",
                     "@article{k, author={秘密新作者}, title={秘密新题名}, year={2020}}")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        comparison = compare_projects(before, after)
        validate_document(comparison.document)
        report = write_report(before, after, comparison, tmp_path / "report", pdf_converter="")
    assert comparison.document.summary.changes == 1
    assert comparison.document.summary.category_hits == {"citation": 1}
    assert comparison.document.summary.added_words == comparison.document.summary.removed_words == 0
    change = comparison.document.changes[0]
    assert change.node_type == "bibliography_change"
    assert change.details[0].summary == "引用键 k：作者、题名变化"
    html = report.html.read_text(encoding="utf-8")
    assert "引用键 k：作者、题名变化" in html
    assert 'data-categories="citation"' in html
    assert all(value not in html for value in ("秘密旧作者", "秘密新作者", "秘密旧题名", "秘密新题名"))
    assert "查看 LaTeX 原文" not in html and 'id="review-data"' not in html
    assert "␠" not in html
    assert html.count('[k]</span>') == 4  # 两侧各有两处正文引用，每处只出现一次键。
    data = json.loads(report.diff_json.read_text(encoding="utf-8"))
    assert data["summary"]["changes"] == 1


def test_numbered_handwritten_citations_are_side_local(tmp_path):
    prefix = r"\begin{document}正文 \cite{k}。\begin{thebibliography}{9}"
    suffix = r" 条目。\end{thebibliography}\end{document}"
    old, new = _pair(tmp_path, prefix + r"\bibitem[3]{k}" + suffix,
                     prefix + r"\bibitem[7]{k}" + suffix)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        preview = render_preview(parse_project(pair.old.expand()), parse_project(pair.new.expand())).html
    old_panel = re.search(r'<section class="preview-side"[^>]*data-side="old"[^>]*>(.*?)</section>', preview, re.S).group(1)
    new_panel = re.search(r'<section class="preview-side"[^>]*data-side="new"[^>]*>(.*?)</section>', preview, re.S).group(1)
    assert re.search(r'\[<a[^>]*>3</a>\]', old_panel)
    assert re.search(r'\[<a[^>]*>7</a>\]', new_panel)
    assert ">7</a>" not in old_panel and ">3</a>" not in new_panel


def test_unpreviewable_table_and_unknown_macro_keep_short_placeholder(tmp_path):
    tex = (r"\begin{document}\begin{table}\begin{tabular}{c}"
           r"\multicolumn{1}{c}{私密源码}\end{tabular}\end{table}"
           r"\mystery{私密参数}\end{document}")
    old, new = _pair(tmp_path, tex, tex)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        html = write_report(before, after, compare_projects(before, after), tmp_path / "report",
                            pdf_converter="").html.read_text(encoding="utf-8")
    assert html.count("此处暂无法预览") >= 2
    assert all(value not in html for value in (r"\multicolumn", r"\mystery", "私密源码", "私密参数"))
    assert 'class="node-warning"' not in html and 'class="report-diagnostics"' not in html
    assert '.preview-side[data-side="old"] .is-highlighted' in html
    assert '.preview-side[data-side="new"] .is-highlighted' in html
