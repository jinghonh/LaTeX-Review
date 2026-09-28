import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from latex_review import (
    ChangeDetail, ComparisonSource, Diagnostic, DiagnosticsDocument,
    PrimaryChange, ReviewDocument, ReviewNode, SourceLocation,
    build_summary, dumps, validate_document,
)


def representative_document():
    old = SourceLocation("sections/method.tex", 12, 12)
    new = SourceLocation("sections/method.tex", 13, 13)
    unknown = SourceLocation("sections/method.tex", None, None, confidence=0.2, uncertainty_reason="宏展开后无法确认行号")
    nodes_old = (
        ReviewNode("sec", "section", "\\section{方法}", None, ("p1",), ("方法",), old),
        ReviewNode("p1", "paragraph", "旧文\\cite{a}", "sec", (), ("方法",), old),
    )
    nodes_new = (
        ReviewNode("sec", "section", "\\section{方法}", None, ("p2", "p3"), ("方法",), new),
        ReviewNode("p2", "paragraph", "新文\\cite{b}", "sec", (), ("方法",), new),
        ReviewNode("p3", "paragraph", "新增", "sec", (), ("方法",), unknown),
    )
    changes = (
        PrimaryChange("c2", "added", "paragraph", None, "p3", None, unknown, 1.0, ("text",), (), "新增段落"),
        PrimaryChange(
            "c1", "modified", "paragraph", "p1", "p2", old, new, 0.8,
            ("text",), (ChangeDetail("d1", "citation", "modified", "a", "b", "引用变化"),), "修改段落",
        ),
    )
    return ReviewDocument(
        "main.tex", ComparisonSource("git", "HEAD"), ComparisonSource("worktree", "."),
        nodes_old, nodes_new, changes,
        (Diagnostic("uncertain_source", "warning", "新增段落行号未知", source_new=unknown),),
        build_summary(changes, added_words=2, removed_words=1),
    )


def test_schema_snapshot_and_counts():
    document = representative_document()
    serialized = dumps(document)
    assert serialized == (Path(__file__).parent / "snapshots/representative_diff.json").read_text(encoding="utf-8")
    data = json.loads(serialized)
    assert data["summary"] == {"changes": 2, "category_hits": {"citation": 1, "text": 2}, "added_words": 2, "removed_words": 1}
    assert data["changes"][0]["id"] == "c1"
    assert data["changes"][1]["source_old"] is None
    assert data["changes"][1]["source_new"]["start_line"] is None
    assert dumps(document) == serialized
    validate_document(data)
    diagnostics = DiagnosticsDocument(document.diagnostics)
    validate_document(json.loads(dumps(diagnostics)))


def test_removed_and_comment_count():
    removed = PrimaryChange("r", "removed", "paragraph", "p", None, SourceLocation("main.tex", 1, 1), None, 1.0, ("text",), (), "删除")
    comment = PrimaryChange("n", "added", "comment", None, "comment-1", None, SourceLocation("main.tex", 2, 2), 1.0, ("comment",), (), "新增注释")
    summary = build_summary((removed, comment))
    assert summary.changes == 2
    assert summary.category_hits == {"comment": 1, "text": 1}
    assert summary.added_words == summary.removed_words == 0


def test_invalid_contract_rejected():
    data = json.loads(dumps(representative_document()))
    data["summary"]["changes"] = 3
    with pytest.raises(ValueError):
        validate_document(data)
    data["summary"]["changes"] = 2
    data["changes"][1]["source_new"]["uncertainty_reason"] = None
    with pytest.raises(ValidationError):
        validate_document(data)
