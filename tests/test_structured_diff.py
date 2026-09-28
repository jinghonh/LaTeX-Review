"""结构化差异的代表性合成论文与主变更口径。"""

from pathlib import Path

import pytest

from latex_review import compare_projects, dumps, parse_project, render_preview, resolve_sources, validate_document


def _compare(tmp_path: Path, before: str, after: str):
    for side, body in (("old", before), ("new", after)):
        root = tmp_path / side
        root.mkdir(parents=True)
        (root / "main.tex").write_text("\\begin{document}\n" + body + "\n\\end{document}\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = compare_projects(old, new)
        validate_document(result.document)
        return old, new, result


def test_inline_display_align_math_and_metadata_share_parent(tmp_path):
    old, new, result = _compare(
        tmp_path,
        r"Text $x_i$ and \(y\)." + "\n\n" +
        r"\begin{equation}\label{eq:old}a=b\tag{1}\end{equation}" + "\n" +
        r"\begin{align}x&=1\\ y&=2\end{align}",
        r"Text $x_j$ and \(y\)." + "\n\n" +
        r"\begin{equation}\label{eq:new}a=b\tag{2}\end{equation}" + "\n" +
        r"\begin{align}x&=1\\ y&=3\end{align}",
    )
    assert result.document.summary.changes == 3
    assert result.document.summary.category_hits == {"equation": 3}
    paragraph = next(change for change in result.document.changes if change.node_type == "paragraph")
    assert len(paragraph.details) == 1
    assert paragraph.details[0].source_old.file == paragraph.details[0].source_new.file == "main.tex"
    metadata = next(change for change in result.document.changes if "eq:old" in next(
        node.raw_latex for node in result.document.nodes_old if node.id == change.old_node_id))
    assert len(metadata.details) == 1
    assert "标签或编号" in metadata.details[0].summary
    assert metadata.details[0].old_text == r"\label{eq:old}, \tag{1}"
    assert "a=b" not in metadata.details[0].summary
    assert "3" in next(change for change in result.document.changes if change is not metadata and change.node_type == "equation").details[0].summary
    assert "math-tex" in render_preview(old, new).html


def test_citations_by_position_preserve_repeats_sources_and_reorder(tmp_path):
    _, _, result = _compare(tmp_path,
                            r"Alpha \cite{same,same,b} beta \cite{same} gamma \cite{old}.",
                            r"Alpha \cite{b,same} beta \cite{same,new} gamma \cite{new}." )
    assert result.document.summary.changes == 1
    change = result.document.changes[0]
    assert change.categories == ("citation",)
    assert len(change.details) == 2
    assert all(detail.source_old and detail.source_new for detail in change.details)
    assert change.details[0].source_old.start_column != change.details[1].source_old.start_column
    assert "第 2 处" in change.details[0].summary
    assert "第 3 处" in change.details[1].summary
    assert result.document.summary.category_hits == {"citation": 1}
    assert result.document.summary.added_words == result.document.summary.removed_words == 0
    assert "source_old" in dumps(result.document)


def test_same_citation_key_moves_between_positions_without_global_cancellation(tmp_path):
    _, _, result = _compare(tmp_path, r"A \cite{same}. B \cite{other}.",
                            r"A \cite{other}. B \cite{same}.")
    assert result.document.summary.changes == 1
    assert len(result.document.changes[0].details) == 2
    assert all(detail.source_old and detail.source_new for detail in result.document.changes[0].details)


def test_paragraph_text_citation_and_math_count_once(tmp_path):
    _, _, result = _compare(tmp_path, r"Old word $x$ \cite{a}.", r"New word $y$ \cite{b}.")
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"citation": 1, "equation": 1, "text": 1}
    assert [d.category for d in result.document.changes[0].details] == ["text", "citation", "equation"]


def test_figure_fields_combine_and_missing_asset_keeps_preview(tmp_path):
    old, new, result = _compare(tmp_path,
                                r"\begin{figure}\includegraphics{old.pdf}\caption{Old caption}\label{fig:x}\end{figure}",
                                r"\begin{figure}\includegraphics{new.pdf}\caption{New caption}\label{fig:y}\end{figure}")
    assert result.document.summary.changes == 1
    assert result.document.changes[0].node_type == "figure"
    assert len(result.document.changes[0].details) == 3
    assert result.document.summary.category_hits == {"figure": 1}
    assert any(d.code == "missing_dependency" for d in result.document.diagnostics)
    preview = render_preview(old, new)
    assert "old.pdf" in preview.html and "new.pdf" in preview.html
    assert "missing_dependency" in preview.html


@pytest.mark.parametrize(("old_asset", "new_asset", "old_caption", "new_caption", "old_label", "new_label", "field"), [
    ("old.pdf", "new.pdf", "Same", "Same", "fig:same", "fig:same", "资源路径"),
    ("same.pdf", "same.pdf", "Before", "After", "fig:same", "fig:same", "图注"),
    ("same.pdf", "same.pdf", "Same", "Same", "fig:old", "fig:new", "标签"),
])
def test_figure_single_field_variants(tmp_path, old_asset, new_asset, old_caption, new_caption,
                                      old_label, new_label, field):
    def figure(asset, caption, label):
        return rf"\begin{{figure}}\includegraphics{{{asset}}}\caption{{{caption}}}\label{{{label}}}\end{{figure}}"
    _, _, result = _compare(tmp_path, figure(old_asset, old_caption, old_label),
                            figure(new_asset, new_caption, new_label))
    assert result.document.summary.changes == 1
    assert len(result.document.changes[0].details) == 1
    assert field in result.document.changes[0].details[0].summary


def test_tables_whole_change_and_complex_source_fallback(tmp_path):
    old, new, result = _compare(tmp_path,
                                r"\begin{table}\caption{Scores}\begin{tabular}{cc}A&B\\ 1&2\end{tabular}\end{table}",
                                r"\begin{table}\caption{Scores}\begin{tabular}{ccc}A&B&C\\ \multicolumn{2}{c}{1}&\nested{3}\end{tabular}\end{table}")
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"table": 1}
    detail = result.document.changes[0].details[0]
    assert "单元格定位" in detail.summary
    assert r"\multicolumn" in detail.new_text and r"\nested" in detail.new_text
    assert r"\multicolumn" in render_preview(old, new).html


def test_table_row_insert_is_one_whole_change(tmp_path):
    _, _, result = _compare(tmp_path, r"\begin{tabular}{c}A\\ B\end{tabular}",
                            r"\begin{tabular}{c}A\\ X\\ B\end{tabular}")
    assert result.document.summary.changes == 1
    assert result.document.changes[0].node_type == "table"


@pytest.mark.parametrize(("before", "after", "kind"), [
    ("", r"\begin{tabular}{c}1\end{tabular}", "added"),
    (r"\begin{tabular}{c}1\end{tabular}", "", "removed"),
    (r"\begin{array}{cc}1&2\end{array}", r"\begin{array}{ccc}1&2&3\end{array}", "modified"),
])
def test_table_add_remove_array_and_column_insert(tmp_path, before, after, kind):
    old, new, result = _compare(tmp_path, before, after)
    assert result.document.summary.changes == 1
    assert result.document.changes[0].kind == kind
    assert result.document.summary.category_hits == {"table": 1}
    assert "table-preview" in render_preview(old, new).html


def test_equation_local_fallback_preserves_other_changes(tmp_path):
    _, _, result = _compare(tmp_path,
                            r"\begin{equation}x=1\end{equation}" + "\n" + r"Stable text.",
                            r"\begin{equation}x={2\end{equation}" + "\n" + r"Changed text.")
    assert any(d.code == "equation_diff_fallback" for d in result.document.diagnostics)
    assert any(change.node_type == "paragraph" for change in result.document.changes)
    assert any(change.node_type == "equation" and change.details[0].new_text for change in result.document.changes)


def test_inline_unknown_macro_keeps_both_raw_formulas(tmp_path):
    _, _, result = _compare(tmp_path, r"Value $\mystery{x}$ remains.", r"Value $\mystery{y}$ remains.")
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"equation": 1}
    assert result.document.changes[0].details[0].old_text == r"$\mystery{x}$"
    assert result.document.changes[0].details[0].new_text == r"$\mystery{y}$"
    assert any(d.code == "equation_diff_fallback" for d in result.document.diagnostics)
