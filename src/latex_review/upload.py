"""显式上传静态报告；SSH 凭据与论文源码不进入发布包。"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
from uuid import uuid4


class UploadError(ValueError):
    pass


MAX_BYTES = 200 * 1024 * 1024
ASSET_ROOTS = {"assets", "previews", "pages"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}


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


class _Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name not in {"src", "href"} or not value:
                continue
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path:
                continue
            path = PurePosixPath(unquote(url.path))
            if path.is_absolute() or ".." in path.parts:
                raise UploadError(f"报告资源路径越界：{value}")
            if path.parts[0] not in ASSET_ROOTS or path.suffix.lower() not in ASSET_SUFFIXES:
                raise UploadError(f"报告引用了不支持的资源：{value}")
            self.paths.add(path.as_posix())


def publication_html(directory: Path, translation_config: dict | None = None) -> str:
    entry = directory / "report.html"
    if entry.is_symlink():
        raise UploadError("报告入口不可为符号链接")
    html = entry.read_text(encoding="utf-8")
    if translation_config and (directory / "translation-units.json").is_file():
        from .translation import TranslationConfig, TranslationManager
        manifest = directory / "translation-units.json"
        if manifest.is_symlink():
            raise UploadError("翻译清单不可为符号链接")
        manager = TranslationManager(json.loads(manifest.read_text(encoding="utf-8")),
                                     TranslationConfig.from_mapping(translation_config),
                                     directory.parent / "translation-cache")
        try:
            results = manager.snapshot()["results"]
        finally:
            manager.close()
        if results:
            # 只读取已有缓存；不会启动翻译请求。
            existing = re.search(r'<script type="application/json" id="translation-results">(.*?)</script>', html, re.S)
            embedded = json.loads(existing[1]) if existing else {}
            payload = json.dumps({**embedded, **results}, ensure_ascii=False).replace("<", "\\u003c")
            html = re.sub(r'(<script type="application/json" id="translation-results">).*?(</script>)',
                          lambda m: m[1] + payload + m[2], html, flags=re.S)
    return html


def _comparison(source: dict) -> str:
    kind, identifier = source.get("kind"), source.get("identifier", "")
    if kind == "git" and re.fullmatch(r"[0-9a-f]{40,64}", identifier):
        return identifier[:12]
    return {"worktree": "工作区", "directory": "目录版本", "file": "文件版本"}.get(kind, "未知版本")


def build_archive(directory: Path, slug: str, stream, translation_config: dict | None = None) -> str:
    directory = directory.resolve()
    html = publication_html(directory, translation_config)
    parser = _Resources()
    parser.feed(html)
    release = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
    diff = json.loads((directory / "diff.json").read_text(encoding="utf-8"))
    metadata = {"slug": slug, "release": release,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "comparison": f'{_comparison(diff.get("old", {}))} → {_comparison(diff.get("new", {}))}'}
    blobs = {"report.html": html.encode("utf-8"),
             "release.json": json.dumps(metadata, ensure_ascii=False).encode("utf-8")}
    total = sum(map(len, blobs.values()))
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, blob in blobs.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(blob), 0o644
            archive.addfile(info, io.BytesIO(blob))
        for name in sorted(parser.paths):
            path = directory / name
            if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
                raise UploadError(f"资源不可读取或含符号链接：{name}")
            size = path.stat().st_size
            total += size
            if total > MAX_BYTES:
                raise UploadError("发布资源超过 200 MiB 上限")
            info = tarfile.TarInfo(name)
            info.size, info.mode = size, 0o644
            with path.open("rb") as asset:
                archive.addfile(info, asset)
    if total > MAX_BYTES:
        raise UploadError("发布资源超过 200 MiB 上限")
    return release


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
