#!/usr/bin/env python3
"""受限 SSH 发布入口。stdin 为未压缩 tar；所有写入限制在站点目录。"""
import fcntl
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


def receive(root, stream):
    root = Path(root)
    papers = json.loads((root / 'papers.json').read_text())
    with (root / 'publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='stage-', dir=root) as temporary:
            stage = Path(temporary)
            seen, total = set(), 0
            with tarfile.open(fileobj=stream, mode='r|') as archive:
                for member in archive:
                    path = PurePosixPath(member.name)
                    allowed = (member.name in {'report.html', 'release.json'} or
                               (len(path.parts) > 1 and path.parts[0] in {'assets', 'previews', 'pages'}
                                and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf'}))
                    if (not member.isfile() or member.size < 0 or path.is_absolute() or '..' in path.parts or '\\' in member.name
                            or path.as_posix() != member.name or not allowed or member.name in seen):
                        raise ValueError('发布包含非法文件或路径')
                    seen.add(member.name)
                    total += member.size
                    if total > MAX_BYTES or len(seen) > MAX_FILES:
                        raise ValueError('发布包超出大小或文件数量上限')
                    destination = stage / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    with destination.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(0o644)
            if not {'report.html', 'release.json'} <= seen:
                raise ValueError('发布包缺少报告或元数据')
            metadata = json.loads((stage / 'release.json').read_text())
            slug, release = metadata.get('slug'), metadata.get('release')
            if slug not in papers or not isinstance(release, str) or not re.fullmatch(RELEASE_RE, release):
                raise ValueError('论文或版本标识无效')
            comparison = metadata.get('comparison')
            if not isinstance(comparison, str) or len(comparison) > 160:
                raise ValueError('比较基准无效')
            metadata = {'slug': slug, 'release': release, 'comparison': comparison,
                        'published_at': datetime.now(timezone.utc).isoformat()}
            # 不发布内部元数据；网页仅包含时间和比较基准。
            (stage / 'release.json').unlink()
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
            return metadata


def main():
    os.umask(0o022)
    root = Path(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == '--init':
        papers = json.loads((root / 'papers.json').read_text())
        catalog = json.loads((root / 'catalog.json').read_text()) if (root / 'catalog.json').exists() else {}
        write_portals(root, papers, catalog)
        return 0
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'publish':
        print('此账号仅允许发布报告', file=sys.stderr)
        return 1
    try:
        print(json.dumps(receive(root, sys.stdin.buffer), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f'发布失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
