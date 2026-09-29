"""受信任工作流中的拉取请求摘要发布器。只读取 JSON 数据，不执行报告内容。"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile


MARKER = "<!-- latex-review-ci:v1 workflow=latex-review-pr -->"
STATUS_ARTIFACT = "latex-review-status"
REPORT_ARTIFACT = "latex-review-report"
CATEGORY_LABELS = {"text": "正文", "equation": "公式", "figure": "图", "table": "表格",
                   "citation": "引用", "comment": "注释", "move": "移动"}


class ApiError(Exception):
    def __init__(self, status: int):
        self.status = status


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        redirected = super().redirect_request(request, response, code, message, headers, newurl)
        if redirected is not None and urlsplit(newurl).netloc != urlsplit(request.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHubAPI:
    def __init__(self, token: str, repository: str):
        self.token = token
        self.repository = repository

    def request(self, method: str, path: str, body: dict | None = None) -> dict | list | bytes:
        url = f"https://api.github.com/repos/{self.repository}/{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28",
                                   "Content-Type": "application/json"})
        try:
            with build_opener(_SafeRedirect()).open(request, timeout=20) as response:
                payload = response.read()
                if path.endswith("/zip"):
                    return payload
                return json.loads(payload) if payload else {}
        except HTTPError as exc:
            raise ApiError(exc.code) from exc


def _artifacts(api: GitHubAPI, run_id: int) -> dict[str, dict]:
    artifacts: dict[str, dict] = {}
    for page in range(1, 11):
        data = api.request("GET", f"actions/runs/{run_id}/artifacts?per_page=100&page={page}")
        batch = data.get("artifacts", []) if isinstance(data, dict) else []
        for item in batch:
            if item.get("name") in (STATUS_ARTIFACT, REPORT_ARTIFACT) and not item.get("expired"):
                artifacts[item["name"]] = item
        if len(batch) < 100:
            break
    return artifacts


def _status(api: GitHubAPI, artifact: dict) -> dict | None:
    data = api.request("GET", f"actions/artifacts/{artifact['id']}/zip")
    if not isinstance(data, bytes) or len(data) > 1_000_000:
        return None
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            files = archive.namelist()
            if files != ["status.json"] or archive.getinfo("status.json").file_size > 20_000:
                return None
            status = json.loads(archive.read("status.json"))
    except (BadZipFile, KeyError, ValueError, OSError):
        return None
    if not isinstance(status, dict) or status.get("schema_version") != 1:
        return None
    if type(status.get("exit_code")) is not int or status["exit_code"] not in (0, 2, 4, 8, 64) or type(status.get("report_available")) is not bool:
        return None
    summary = status.get("summary")
    if status["report_available"]:
        if status["exit_code"] not in (0, 2) or not isinstance(summary, dict):
            return None
        if not all(type(summary.get(key)) is int and 0 <= summary[key] <= 1_000_000_000
                   for key in ("changes", "added_words", "removed_words")):
            return None
        hits = summary.get("category_hits")
        if not isinstance(hits, dict) or any(key not in CATEGORY_LABELS or type(value) is not int
                                              or value < 0 or value > 1_000_000_000
                                              for key, value in hits.items()):
            return None
    elif summary is not None:
        return None
    return status


def _body(status: dict, url: str) -> str:
    code = status["exit_code"]
    meaning = {0: "完整报告已生成", 2: "降级报告已生成，请检查诊断", 4: "比较来源无法读取",
               8: "审阅程序内部错误", 64: "参数或配置无效"}[code]
    if not status["report_available"] and code in (0, 2):
        meaning = "报告产物不完整，请检查工作流日志"
    lines = [MARKER, "### LaTeX 审阅摘要", "", f"审阅状态：`{code}`，{meaning}。"]
    summary = status["summary"]
    if summary:
        lines += [f"主变更：{summary['changes']}；正文新增词数：{summary['added_words']}；正文删除词数：{summary['removed_words']}。"]
        hits = summary["category_hits"]
        if hits:
            lines.append("分类命中：" + "；".join(f"{CATEGORY_LABELS[key]} {hits[key]}" for key in CATEGORY_LABELS if key in hits) + "。")
        lines.append("分类命中可重叠；详细变更与诊断请下载产物查看。")
    else:
        lines.append("本次未产生可用的完整报告；请查看工作流日志与保留的失败诊断。")
    lines += [f"[下载审阅产物]({url})", "", "报告可能包含论文原文；请遵守仓库的产物访问设置。"]
    return "\n".join(lines)


def publish(event: dict, api: GitHubAPI) -> str:
    run = event.get("workflow_run", {})
    repository = event.get("repository", {}).get("full_name")
    if repository != api.repository or run.get("name") != "LaTeX Review PR":
        return "事件仓库或工作流不匹配，跳过评论"
    if run.get("head_repository", {}).get("full_name") != repository:
        return "分叉请求保持只读，跳过评论"
    pulls = run.get("pull_requests", [])
    if len(pulls) != 1 or type(pulls[0].get("number")) is not int:
        return "无法唯一确定拉取请求，跳过评论"
    number = pulls[0]["number"]
    run_id = run.get("id")
    if type(run_id) is not int:
        return "运行标识无效，跳过评论"
    artifacts = _artifacts(api, run_id)
    if STATUS_ARTIFACT not in artifacts or REPORT_ARTIFACT not in artifacts:
        return "状态或报告产物缺失，跳过评论"
    status = _status(api, artifacts[STATUS_ARTIFACT])
    if status is None:
        return "状态产物无效，跳过评论"
    url = f"https://github.com/{repository}/actions/runs/{run_id}/artifacts/{artifacts[REPORT_ARTIFACT]['id']}"
    body = _body(status, url)
    existing = None
    for page in range(1, 21):
        comments = api.request("GET", f"issues/{number}/comments?per_page=100&page={page}")
        if not isinstance(comments, list):
            return "评论列表无效，跳过评论"
        for item in comments:
            if MARKER in (item.get("body") or "") and item.get("user", {}).get("login") == "github-actions[bot]":
                existing = item["id"]
        if len(comments) < 100:
            break
    if existing is None:
        api.request("POST", f"issues/{number}/comments", {"body": body})
        return "已新增审阅摘要评论"
    api.request("PATCH", f"issues/comments/{existing}", {"body": body})
    return "已更新审阅摘要评论"


def main() -> int:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        api = GitHubAPI(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
        print(publish(event, api))
    except ApiError as exc:
        if exc.status in (401, 403, 404):
            print(f"评论权限不可用或资源不可见（HTTP {exc.status}），跳过评论；不会提权重试")
            return 0
        print(f"GitHub API 失败：HTTP {exc.status}", file=sys.stderr)
        return 1
    except (KeyError, ValueError, URLError, OSError) as exc:
        print(f"评论发布未完成：{type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
