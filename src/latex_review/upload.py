"""显式上传静态报告；SSH 凭据与论文源码不进入发布包。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

from .delivery import DeliveryError as UploadError, build_archive


def upload_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    if not base.is_absolute():
        raise UploadError("XDG_CONFIG_HOME 必须是绝对路径")
    return base / "latex-review" / "upload.toml"


def load_upload_config(entry: Path) -> tuple[str, str, str]:
    path = upload_config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UploadError(f"无法读取上传配置 {path}：{exc}") from exc
    host, base = data.get("host"), data.get("base_url")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
        raise UploadError("上传 host 必须是 SSH 配置别名")
    if (not isinstance(base, str) or not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?", base)):
        raise UploadError("上传 base_url 必须是 HTTPS 站点地址")
    papers = data.get("papers", [])
    if not isinstance(papers, list):
        raise UploadError("上传 papers 必须是表数组")
    matches = [item for item in papers if isinstance(item, dict) and isinstance(item.get("entry"), str)
               and Path(item["entry"]).expanduser().resolve() == entry.resolve()]
    if (len(matches) != 1 or not isinstance(matches[0].get("slug"), str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", matches[0]["slug"])):
        raise UploadError("论文入口须在 upload.toml 中唯一注册，并指定有效 slug")
    return host, base, matches[0]["slug"]


def upload_report(directory: Path, entry: Path, translation_config: dict | None = None) -> str:
    host, base, slug = load_upload_config(entry)
    try:
        with tempfile.TemporaryFile() as archive:
            release = build_archive(directory, slug, archive, translation_config)
            archive.seek(0)
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                                     "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                                     host, "publish"], stdin=archive, capture_output=True, timeout=300)
        if result.returncode:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise UploadError(f"SSH 发布失败：{message or result.returncode}")
        receipt = json.loads(result.stdout)
        if receipt.get("release") != release or receipt.get("slug") != slug:
            raise UploadError("服务器发布回执与本次报告不一致")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, UploadError):
            raise
        raise UploadError(str(exc)) from exc
    return f"{base}/{slug}/latest/report.html"
