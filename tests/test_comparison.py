from pathlib import Path
import unicodedata

from latex_review import (
    compare_projects, dumps, match_nodes, parse_project, resolve_sources, scan_latex, token_edits,
)
import latex_review.comparison as comparison_module


def _projects(tmp_path: Path, old_text: str, new_text: str):
    for side, text in (("old", old_text), ("new", new_text)):
        root = tmp_path / side
        root.mkdir(parents=True)
        (root / "main.tex").write_text("\\begin{document}\n" + text + "\n\\end{document}\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        yield parse_project(pair.old.expand()), parse_project(pair.new.expand())


def _paragraphs(project):
    return [node for node in project.nodes if node.review.type == "paragraph"]


def test_single_node_diff_failure_keeps_raw_and_other_changes(tmp_path, monkeypatch):
    old, new = next(_projects(tmp_path, "Alpha FAIL.\n\nStable before.",
                              "Alpha changed.\n\nStable after."))
    original = comparison_module.token_edits
    def fail_one(before, after):
        if any(token.text == "FAIL" for token in before):
            raise ValueError("broken token")
        return original(before, after)
    monkeypatch.setattr(comparison_module, "token_edits", fail_one)
    result = compare_projects(old, new)
    assert any(item.code == "node_diff_fallback" for item in result.document.diagnostics)
    assert any("FAIL" in detail.old_text for change in result.document.changes
               for detail in change.details if detail.old_text)
    assert any("Stable" in (detail.old_text or "") for change in result.document.changes
               for detail in change.details)


def test_inserted_paragraph_numbered_heading_and_repeated_body_stay_one_to_one(tmp_path):
    old_text = """\\section{1. Intro}\\label{sec:intro}
Alpha anchor.

Repeated body.

First boundary.

Repeated body.

Last boundary."""
    new_text = """\\section{2. Intro}\\label{sec:intro}
Fresh insertion.

Alpha anchor.

Repeated body.

First boundary.

Repeated body.

Last boundary."""
    old, new = next(_projects(tmp_path, old_text, new_text))
    result = compare_projects(old, new)
    assert len(result.mapping.pairs) == len(old.nodes)
    assert len(set(result.mapping.old_to_new.values())) == len(result.mapping.pairs)
    repeated = [n.review.id for n in _paragraphs(old) if "Repeated body" in n.review.raw_latex]
    assert len(repeated) == 2
    assert result.mapping.old_to_new[repeated[0]] != result.mapping.old_to_new[repeated[1]]
    assert result.document.summary.changes == 2  # 标题编号和新增段落。
    assert sum(change.kind == "added" for change in result.document.changes) == 1
    assert dumps(result.document) == dumps(compare_projects(old, new).document)


def test_ambiguous_duplicates_rejected_but_heavy_edit_with_two_anchors_matched(tmp_path):
    old, new = next(_projects(tmp_path, "Repeat.\n\nRepeat.", "Repeat.\n\nRepeat."))
    mapping = match_nodes(old, new)
    assert not mapping.pairs
    assert len(mapping.old_unmatched) == len(mapping.new_unmatched) == 2
    assert all("歧义" in item.reason for item in mapping.old_unmatched)
    rejected = compare_projects(old, new)
    assert [change.kind for change in rejected.document.changes] == ["removed", "removed", "added", "added"]
    assert all(change.matching_confidence <= .8 and "歧义" in change.summary for change in rejected.document.changes)
    old2, new2 = next(_projects(tmp_path / "second", "Start anchor.\n\nOriginal unrelated passage.\n\nEnd anchor.",
                                     "Start anchor.\n\nCompletely rewritten material.\n\nEnd anchor."))
    result = compare_projects(old2, new2)
    assert len(result.mapping.pairs) == 3
    assert result.document.summary.changes == 1
    assert result.document.changes[0].kind == "modified"


def test_repeated_labels_do_not_force_wrong_type_or_duplicate_pair(tmp_path):
    old, new = next(_projects(tmp_path, "\\section{X}\n\\label{dup} Alpha.\n\n\\label{dup} Beta.",
                              "\\section{X}\n\\label{dup} Alpha.\n\n\\label{dup} Beta."))
    mapping = match_nodes(old, new)
    assert len({p.new_id for p in mapping.pairs}) == len(mapping.pairs)
    assert len(mapping.pairs) == len(old.nodes)
    other_old, other_new = next(_projects(tmp_path / "types", "Plain text.",
                                          "\\begin{equation}Plain text.\\end{equation}"))
    assert not match_nodes(other_old, other_new).pairs


def test_unique_heading_label_survives_renamed_section_and_keeps_child(tmp_path):
    old, new = next(_projects(tmp_path, "\\section{Old name}\\label{sec:identity}\nStable paragraph.",
                              "\\section{Entirely new name}\\label{sec:identity}\nStable paragraph."))
    mapping = match_nodes(old, new)
    heading = next(node for node in old.nodes if node.review.type == "section")
    paragraph = _paragraphs(old)[0]
    assert mapping.old_to_new[heading.review.id]
    assert mapping.old_to_new[paragraph.review.id]
    assert next(pair for pair in mapping.pairs if pair.old_id == heading.review.id).reason.startswith("两侧唯一标签")


def test_child_label_anchors_rewritten_text_after_parent_heading_rename(tmp_path):
    old, new = next(_projects(tmp_path, r"\section{Old heading}\label{sec:stable}" + "\n" +
                              r"\label{p:stable} Astronomy orbit galaxies.",
                              r"\section{Entirely new heading}\label{sec:stable}" + "\n" +
                              r"\label{p:stable} Cooking bread tomatoes."))
    mapping = match_nodes(old, new)
    paragraph = _paragraphs(old)[0]
    matched = next(pair for pair in mapping.pairs if pair.old_id == paragraph.review.id)
    assert matched.reason.startswith("两侧唯一标签")
    assert compare_projects(old, new).document.summary.changes == 2

    unrelated_old, unrelated_new = next(_projects(tmp_path / "unrelated",
                                        r"\section{Astronomy}" + "\nOpening.\n\n" +
                                        r"\label{p:stable} Orbit galaxies.",
                                        r"\subsection{Cooking}" + "\nOpening.\n\n" +
                                        r"\label{p:stable} Bread tomatoes."))
    assert not match_nodes(unrelated_old, unrelated_new).pairs


def test_unrelated_paragraphs_report_low_confidence_as_remove_and_add(tmp_path):
    old, new = next(_projects(tmp_path, "Astronomy orbit galaxies.", "Cooking bread tomatoes."))
    result = compare_projects(old, new)
    assert not result.mapping.pairs
    assert [change.kind for change in result.document.changes] == ["removed", "added"]
    assert all(0 < change.matching_confidence < .45 for change in result.document.changes)
    assert all("置信度不足" in change.summary for change in result.document.changes)
    assert [diagnostic.code for diagnostic in result.document.diagnostics].count("low_confidence_match") == 2


def test_word_tokens_preserve_protected_regions_and_count_chinese_per_character():
    assert not token_edits(scan_latex("Alpha   beta\n gamma.")[0], scan_latex("Alpha beta gamma.")[0])
    assert not token_edits(scan_latex("Alpha % changed\nbeta")[0], scan_latex("Alpha % edited\nbeta")[0])
    assert not token_edits(scan_latex("\\textbf{a% note\nb}")[0], scan_latex("\\textbf{a% other\nb}")[0])
    assert not token_edits(scan_latex(r"\cite {key}")[0], scan_latex(r"\cite{key}")[0])
    for left, right in ((r"\textbf{a b}", r"\textbf{a  b}"),
                        (r"\cite{key}", r"\cite{other}"),
                        (r"\ref{sec:a}", r"\ref{sec:b}"),
                        (r"$a + b$", r"$a+b$"),
                        (r"\begin{verbatim}a b\end{verbatim}",
                         r"\begin{verbatim}a  b\end{verbatim}")):
        assert token_edits(scan_latex(left)[0], scan_latex(right)[0])
    edits = token_edits(scan_latex("Alpha beta 中文一")[0], scan_latex("Alpha gamma delta 中文二")[0])
    assert sum(t.words for edit in edits for t in edit.old) == 2
    assert sum(t.words for edit in edits for t in edit.new) == 3
    assert scan_latex(r"\textbf{Alpha 中文}")[0][0].words == 3


def test_semantic_space_boundary_and_unicode_latin_word_count(tmp_path):
    for left, right in ((r"$x$ y", r"$x$y"),
                        (r"\mbox{a} b", r"\mbox{a}b"),
                        (r"hello \textbf{x}", r"hello\textbf{x}")):
        edits = token_edits(scan_latex(left)[0], scan_latex(right)[0])
        assert len(edits) == 1
        assert any(token.kind == "space" for token in (*edits[0].old, *edits[0].new))
    assert not token_edits(scan_latex(r"\LaTeX  text")[0], scan_latex(r"\LaTeX text")[0])
    assert not token_edits(scan_latex("a  b\nc")[0], scan_latex("a b c")[0])
    old, new = next(_projects(tmp_path, r"$x$ y", r"$x$y"))
    result = compare_projects(old, new)
    assert result.document.summary.changes == 1
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (0, 0)
    assert "␠" in result.document.changes[0].details[0].old_text

    polish = "Zażółć gęślą jaźń"
    for text in (polish, unicodedata.normalize("NFD", polish)):
        assert [token.text for token in scan_latex(text)[0] if token.words] == text.split()
        assert sum(token.words for token in scan_latex(text)[0]) == 3
    before, after = next(_projects(tmp_path / "latin", "Zażółć gęślą jaźń.", "Zażółć nową jaźń."))
    counted = compare_projects(before, after).document.summary
    assert (counted.added_words, counted.removed_words) == (1, 1)


def test_nonbreaking_space_before_reference_changes_format_without_adding_word(tmp_path):
    old, new = next(_projects(tmp_path, r"See Fig.\ref{fig:one}.", r"See Fig.~\ref{fig:one}."))
    result = compare_projects(old, new)
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"text": 1}
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (0, 0)
    assert result.document.changes[0].details[0].new_text == "~"
    assert token_edits(scan_latex(r"Fig. \ref{fig:one}")[0],
                       scan_latex(r"Fig.~\ref{fig:one}")[0])


def test_paragraph_whitespace_chinese_counts_and_verbatim_content(tmp_path):
    old, new = next(_projects(tmp_path, "Alpha  beta\n中文一。\n\n\\begin{verbatim}a b\\end{verbatim}",
                              "Alpha beta 中文二。\n\n\\begin{verbatim}a  b\\end{verbatim}"))
    result = compare_projects(old, new)
    assert result.document.summary.changes == 2
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (1, 1)
    assert any(change.node_type in {"environment", "fallback"} for change in result.document.changes)
    assert all(change.source_old and change.source_new for change in result.document.changes)


def test_top_level_unknown_environment_content_is_not_silently_skipped(tmp_path):
    for side, abstract in (("old", "Old abstract claim."), ("new", "Revised abstract claim.")):
        root = tmp_path / side
        root.mkdir()
        (root / "main.tex").write_text(
            "\\begin{document}\\begin{frontmatter}\\begin{abstract}" + abstract +
            "\\end{abstract}\\end{frontmatter}\n"
            "\\section{Body}Stable body.\\end{document}\n")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        result = compare_projects(parse_project(pair.old.expand()), parse_project(pair.new.expand()))
    assert result.document.summary.changes == 1
    change = result.document.changes[0]
    assert change.node_type == "fallback" and "text" in change.categories
    assert change.source_old.file == change.source_new.file == "main.tex"
    assert any(detail.old_text == "Old" and detail.new_text == "Revised" for detail in change.details)


def test_comment_switch_inline_standalone_and_escaped_percent(tmp_path):
    old, new = next(_projects(tmp_path, "Value \\% literal. % inline old\n\n% standalone old\n",
                              "Value \\% literal. % inline new\n\n% standalone new\n"))
    default = compare_projects(old, new)
    assert default.document.summary.changes == 0
    enabled = compare_projects(old, new, review_comments=True)
    assert enabled.document.summary.changes == 2
    assert enabled.document.summary.category_hits == {"comment": 2}
    assert (enabled.document.summary.added_words, enabled.document.summary.removed_words) == (0, 0)
    assert all(change.node_type == "comment" for change in enabled.document.changes)
    assert len(scan_latex(r"Escaped \% stays; real % comment")[1]) == 1
    assert len(scan_latex(r"Double slash \\% comment")[1]) == 1
    assert scan_latex(r"\verb|% literal|")[1] == ()
    dumps(enabled.document)


def test_comment_change_is_independent_of_text_change(tmp_path):
    old, new = next(_projects(tmp_path, "Alpha beta. % note one", "Alpha gamma. % note two"))
    result = compare_projects(old, new, review_comments=True)
    assert result.document.summary.changes == 2
    assert result.document.summary.category_hits == {"comment": 1, "text": 1}
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (1, 1)


def test_text_and_citation_details_share_one_primary_change(tmp_path):
    old, new = next(_projects(tmp_path, "Alpha beta \\cite{old}.", "Alpha gamma \\cite{new}."))
    result = compare_projects(old, new)
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"citation": 1, "text": 1}
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (1, 1)
    assert len(result.token_changes) == 1
    dumps(result.document)


def test_extended_citation_commands_and_two_optional_arguments(tmp_path):
    old, new = next(_projects(tmp_path, r"See \parencite[pre][post]{old-key} and \textcite{same}.",
                              r"See \parencite[pre][post]{new-key} and \textcite{same}."))
    paragraph_old = _paragraphs(old)[0]
    paragraph_new = _paragraphs(new)[0]
    assert paragraph_old.citations == ("old-key", "same")
    assert paragraph_new.citations == ("new-key", "same")
    assert any(node.review.type == "citation" and node.review.raw_latex.startswith(r"\parencite")
               for node in old.nodes)
    result = compare_projects(old, new)
    assert result.document.summary.changes == 1
    assert result.document.summary.category_hits == {"citation": 1}
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (0, 0)
    assert result.document.changes[0].details[0].category == "citation"
