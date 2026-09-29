"""#43：句子明细、侧别和报告锚点的可提交最小样例。"""

from pathlib import Path
import json

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.text_diff import split_sentences


def _compare(tmp_path: Path, old_text: str, new_text: str):
    for side, body in (("old", old_text), ("new", new_text)):
        folder = tmp_path / side
        folder.mkdir()
        (folder / "main.tex").write_text("\\begin{document}\n" + body + "\n\\end{document}\n")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = compare_projects(old, new)
        report = write_report(old, new, result, tmp_path / "report", pdf_converter="")
    return result, report


def test_protected_sentence_boundaries():
    text = (r"Dr. Smith measured 3.14 in Fig. 2. "
            r"The value $x_{i.1}$ agrees with \cite[see Eq. 3]{paper.key}. "
            r"A final sentence!")
    sentences = split_sentences(text)
    assert [item.text for item in sentences] == [
        "Dr. Smith measured 3.14 in Fig. 2.",
        r"The value $x_{i.1}$ agrees with \cite[see Eq. 3]{paper.key}.",
        "A final sentence!",
    ]
    assert all(text[item.start:item.end] == item.text for item in sentences)
    assert [item.text for item in split_sentences("J. Smith agrees with Lee et al. The result holds.")] == [
        "J. Smith agrees with Lee et al.", "The result holds."]
    assert len(split_sentences(r"Before. \begin{verbatim}x. y.\end{verbatim} After.")) == 2


def test_one_word_change_colors_whole_sentence_and_preserves_unchanged_sentence(tmp_path):
    result, report = _compare(tmp_path, "A stable sentence. We found a small gain. Another stable sentence.",
                              "A stable sentence. We found a large gain. Another stable sentence.")
    assert result.document.summary.changes == 1
    change = result.document.changes[0]
    sentence = next(detail for detail in change.details if detail.old_sentences)
    assert sentence.old_text == "We found a small gain."
    assert sentence.new_text == "We found a large gain."
    assert sentence.old_sentences == sentence.new_sentences == (2,)
    html = report.html.read_text()
    assert 'class="review-sentence sentence-changed"' in html
    assert html.count('class="review-sentence sentence-changed"') == 2
    assert f'id="old-{change.old_node_id}-sentence-2"' in html
    assert f'id="new-{change.new_node_id}-sentence-2"' in html
    assert f'data-old-sentences="{change.old_node_id}-sentence-2"' in html
    assert "修改前：We found a small gain." in html
    assert "修改后：We found a large gain." in html
    data = json.loads(report.diff_json.read_text())
    assert data["changes"][0]["details"][0]["old_sentences"] == [2]


def test_sentence_insert_delete_and_split_merge_keep_all_text(tmp_path):
    result, _ = _compare(tmp_path, "Keep this. A long thought continues here. End here.",
                         "Keep this. A long thought. It continues here. End here.")
    change = result.document.changes[0]
    detail = next(detail for detail in change.details if detail.old_sentences)
    assert detail.old_text == "A long thought continues here."
    assert detail.new_text == "A long thought. It continues here."
    assert detail.old_sentences == (2,) and detail.new_sentences == (2, 3)

    # 新增句子不借用旧侧段落位置；独立结构仍只计一项主变更。
    other = tmp_path / "other"
    other.mkdir()
    inserted, report = _compare(other, "Keep this. End here.", "Keep this. Add this. End here.")
    assert inserted.document.summary.changes == 1
    detail = next(detail for detail in inserted.document.changes[0].details if detail.new_sentences)
    assert detail.kind == "added" and detail.old_sentences == () and detail.new_sentences == (2,)
    assert 'data-old=""' in report.html.read_text()


def test_unchanged_inline_math_and_citation_remain_inside_sentence(tmp_path):
    result, report = _compare(tmp_path,
                              r"First $x_{1.2}$ \cite{same} remains. We found a small gain.",
                              r"First $x_{1.2}$ \cite{same} remains. We found a large gain.")
    detail = next(detail for detail in result.document.changes[0].details if detail.old_sentences)
    assert detail.old_sentences == (2,)
    html = report.html.read_text()
    assert "sentence-1" in html and "sentence-2" in html
    assert html.count('class="review-sentence sentence-changed"') == 2


def test_comment_source_is_not_exposed_in_sentence_preview(tmp_path):
    _, report = _compare(tmp_path, "A stable sentence. % private note\nThe old claim.",
                         "A stable sentence. % private note\nThe new claim.")
    html = report.html.read_text()
    assert "private note" not in html
    assert "The old claim." in html and "The new claim." in html


@pytest.mark.parametrize("document_class", ["article", "IEEEtran"])
def test_common_document_classes_keep_sentence_anchors(tmp_path, document_class):
    for side, adjective in (("old", "small"), ("new", "large")):
        folder = tmp_path / side
        folder.mkdir()
        (folder / "main.tex").write_text(
            rf"\documentclass{{{document_class}}}\begin{{document}}\section{{Results}}"
            + rf"See Fig. 2 and $x_{{1.2}}$. We found a {adjective} gain."
            + r"\end{document}", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = compare_projects(old, new)
        report = write_report(old, new, result, tmp_path / "report", pdf_converter="")
    assert result.document.summary.changes == 1
    assert any(detail.old_sentences == detail.new_sentences == (2,)
               for change in result.document.changes for detail in change.details)
    assert report.html.read_text().count('class="review-sentence sentence-changed"') == 2
