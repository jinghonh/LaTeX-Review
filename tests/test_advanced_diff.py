"""第一点五版高级差异的可复现验收样例。"""

from __future__ import annotations

from pathlib import Path

from latex_review import compare_projects, dumps, parse_project, render_preview, resolve_sources, write_report


def _compare(tmp_path: Path, before: str, after: str):
    for side, body in (("old", before), ("new", after)):
        root = tmp_path / side
        root.mkdir(parents=True)
        (root / "main.tex").write_text("\\begin{document}\n" + body + "\n\\end{document}\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        return old, new, compare_projects(old, new)


def test_table_cells_rows_columns_and_preview_share_coordinates(tmp_path):
    old, new, result = _compare(tmp_path,
        r"\begin{tabular}{cc}A&$x_i$\\B&\cite{k}\&z\end{tabular}",
        r"\begin{tabular}{ccc}A&New&$x_j$\\B& &\cite{k}\&z\end{tabular}")
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"table": 1}
    change = result.document.changes[0]
    assert any(detail.kind == "added" and detail.column_new == 2 for detail in change.details)
    assert any(detail.kind == "modified" and (detail.row_old, detail.column_old, detail.row_new, detail.column_new)
               == (1, 2, 1, 3) for detail in change.details)
    assert not any("整体差异" in detail.summary for detail in change.details)
    assert 'data-row="1" data-column="3"' in render_preview(old, new).html
    report = write_report(old, new, result, tmp_path / "report", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    assert 'data-old-row="1" data-old-column="2" data-new-row="1" data-new-column="3"' in html
    assert "is-cell-highlighted" in html
    dumps(result.document)


def test_table_row_insertion_matrix_and_ambiguous_fallback(tmp_path):
    _, _, inserted = _compare(tmp_path / "insert",
        r"\begin{tabular}{c}Alpha\\Beta\end{tabular}",
        r"\begin{tabular}{c}Alpha\\New\\Beta\end{tabular}")
    assert inserted.document.summary.changes == 1
    assert any(detail.kind == "added" and detail.row_new == 2 for detail in inserted.document.changes[0].details)
    _, _, deleted = _compare(tmp_path / "delete",
        r"\begin{tabular}{ccc}A&Drop&B\\C&Gone&D\end{tabular}",
        r"\begin{tabular}{cc}A&B\\C&D\end{tabular}")
    assert deleted.document.summary.changes == 1
    assert any(detail.kind == "removed" and detail.column_old == 2 for detail in deleted.document.changes[0].details)

    _, _, matrix = _compare(tmp_path / "matrix",
        r"\[\begin{pmatrix}a&b\\c&d\end{pmatrix}\]",
        r"\[\begin{pmatrix}a&x\\c&d\end{pmatrix}\]")
    assert matrix.document.summary.changes == 1
    assert matrix.document.summary.category_hits["table"] == 1
    assert any(detail.row_old == 1 and detail.column_old == 2 for detail in matrix.document.changes[0].details)

    _, _, ambiguous = _compare(tmp_path / "ambiguous",
        r"\begin{tabular}{c}Same\\Same\\ \end{tabular}",
        r"\begin{tabular}{c}Same\\New\\Same\\ \end{tabular}")
    assert ambiguous.document.summary.changes == 1
    assert len(ambiguous.document.changes[0].details) == 1
    assert "歧义" in ambiguous.document.changes[0].details[0].summary
    assert ambiguous.document.changes[0].details[0].old_text.startswith(r"\begin{tabular}")
    _, _, empty = _compare(tmp_path / "empty",
        r"\begin{tabular}{c}A\\ \\ \\B\end{tabular}",
        r"\begin{tabular}{c}A\\ \\B\end{tabular}")
    assert len(empty.document.changes[0].details) == 1
    assert "歧义" in empty.document.changes[0].details[0].summary


def test_complex_table_falls_back_without_false_cell_boundary(tmp_path):
    _, _, result = _compare(tmp_path,
        r"\begin{tabular}{cc}A&B\end{tabular}",
        r"\begin{tabular}{cc}\multicolumn{2}{c}{A&B}\end{tabular}")
    assert result.document.summary.changes == 1
    detail = result.document.changes[0].details[0]
    assert detail.row_old is None and detail.row_new is None
    assert "跨行跨列" in detail.summary


def test_same_section_move_is_one_primary_change_and_not_double_occupied(tmp_path):
    old, new, result = _compare(tmp_path,
        "\\section{Results}\nAlpha unique paragraph.\n\nBeta unique paragraph.\n\nGamma unique paragraph.\n\nDelta unique paragraph.",
        "\\section{Results}\nBeta unique paragraph.\n\nGamma unique paragraph.\n\nAlpha unique paragraph.\n\nDelta unique paragraph.")
    moved = [change for change in result.document.changes if change.kind == "moved"]
    assert len(moved) == result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"move": 1}
    assert moved[0].source_old and moved[0].source_new
    assert len({pair.old_id for pair in result.mapping.pairs}) == len(result.mapping.pairs)
    assert len({pair.new_id for pair in result.mapping.pairs}) == len(result.mapping.pairs)
    report = write_report(old, new, result, tmp_path / "report", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    assert '<option value="moved">移动</option>' in html
    assert f'data-old="{moved[0].old_node_id}" data-new="{moved[0].new_node_id}"' in html


def test_ambiguous_short_reorder_falls_back_to_remove_and_add(tmp_path):
    _, _, result = _compare(tmp_path,
        "\\section{Results}\nAlpha unique paragraph.\n\nBeta unique paragraph.",
        "\\section{Results}\nBeta unique paragraph.\n\nAlpha unique paragraph.")
    assert not any(change.kind == "moved" for change in result.document.changes)
    assert [change.kind for change in result.document.changes].count("removed") == 2
    assert [change.kind for change in result.document.changes].count("added") == 2


def test_cross_section_move_with_local_edit_and_repeated_content_fallback(tmp_path):
    _, _, result = _compare(tmp_path / "moved",
        "\\section{First}\nStable method \\label{p:anchor} with parameter alpha.\n\\section{Second}\nKeep second section.",
        "\\section{First}\n\\section{Second}\nKeep second section.\n\nStable method \\label{p:anchor} with parameter beta.")
    moved = [change for change in result.document.changes if change.kind == "moved"]
    assert len(moved) == 1
    assert moved[0].matching_confidence >= .9
    assert moved[0].source_old and moved[0].source_new
    assert {detail.category for detail in moved[0].details} == {"text", "move"}
    assert result.document.summary.category_hits["move"] == 1
    assert result.document.summary.category_hits["text"] == 1

    _, _, repeated = _compare(tmp_path / "repeated",
        "\\section{First}\nRepeated common body.\n\nRepeated common body.\n\\section{Second}",
        "\\section{First}\n\\section{Second}\nRepeated common body.\n\nRepeated common body.")
    assert not any(change.kind == "moved" for change in repeated.document.changes)
    assert {change.kind for change in repeated.document.changes} >= {"added", "removed"}


def test_math_structure_summaries_and_conservative_fallback(tmp_path):
    cases = [
        (r"$x_i$", r"$x_j$", "下标替换"),
        (r"$f(x)$", r"$f(y)$", "参数变更"),
        (r"$a+b$", r"$a+b+c$", "子表达式新增"),
        (r"$\frac{x}{y}$", r"$\frac{z}{y}$", "分子变更"),
        (r"$\sqrt{x}$", r"$\sqrt{y}$", "根式内容变更"),
    ]
    for index, (before, after, expected) in enumerate(cases):
        _, _, result = _compare(tmp_path / str(index), "Value " + before + ".", "Value " + after + ".")
        assert result.document.summary.changes == 1
        assert expected in result.document.changes[0].details[0].summary
    _, _, unchanged = _compare(tmp_path / "space", r"Value $x + y$.", r"Value $x+y$.")
    assert unchanged.document.summary.changes == 0
    _, _, unsupported = _compare(tmp_path / "unsupported",
        r"Value $x+\mystery{a}$.", r"Value $x+\mystery{b}$.")
    detail = unsupported.document.changes[0].details[0]
    assert "词元" in detail.summary
    assert detail.old_text == r"$x+\mystery{a}$" and detail.new_text == r"$x+\mystery{b}$"
    assert any(item.code == "equation_diff_fallback" for item in unsupported.document.diagnostics)
    _, _, align = _compare(tmp_path / "align",
        r"\begin{align}a&=b\\c&=d\end{align}",
        r"\begin{align}a&=b\\c&=e\end{align}")
    assert align.document.summary.changes == 1
    assert "数学内容词元" in align.document.changes[0].details[0].summary
