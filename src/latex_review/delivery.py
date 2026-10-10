"""报告交付：资源校验、译文嵌入及单文件转换。"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import tarfile
from uuid import uuid4
from html import escape
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit


class DeliveryError(OSError, ValueError):
    """交付文件无法安全读取或转换。"""


ASSET_ROOTS = {"assets", "previews", "pages"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
MAX_BYTES = 200 * 1024 * 1024
_MAX_EMBEDDED_ASSET = 25 * 1024 * 1024
_MAX_EMBEDDED_TOTAL = 100 * 1024 * 1024


class _Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name not in {"src", "href", "data-fallback-src"} or not value:
                continue
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path:
                continue
            path = PurePosixPath(unquote(url.path))
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] not in ASSET_ROOTS or path.suffix.lower() not in ASSET_SUFFIXES):
                raise DeliveryError(f"报告资源路径不安全或不支持：{value}")
            self.paths[value] = path.as_posix()


def report_resources(html: str, directory: Path) -> dict[str, Path]:
    """两种交付形式使用同一资源识别与路径规则。"""
    parser = _Resources()
    parser.feed(html)
    resources = {}
    for name in parser.paths.values():
        path = directory / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise DeliveryError(f"资源不可读取或含符号链接：{name}")
        resources[name] = path
    return resources


def _embed_results(html: str, results: dict) -> str:
    payload = json.dumps(results, ensure_ascii=False).replace("<", "\\u003c")
    return re.sub(r'(<script type="application/json" id="translation-results">).*?(</script>)',
                  lambda m: m[1] + payload + m[2], html, flags=re.S)


def embed_annotations(html: str, content: dict, directory: Path | None = None) -> str:
    """Embed a portable manifest and existing backup without creating project paths."""
    from .annotations import AnnotationStore, SCHEMA_VERSION, validate_content
    validate_content(content)
    bundle = {'schema_version': SCHEMA_VERSION, 'project_id': content['project_id'],
              'revision': 0, 'versions': {}, 'threads': {}}
    root = Path(content['project_root']) if content.get('project_root') else None
    carried = re.search(r'<script type="application/json" id="annotation-backup">(.*?)</script>', html, re.S)
    if carried:
        original = json.loads(carried[1])
        verifier = AnnotationStore(root or Path.cwd(), content['project_id'])
        verifier._import(bundle, original)
        if type(original['revision']) is not int or original['revision'] < 0:
            raise DeliveryError('携带批注修订号无效')
        bundle['revision'] = original['revision']
    if root is not None and root.is_dir():
        store = AnnotationStore.for_content(content)
        if store.path.is_file():
            bundle = store.export()
    AnnotationStore(root or Path.cwd(), content['project_id'])._content(bundle, content)
    assets = {}
    if directory is not None:
        total = 0
        resources = _Resources()
        resources.feed(html)
        for name in sorted(set(resources.paths.values())):
            path = directory / name
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise DeliveryError(f'资源不可读取或含符号链接：{name}')
            if not path.is_file():
                continue  # Keep the generator's degraded preview; delivery/export still validates assets.
            size = path.stat().st_size
            if size > _MAX_EMBEDDED_ASSET or total + size > _MAX_EMBEDDED_TOTAL:
                continue  # Directory reports retain their original resource links.
            total += size
            media = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            assets[name] = f'data:{media};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
    for identifier, value in (('annotation-content', content), ('annotation-backup', bundle), ('annotation-assets', assets)):
        payload = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
        tag = f'<script type="application/json" id="{identifier}">{payload}</script>'
        pattern = rf'<script type="application/json" id="{identifier}">.*?</script>'
        if re.search(pattern, html, re.S):
            html = re.sub(pattern, lambda _: tag, html, flags=re.S)
        else:
            html = html.replace('</head>', tag + '</head>', 1)
    return html


def publication_html(directory: Path, translation_config: dict | None = None, *, results: dict | None = None,
                     warnings: tuple[str, ...] = ()) -> str:
    """有配置时使用其有效缓存；无配置时保留已有译文。"""
    entry = directory / "report.html"
    if any(p.is_symlink() for p in (entry, *entry.parents)):
        raise DeliveryError("报告入口不可含符号链接")
    html = entry.read_text(encoding="utf-8")
    manifest = directory / "annotation-content.json"
    if manifest.is_file():
        if manifest.is_symlink():
            raise DeliveryError("批注清单不可为符号链接")
        html = embed_annotations(html, json.loads(manifest.read_text(encoding="utf-8")), directory)
    warnings = list(warnings)
    if translation_config is not None:
        from .translation import RULE_VERSION, TranslationConfig, cache_key, read_cached_translations, rendered_result
        config = TranslationConfig.from_mapping(translation_config)
        results = {}
        manifest = directory / "translation-units.json"
        try:
            if manifest.is_symlink():
                raise ValueError("翻译清单不可为符号链接")
            units = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(units, list) or any(not isinstance(unit, dict)
                    or unit.get("rule_version") != RULE_VERSION for unit in units)):
                raise ValueError("翻译清单规则不兼容")
            cached, warnings = read_cached_translations(units, config, directory.parent / "translation-cache")
            results = {unit["id"]: rendered_result(unit, cached[cache_key(unit, config)])
                       for unit in units if not unit.get("error") and cache_key(unit, config) in cached}
        except (OSError, ValueError, KeyError, TypeError):
            warnings = ["翻译清单不兼容或无法读取，已跳过译文"]
    if results is not None:
        html = _embed_results(html, results)
    if warnings:
        notice = '<p role="status" class="delivery-warning">' + escape("；".join(warnings)) + '</p>'
        html = html.replace('</body>', notice + '</body>') if '</body>' in html else html + notice
    return html


def single_file_html(html: str, directory: Path) -> str:
    """校验本地资源，再将其内嵌；保留联网公式依赖。"""
    resources = report_resources(html, directory)
    embedded_total = 0
    def replace_url(match: re.Match[str]) -> str:
        nonlocal embedded_total
        attr, quote, url = match.group(1), match.group(2), match.group(3)
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match[0]
        name = PurePosixPath(unquote(parsed.path)).as_posix()
        path = resources[name]
        size = path.stat().st_size
        embedded_total += size
        if size > _MAX_EMBEDDED_ASSET or embedded_total > _MAX_EMBEDDED_TOTAL:
            raise DeliveryError(f"单文件资源超出内嵌上限：{name}")
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'{attr}={quote}data:{media};base64,{encoded}{quote}'
    return re.sub(r"\b(src|href)=([\"'])(.*?)\2", replace_url, html)


def _comparison(source: dict) -> str:
    kind, identifier = source.get("kind"), source.get("identifier", "")
    if kind == "git" and re.fullmatch(r"[0-9a-f]{40,64}", identifier):
        return identifier[:12]
    return {"worktree": "工作区", "directory": "目录版本", "file": "文件版本"}.get(kind, "未知版本")


def build_archive(directory: Path, slug: str, stream, translation_config: dict | None = None) -> str:
    directory = directory.resolve()
    try:
        html = publication_html(directory, translation_config)
        resources = report_resources(html, directory)
    except (OSError, ValueError) as exc:
        raise DeliveryError(str(exc)) from exc
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
        for name in sorted(resources):
            path = directory / name
            size = path.stat().st_size
            total += size
            if total > MAX_BYTES:
                raise DeliveryError("发布资源超过 200 MiB 上限")
            info = tarfile.TarInfo(name)
            info.size, info.mode = size, 0o644
            with path.open("rb") as asset:
                archive.addfile(info, asset)
    if total > MAX_BYTES:
        raise DeliveryError("发布资源超过 200 MiB 上限")
    return release

