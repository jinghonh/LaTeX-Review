import io
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from latex_review.ci import _summary
from latex_review.ci_comment import ApiError, MARKER, _body, publish


def _zip_status(status):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("status.json", json.dumps(status))
    return buffer.getvalue()


class FakeAPI:
    repository = "owner/paper"

    def __init__(self, *, comments=(), report=True, denied=False):
        self.comments = list(comments)
        self.report = report
        self.denied = denied
        self.writes = []

    def request(self, method, path, body=None):
        if path.startswith("actions/runs/"):
            artifacts = [{"name": "latex-review-status", "id": 11, "expired": False}]
            if self.report:
                artifacts.append({"name": "latex-review-report", "id": 12, "expired": False})
            return {"artifacts": artifacts}
        if path == "actions/artifacts/11/zip":
            return _zip_status({"schema_version": 1, "exit_code": 2, "report_available": True,
                                "summary": {"changes": 3, "added_words": 7, "removed_words": 2,
                                            "category_hits": {"text": 2, "citation": 1}}})
        if method == "GET" and path.startswith("issues/23/comments"):
            return self.comments
        if self.denied:
            raise ApiError(403)
        self.writes.append((method, path, body))
        return {}


def _event(fork=False):
    return {"repository": {"full_name": "owner/paper"},
            "workflow_run": {"name": "LaTeX Review PR", "id": 42,
                             "head_repository": {"full_name": "outsider/paper" if fork else "owner/paper"},
                             "pull_requests": [{"number": 23}]}}


def test_comment_create_update_and_no_paper_text():
    api = FakeAPI()
    assert publish(_event(), api) == "已新增审阅摘要评论"
    method, path, payload = api.writes[0]
    assert (method, path) == ("POST", "issues/23/comments")
    assert "主变更：3" in payload["body"] and "降级报告" in payload["body"]
    assert "/actions/runs/42/artifacts/12" in payload["body"]
    assert "main.tex" not in payload["body"]
    api.comments = [{"id": 99, "body": payload["body"], "user": {"login": "github-actions[bot]"}}]
    assert publish(_event(), api) == "已更新审阅摘要评论"
    assert api.writes[-1][:2] == ("PATCH", "issues/comments/99")


def test_comment_fork_missing_artifact_and_foreign_marker():
    api = FakeAPI()
    assert "分叉" in publish(_event(fork=True), api)
    assert not api.writes
    api = FakeAPI(report=False)
    assert "产物缺失" in publish(_event(), api)
    assert not api.writes
    api = FakeAPI(comments=[{"id": 7, "body": MARKER, "user": {"login": "other-bot"}}])
    publish(_event(), api)
    assert api.writes[0][0] == "POST"


@pytest.mark.parametrize("code,phrase", [(0, "完整报告"), (2, "降级报告"),
                                        (4, "来源无法读取"), (8, "内部错误")])
def test_comment_explains_exit_code(code, phrase):
    available = code in (0, 2)
    summary = {"changes": 0, "added_words": 0, "removed_words": 0, "category_hits": {}} if available else None
    body = _body({"exit_code": code, "report_available": available, "summary": summary},
                 "https://github.com/owner/paper/actions/runs/1/artifacts/2")
    assert phrase in body and MARKER in body
    assert "/private/" not in body


def test_permission_denial_is_exposed_to_main_without_retry(monkeypatch, tmp_path, capsys):
    from latex_review import ci_comment
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(_event()), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/paper")
    api = FakeAPI(denied=True)
    monkeypatch.setattr(ci_comment, "GitHubAPI", lambda *_: api)
    assert ci_comment.main() == 0
    assert "权限不可用" in capsys.readouterr().out
    assert len(api.writes) == 0


def test_ci_summary_rejects_paper_content(tmp_path):
    (tmp_path / "diff.json").write_text(json.dumps({"summary": {"changes": 1,
        "added_words": 2, "removed_words": 0, "category_hits": {"text": 1},
        "paper_text": "private"}}), encoding="utf-8")
    assert _summary(tmp_path) == {"changes": 1, "added_words": 2, "removed_words": 0,
                                     "category_hits": {"text": 1}}


@pytest.mark.parametrize("code,expected_report", [(2, True), (8, False)])
def test_ci_records_degradation_and_internal_error(tmp_path, monkeypatch, code, expected_report):
    from latex_review import ci

    def fake_run(command, **kwargs):
        report = tmp_path / "report"
        report.mkdir()
        if code == 2:
            (report / "report.html").write_text("<html></html>", encoding="utf-8")
            (report / "diagnostics.json").write_text("{}", encoding="utf-8")
            (report / "diff.json").write_text(json.dumps({"summary": {"changes": 1,
                "added_words": 0, "removed_words": 0, "category_hits": {"text": 1}}}), encoding="utf-8")
        return subprocess.CompletedProcess(command, code, stdout="/private/paper/report.html", stderr="/private/paper")

    monkeypatch.setattr(ci.subprocess, "run", fake_run)
    status = tmp_path / "status.json"
    assert ci.main(["--paper-dir", str(tmp_path), "--entry", "main.tex", "--old", "a", "--new", "b",
                    "--report-dir", str(tmp_path / "report"), "--status-file", str(status)]) == 0
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["exit_code"] == code and data["report_available"] is expected_report
    assert "/private/paper" not in status.read_text(encoding="utf-8")


def test_ci_real_report_and_source_failure(tmp_path):
    paper = tmp_path / "paper"
    paper.mkdir()
    subprocess.run(["git", "init", "-q", str(paper)], check=True)
    (paper / "main.tex").write_text("\\section{A}\nOld words.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(paper), "add", "main.tex"], check=True)
    subprocess.run(["git", "-C", str(paper), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "old"], check=True)
    old = subprocess.check_output(["git", "-C", str(paper), "rev-parse", "HEAD"], text=True).strip()
    (paper / "main.tex").write_text("\\section{A}\nNew words.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(paper), "add", "main.tex"], check=True)
    subprocess.run(["git", "-C", str(paper), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "new"], check=True)
    new = subprocess.check_output(["git", "-C", str(paper), "rev-parse", "HEAD"], text=True).strip()
    def run(before):
        status = tmp_path / "status" / "status.json"
        return subprocess.run([sys.executable, "-m", "latex_review.ci", "--paper-dir", str(paper),
            "--entry", "main.tex", "--old", before, "--new", new,
            "--report-dir", str(tmp_path / "report"), "--status-file", str(status)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            capture_output=True, text=True), json.loads(status.read_text(encoding="utf-8"))
    result, status = run(old)
    assert result.returncode == 0 and status["exit_code"] == 0 and status["report_available"]
    assert status["summary"]["changes"] == 1
    result, status = run("missing-revision")
    assert result.returncode == 0 and status["exit_code"] == 4 and not status["report_available"]
    assert (tmp_path / "report" / "diagnostics.json").is_file()
