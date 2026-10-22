"""External project profiles keep read-only papers free of application files."""
from pathlib import Path
import os
import re
import sys
import tomllib

from .identity import identity_hash


def cache_home():
    configured = os.environ.get('XDG_CACHE_HOME')
    base = Path(configured).expanduser() if configured else (
        Path.home() / 'Library' / 'Caches' if sys.platform == 'darwin' else Path.home() / '.cache')
    if not base.is_absolute():
        raise ValueError('应用缓存目录必须是绝对路径')
    return base / 'latex-review' / 'projects'


def directory(root):
    return cache_home() / identity_hash(str(Path(root).resolve()))


def load(root, entry=None):
    path = directory(root) / 'project.toml'
    values = validate(tomllib.loads(path.read_text(encoding='utf-8'))) if path.is_file() else {}
    entries = values.get('project', {}).get('entries', [])
    return {} if entry is not None and entries and entry not in entries else values


def settings(source):
    profile = load(source.worktree_origin or source.root, source.entry)
    for key, value in (source.configuration or {}).items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key].update(value)
        else:
            profile[key] = value
    return profile


def output(root, entry, kind):
    return directory(root) / 'entries' / identity_hash(str(entry)) / kind


def validate(data):
    if not isinstance(data.get("compile", {}), dict):
        raise ValueError("配置 [compile] 必须是表")
    source = data.get('source', {})
    if not isinstance(source, dict) or set(source) - {'include_macros', 'omit_macros', 'environments'}:
        raise ValueError('配置 [source] 含未知字段或类型错误')
    for key in ('include_macros', 'environments'):
        values = source.get(key, {})
        if not isinstance(values, dict):
            raise ValueError(f'配置 [source].{key} 必须是表')
        for name, value in values.items():
            if not re.fullmatch(r'[A-Za-z@]+', name) or not isinstance(value, str) or not value:
                raise ValueError(f'配置 [source].{key} 含非法映射')
            if key == 'include_macros' and (value.count('#1') != 1 or '\\' in value or Path(value).is_absolute()):
                raise ValueError('包含宏必须使用一个 #1 参数及相对路径')
            if key == 'environments' and value not in {'figure', 'table', 'keywords'}:
                raise ValueError('环境别名仅支持 figure、table 或 keywords')
    names = source.get('omit_macros', [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[A-Za-z@]+', n) for n in names):
        raise ValueError('omit_macros 必须是命令名称数组')
    project = data.get('project', {})
    if not isinstance(project, dict) or set(project) - {'read_only', 'entries'}:
        raise ValueError('配置 [project] 含未知字段或类型错误')
    if type(project.get('read_only', False)) is not bool:
        raise ValueError('read_only 必须是布尔值')
    for values in (project.get('entries', []), data.get('compile', {}).get('sequence', []), data.get('compile', {}).get('include_paths', [])):
        if not isinstance(values, list) or any(not isinstance(p, str) or not p or Path(p).is_absolute() or '..' in Path(p).parts for p in values):
            raise ValueError('入口和编译顺序必须是项目根目录内的相对路径数组')
    return data


def main(argv=None):
    """Import an explicit profile without writing into its paper project."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description='为论文项目保存外部配置')
    parser.add_argument('root', type=Path, help='包含所有入口和共享资源的项目根目录')
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = args.root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('项目根目录不存在')
        from .cli import _config
        values = _config(args.config.expanduser().resolve(), True)
        for entry in values.get('project', {}).get('entries', []):
            path = (root / entry).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('入口必须位于项目根目录内')
        target = directory(root) / 'project.toml'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(args.config.expanduser().read_bytes())
        temporary.replace(target)
        print(json.dumps({'root': str(root), 'config': str(target),
                          'entries': values.get('project', {}).get('entries', [])}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        import sys
        print(f'项目配置失败：{exc}', file=sys.stderr)
        return 64


if __name__ == '__main__':
    raise SystemExit(main())
