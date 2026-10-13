"""Full private report copies, kept separate from static publication packaging."""
import hashlib
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit
import os
from pathlib import Path
import shutil

from .identity import identity_hash, paper_identity
from .local_state import read_json
from .report_rebuild import rebuild_lock


def report_identity(directory, *, root=None, entry=None):
    directory = Path(directory).expanduser().resolve()
    if not (directory / 'report.html').is_file():
        raise ValueError('报告目录必须包含report.html')
    content = read_json(directory / 'annotation-content.json')
    if content:
        original_root, original_entry = content.get('project_root'), content.get('entry')
        if not original_root or not original_entry or content.get('project_id') != identity_hash(
                {'root': str(Path(original_root).resolve()), 'entry': original_entry}):
            raise ValueError('报告论文身份无效')
        identity = paper_identity(original_root, original_entry)
        identity['report_project_id'] = content['project_id']
        if root and Path(root).resolve() != Path(identity['root']):
            raise ValueError('指定根目录与报告论文身份不符')
        if entry and paper_identity(root or original_root, entry)['paper_id'] != identity['paper_id']:
            raise ValueError('指定入口与报告论文身份不符')
        return identity
    saved = read_json(directory / '.local-identity.json')
    if saved:
        identity = paper_identity(saved['root'], saved['entry'])
        if saved.get('paper_id') != identity['paper_id']:
            raise ValueError('报告论文身份无效')
        if root and Path(root).resolve() != Path(identity['root']):
            raise ValueError('指定根目录与报告论文身份不符')
        if entry and paper_identity(root or identity['root'], entry)['paper_id'] != identity['paper_id']:
            raise ValueError('指定入口与报告论文身份不符')
        return identity
    if entry is None:
        raise ValueError('旧报告缺少论文身份；请指定--entry和--root后重试')
    entry_path = Path(entry).expanduser()
    if root and not entry_path.is_absolute():
        entry_path = Path(root) / entry_path
    entry_path = entry_path.resolve()
    return paper_identity(root or entry_path.parent, entry_path)


def copy_report(source, destination):
    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists():
        raise ValueError('候选报告目录已存在')
    class Resources(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name not in ('src', 'href', 'data-fallback-src') or not value:
                    continue
                url = urlsplit(value)
                if url.scheme or url.netloc or not url.path:
                    continue
                relative = Path(unquote(url.path))
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('报告资源路径越界')
                if not (source / relative).is_file() or not (source / relative).resolve().is_relative_to(source):
                    raise ValueError(f'报告资源缺失或越界：{relative}')
    digest = hashlib.sha256()
    total = count = 0
    try:
        with rebuild_lock(source):
            if not (source / 'report.html').is_file():
                raise ValueError('报告入口不存在')
            Resources().feed((source / 'report.html').read_text(encoding='utf-8'))
            destination.mkdir(parents=True, mode=0o700)
            for folder, dirs, names in os.walk(source, followlinks=False):
                folder = Path(folder)
                dirs.sort()
                for name in sorted(dirs):
                    path = folder / name
                    if path.is_symlink():
                        raise ValueError('报告副本拒绝目录符号链接')
                    (destination / path.relative_to(source)).mkdir(mode=0o700)
                for name in sorted(names):
                    path = folder / name
                    if path.is_symlink() or not path.is_file():
                        raise ValueError('报告副本拒绝非普通文件')
                    data = path.read_bytes()
                    total += len(data)
                    count += 1
                    if total > 1024**3 or count > 16384:
                        raise ValueError('报告副本超过容量限制')
                    relative = path.relative_to(source).as_posix()
                    digest.update(relative.encode() + b'\0' + hashlib.sha256(data).digest())
                    (destination / relative).write_bytes(data)
            if not (destination / 'report.html').is_file():
                raise ValueError('候选报告缺少入口')
        return digest.hexdigest()
    except BaseException:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def replace_report(stage, output):
    """Swap a completed generation on the output filesystem, with rollback."""
    import uuid
    stage, output = Path(stage), Path(output)
    backup = output.parent / ('.' + output.name + '.before-' + uuid.uuid4().hex)
    had_output = output.exists()
    if had_output:
        output.replace(backup)
    try:
        stage.replace(output)
    except BaseException:
        if had_output:
            backup.replace(output)
        raise
    if had_output:
        shutil.rmtree(backup)
