"""报告交付：资源校验、译文嵌入及单文件转换。"""
from __future__ import annotations

from .version_storage import pack_versions, unpack_versions

import base64
import gzip
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
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


def embed_annotations(html: str, content: dict, directory: Path | None = None, *, include_assets: bool = True, compressed: bool = False) -> str:
    """Embed a portable manifest and existing backup without creating project paths."""
    from .annotations import AnnotationStore, validate_bundle
    bundle = validate_bundle(content)
    root = Path(content['project_root']) if content.get('project_root') else None
    carried = re.search(r'<script type="application/json" id="annotation-backup">(.*?)</script>', html, re.S)
    packed = re.search(r'<script type="application/json" id="annotation-backup-gzip">(.*?)</script>', html, re.S)
    if carried or packed:
        original = json.loads(carried[1]) if carried else decode_annotation_backup(json.loads(packed[1]))
        bundle = validate_bundle(content, original)
    if root is not None and root.is_dir():
        session = AnnotationStore.open(content)
        if session.stored:
            bundle = session.export()
    assets = {}
    if directory is not None and include_assets:
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
    if not compressed:
        html = re.sub(r'<script type="application/json" id="annotation-(?:current-version|backup-gzip)">.*?</script>', '', html, flags=re.S)
    values = [('annotation-content', content), ('annotation-backup', bundle), ('annotation-assets', assets)]
    if compressed:
        # Validate above, then serialize the full backup once at the delivery boundary.
        values = [('annotation-current-version', content['version_id']),
                  ('annotation-backup-gzip', encode_annotation_backup(bundle))]
        html = re.sub(r'<script type="application/json" id="annotation-(?:content|backup|assets|current-version|backup-gzip)">.*?</script>', '', html, flags=re.S)
    for identifier, value in values:
        payload = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
        tag = f'<script type="application/json" id="{identifier}">{payload}</script>'
        pattern = rf'<script type="application/json" id="{identifier}">.*?</script>'
        if re.search(pattern, html, re.S):
            html = re.sub(pattern, lambda _: tag, html, flags=re.S)
        else:
            html = html.replace('</head>', tag + '</head>', 1)
    return html


def _annotation_storage(html: str, mode: str) -> str:
    html = re.sub(r'''<meta\b(?=[^>]*\bname=["']latex-review-annotation-storage["'])[^>]*>''',
                  '', html, flags=re.I)
    tag = f'<meta name="latex-review-annotation-storage" content="{mode}">'
    return html.replace('</head>', tag + '</head>', 1)


def local_report_html(html: str) -> str:
    """本机报告从服务读取批注；仅独立交付携带备份和资源。"""
    html = re.sub(r'<script type="application/json" id="annotation-(?:content|backup|assets|current-version|backup-gzip)">.*?</script>',
                  '', html, flags=re.S)
    return _annotation_storage(html, 'disk')


def decode_annotation_backup(envelope: dict) -> dict:
    """Bound decompression before importing a carried complete backup."""
    try:
        if (set(envelope) != {'encoding', 'size', 'data'} or envelope['encoding'] not in ('gzip-base64-v1', 'gzip-shared-units-v1')
                or type(envelope['size']) is not int or not 0 <= envelope['size'] <= MAX_BYTES):
            raise ValueError('压缩备份格式无效')
        compressed = base64.b64decode(envelope['data'], validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(envelope['size'] + 1)
        if len(raw) != envelope['size']:
            raise ValueError('压缩备份大小不一致')
        return unpack_versions(json.loads(raw))
    except (ValueError, TypeError, KeyError, OSError, EOFError) as error:
        raise DeliveryError('批注备份读取失败') from error


def encode_annotation_backup(bundle: dict) -> dict:
    """Transport codec only: decoding returns the unchanged full backup contract."""
    raw = json.dumps(pack_versions(bundle), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise DeliveryError('完整批注备份超过 200 MiB 上限')
    return {'encoding': 'gzip-shared-units-v1', 'size': len(raw),
            'data': base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode('ascii')}


def shared_publication_annotations(html: str) -> str:
    """Static delivery selects current content from the complete portable backup.

    The JSON export schema stays unchanged. Old reports still carry both tags;
    the updated reader supports both layouts without a server protocol change.
    """
    pattern = r'<script type="application/json" id="annotation-{}">(.*?)</script>'
    current = re.search(pattern.format('content'), html, re.S)
    backup = re.search(pattern.format('backup'), html, re.S)
    if not current or not backup:
        return html
    content, bundle = json.loads(current[1]), json.loads(backup[1])
    if bundle['versions'].get(content['version_id']) != content:
        raise DeliveryError('发布正文与备份版本不一致')
    payload = json.dumps(bundle, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    html = re.sub(pattern.format('backup'), lambda m: m[0].replace(m[1], payload), html, flags=re.S)
    reference = json.dumps(content['version_id'])
    return re.sub(pattern.format('content'), lambda _: '<script type="application/json" id="annotation-current-version">'
                  + reference + '</script>', html, flags=re.S)


def publication_html(directory: Path, translation_config: dict | None = None, *, results: dict | None = None,
                     warnings: tuple[str, ...] = (), include_assets: bool = True, compressed_annotations: bool = False) -> str:
    """有配置时使用其有效缓存；无配置时保留已有译文。"""
    entry = directory / "report.html"
    if any(p.is_symlink() for p in (entry, *entry.parents)):
        raise DeliveryError("报告入口不可含符号链接")
    html = entry.read_text(encoding="utf-8")
    manifest = directory / "annotation-content.json"
    if manifest.is_file():
        if manifest.is_symlink():
            raise DeliveryError("批注清单不可为符号链接")
        html = embed_annotations(html, json.loads(manifest.read_text(encoding="utf-8")), directory, include_assets=include_assets, compressed=compressed_annotations)
    warnings = list(warnings)
    if translation_config is not None:
        from .translation import TranslationConfig, cache_key, read_cached_translations, rendered_result, validate_manifest
        config = TranslationConfig.from_mapping(translation_config)
        results = {}
        manifest = directory / "translation-units.json"
        try:
            if manifest.is_symlink():
                raise ValueError("翻译清单不可为符号链接")
            units = validate_manifest(json.loads(manifest.read_text(encoding="utf-8")))
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
    return _annotation_storage(html, 'browser')


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


@dataclass(frozen=True)
class PreparedPublication:
    metadata: dict
    html: bytes
    resources: dict[str, bytes]
    manifest: dict


def prepare_publication(directory: Path, slug: str, translation_config: dict | None = None) -> PreparedPublication:
    from .report_rebuild import rebuild_lock
    with rebuild_lock(directory):
        return _prepare_publication(directory, slug, translation_config)


def _prepare_publication(directory: Path, slug: str, translation_config: dict | None = None) -> PreparedPublication:
    """Freeze a publication before negotiating missing resources."""
    directory = directory.resolve()
    html = publication_html(directory, translation_config, include_assets=False, compressed_annotations=True)
    if 'id="annotation-backup-gzip"' not in html:
        html = shared_publication_annotations(html)
    resources = report_resources(html, directory)
    html = re.sub(r'<script type="application/json" id="annotation-assets">.*?</script>', '', html, flags=re.S)
    from .annotations._suggestions import delivery_script
    script = delivery_script()
    html = re.sub(r'<script>/\* Portable annotation contract.*?</script>',
                  lambda _: '<script>' + script + '</script>', html, flags=re.S)
    html = html.replace('</head>', '<meta name="latex-review-publication" content="static"><style>#translation-export{display:none!important}</style></head>', 1)
    content = html.encode('utf-8')
    blobs = {name: path.read_bytes() for name, path in resources.items()}
    manifest = {'protocol': 1, 'slug': slug, 'report_size': len(content),
                'resources': {name: {'sha256': sha256(blob).hexdigest(), 'size': len(blob)}
                              for name, blob in blobs.items()}}
    diff = json.loads((directory / 'diff.json').read_text(encoding='utf-8'))
    metadata = {'slug': slug, 'release': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:12],
                'published_at': datetime.now(timezone.utc).isoformat(),
                'comparison': f'{_comparison(diff.get("old", {}))} → {_comparison(diff.get("new", {}))}'}
    if len(blobs) + 2 > 4096 or len(content) + sum(map(len, blobs.values())) + len(json.dumps(metadata).encode()) > MAX_BYTES:
        raise DeliveryError('发布资源超过 200 MiB 或 4096 个文件上限')
    return PreparedPublication(metadata, content, blobs, manifest)


def manifest_id(manifest: dict) -> str:
    return sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def write_archive(prepared: PreparedPublication, stream, *, missing: set[str] | None = None) -> dict:
    """Full archives remain compatible; incremental archives include the complete manifest."""
    metadata = dict(prepared.metadata)
    if missing is not None:
        metadata.update(protocol=1, manifest=prepared.manifest, manifest_id=manifest_id(prepared.manifest))
    blobs = {'report.html': prepared.html, 'release.json': json.dumps(metadata, ensure_ascii=False).encode()}
    sent = set()
    for name, blob in prepared.resources.items():
        digest = prepared.manifest['resources'][name]['sha256']
        if missing is not None and (digest not in missing or digest in sent):
            continue
        blobs[name] = blob
        sent.add(digest)
    if len(prepared.html) + sum(map(len, prepared.resources.values())) + len(blobs['release.json']) > MAX_BYTES:
        raise DeliveryError('发布资源超过 200 MiB 上限')
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, blob in blobs.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(blob), 0o644
            archive.addfile(info, io.BytesIO(blob))
    return {'resource_files_sent': len(blobs) - 2,
            'resource_bytes_sent': sum(len(blob) for name, blob in blobs.items() if name not in {'report.html', 'release.json'})}


def build_archive(directory: Path, slug: str, stream, translation_config: dict | None = None) -> str:
    prepared = prepare_publication(directory, slug, translation_config)
    write_archive(prepared, stream)
    return prepared.metadata['release']
