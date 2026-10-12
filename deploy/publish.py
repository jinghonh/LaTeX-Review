#!/usr/bin/env python3
"""受限 SSH 发布入口。stdin 为未压缩 tar；所有写入限制在站点目录。"""
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

from portal import render_portal

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 4096
RELEASE_RE = r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}"


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.chmod(0o644)
    os.replace(temporary, path)


def write_portals(root, papers, catalog):
    for slug in papers:
        atomic_text(root / 'site' / slug / 'index.html', render_portal(papers, catalog, slug))
    atomic_text(root / 'site' / 'index.html', render_portal(papers, catalog))


def resource_path(name):
    if not isinstance(name, str):
        return False
    path = PurePosixPath(name)
    return (len(path.parts) > 1 and path.parts[0] in {'assets', 'previews', 'pages'}
            and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf'}
            and not path.is_absolute() and '..' not in path.parts and '\\' not in name
            and path.as_posix() == name)


def file_hash(path):
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest_id(manifest):
    return sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_manifest(root, manifest):
    papers = json.loads((root / 'papers.json').read_text())
    if (not isinstance(manifest, dict) or set(manifest) != {'protocol', 'slug', 'report_size', 'resources'}
            or type(manifest['protocol']) is not int or manifest['protocol'] != 1
            or not isinstance(manifest['slug'], str) or manifest['slug'] not in papers
            or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', manifest['slug'])
            or type(manifest['report_size']) is not int or manifest['report_size'] < 0
            or not isinstance(manifest['resources'], dict)):
        raise ValueError('资源清单无效')
    total = manifest['report_size']
    by_hash = {}
    for name, item in manifest['resources'].items():
        if (not resource_path(name) or not isinstance(item, dict) or set(item) != {'sha256', 'size'}
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or type(item['size']) is not int or item['size'] < 0):
            raise ValueError('资源清单项无效')
        if item['sha256'] in by_hash and by_hash[item['sha256']] != item['size']:
            raise ValueError('同一哈希的资源大小不一致')
        by_hash[item['sha256']] = item['size']
        total += item['size']
    if len(manifest['resources']) + 2 > MAX_FILES or total > MAX_BYTES:
        raise ValueError('完整发布内容超出大小或文件数量上限')
    return total


def cache_directory(root, slug):
    cache = root / 'resources' / slug
    if any(p.is_symlink() for p in (root / 'resources', cache)):
        raise ValueError('资源缓存目录不可含链接')
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def cached_resource(cache, digest, size):
    path = cache / digest
    return (not path.is_symlink() and path.is_file() and path.stat().st_size == size
            and file_hash(path) == digest)


def collect_resources(cache):
    for path in cache.iterdir():
        if not path.is_symlink() and path.is_file() and path.stat().st_nlink == 1:
            path.unlink()


def query_resources(root, manifest):
    root = Path(root)
    with (root / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        validate_manifest(root, manifest)
        cache = cache_directory(root, manifest['slug'])
        missing = sorted({item['sha256'] for item in manifest['resources'].values()
                          if not cached_resource(cache, item['sha256'], item['size'])})
        return {'protocol': 1, 'slug': manifest['slug'], 'manifest_id': manifest_id(manifest), 'missing': missing}


class MissingResources(ValueError):
    def __init__(self, slug, identifier, missing):
        self.receipt = {'protocol': 1, 'error': 'missing_resources', 'slug': slug,
                        'manifest_id': identifier, 'missing': sorted(missing)}
        super().__init__('发布前发现缺少资源，请补传')


def share_resource(cache, path, digest):
    entry = cache / digest
    if entry.exists() or entry.is_symlink():
        if entry.is_symlink() or not entry.is_file() or file_hash(entry) != digest:
            raise ValueError('资源缓存损坏')
    else:
        os.link(path, entry)
    if os.path.samefile(entry, path):
        return
    temporary = path.with_name(path.name + '.resource-link')
    try:
        os.link(entry, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def receive(root, stream, *, incremental=False):
    root = Path(root)
    papers = json.loads((root / 'papers.json').read_text())
    with (root / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _receive_locked(root, stream, papers, incremental)
        finally:
            # Stage cleanup has completed before collecting unreferenced cache files.
            for slug in papers:
                candidate = root / 'resources' / slug
                if candidate.is_dir() and not candidate.is_symlink():
                    collect_resources(candidate)


def _receive_locked(root, stream, papers, incremental):
    with tempfile.TemporaryDirectory(prefix='stage-', dir=root) as temporary:
        stage = Path(temporary)
        seen, total = set(), 0
        with tarfile.open(fileobj=stream, mode='r|') as archive:
            for member in archive:
                allowed = member.name in {'report.html', 'release.json'} or resource_path(member.name)
                if not member.isfile() or member.size < 0 or not allowed or member.name in seen:
                    raise ValueError('发布包含非法文件或路径')
                seen.add(member.name)
                total += member.size
                if total > MAX_BYTES or len(seen) > MAX_FILES:
                    raise ValueError('发布包超出大小或文件数量上限')
                destination = stage / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('wb') as output:
                    shutil.copyfileobj(archive.extractfile(member), output)
                destination.chmod(0o644)
        if not {'report.html', 'release.json'} <= seen:
            raise ValueError('发布包缺少报告或元数据')
        metadata_path = stage / 'release.json'
        metadata_size = metadata_path.stat().st_size
        metadata = json.loads(metadata_path.read_text())
        slug, release = metadata.get('slug'), metadata.get('release')
        if (not isinstance(slug, str) or slug not in papers or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug)
                or not isinstance(release, str) or not re.fullmatch(RELEASE_RE, release)):
            raise ValueError('论文或版本标识无效')
        comparison = metadata.get('comparison')
        if not isinstance(comparison, str) or len(comparison) > 160:
            raise ValueError('比较基准无效')
        resource_files = [stage / name for name in seen if resource_path(name)]
        received_bytes = sum(p.stat().st_size for p in resource_files)
        cache = cache_directory(root, slug)
        if incremental:
            manifest = metadata.get('manifest')
            complete_size = validate_manifest(root, manifest)
            identifier = manifest_id(manifest)
            if (metadata.get('protocol') != 1 or manifest['slug'] != slug
                    or metadata.get('manifest_id') != identifier
                    or manifest['report_size'] != (stage / 'report.html').stat().st_size
                    or complete_size + metadata_size > MAX_BYTES):
                raise ValueError('增量发布清单与报告不一致或超限')
            resources = manifest['resources']
            uploaded = {}
            for path in resource_files:
                name = path.relative_to(stage).as_posix()
                item = resources.get(name)
                if not item or path.stat().st_size != item['size'] or file_hash(path) != item['sha256']:
                    raise ValueError('上传资源与内容哈希不一致')
                uploaded[item['sha256']] = path
            missing = {item['sha256'] for item in resources.values()
                       if item['sha256'] not in uploaded and not cached_resource(cache, item['sha256'], item['size'])}
            if missing:
                raise MissingResources(slug, identifier, missing)
            for name, item in resources.items():
                path = stage / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    source = uploaded.get(item['sha256'], cache / item['sha256'])
                    os.link(source, path)
                share_resource(cache, path, item['sha256'])
        else:
            resources = {p.relative_to(stage).as_posix(): {'sha256': file_hash(p), 'size': p.stat().st_size}
                         for p in resource_files}
            for path in resource_files:
                share_resource(cache, path, resources[path.relative_to(stage).as_posix()]['sha256'])
        metadata = {'slug': slug, 'release': release, 'comparison': comparison,
                    'published_at': datetime.now(timezone.utc).isoformat()}
        metadata_path.unlink()
        paper = root / 'site' / slug
        releases = paper / 'releases'
        releases.mkdir(parents=True, exist_ok=True)
        target = releases / release
        if target.exists():
            raise ValueError('该版本已经发布')
        catalog_path = root / 'catalog.json'
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
        previous = catalog.get(slug, [])
        os.rename(stage, target)
        target.chmod(0o755)
        link = paper / '.latest.tmp'
        link.unlink(missing_ok=True)
        link.symlink_to('releases/' + release, target_is_directory=True)
        os.replace(link, paper / 'latest')
        catalog[slug] = [metadata, *previous][:10]
        atomic_text(catalog_path, json.dumps(catalog, ensure_ascii=False))
        write_portals(root, papers, catalog)
        for item in previous[9:]:
            old = releases / item['release']
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
        metadata.update(resource_files_received=len(resource_files),
                        resource_bytes_received=received_bytes,
                        resource_files_reused=len(resources) - len(resource_files))
        return metadata

def deduplicate_existing(root):
    """Administrative migration: preserve every historical page and resource URL."""
    root = Path(root)
    papers = json.loads((root / 'papers.json').read_text())
    linked = 0
    with (root / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for slug in papers:
            cache = cache_directory(root, slug)
            releases = root / 'site' / slug / 'releases'
            if not releases.is_dir():
                continue
            for release in releases.iterdir():
                if release.is_symlink() or not release.is_dir():
                    continue
                for path in release.rglob('*'):
                    if not resource_path(path.relative_to(release).as_posix()):
                        continue
                    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                        raise ValueError('历史资源不可含符号链接')
                    if path.is_file():
                        share_resource(cache, path, file_hash(path))
                        linked += 1
            collect_resources(cache)
    return {'resource_files_checked': linked}


def main():
    os.umask(0o022)
    root = Path(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == '--deduplicate':
        print(json.dumps(deduplicate_existing(root)))
        return 0
    if len(sys.argv) > 2 and sys.argv[2] == '--init':
        papers = json.loads((root / 'papers.json').read_text())
        catalog = json.loads((root / 'catalog.json').read_text()) if (root / 'catalog.json').exists() else {}
        write_portals(root, papers, catalog)
        return 0
    command = os.environ.get('SSH_ORIGINAL_COMMAND')
    if command not in {'publish', 'resource-query-v1', 'publish-incremental-v1'}:
        print('此账号仅允许发布报告', file=sys.stderr)
        return 1
    try:
        if command == 'resource-query-v1':
            raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('资源查询清单超限')
            result = query_resources(root, json.loads(raw))
        else:
            result = receive(root, sys.stdin.buffer, incremental=command == 'publish-incremental-v1')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MissingResources as exc:
        print(json.dumps(exc.receipt, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(f'发布失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
