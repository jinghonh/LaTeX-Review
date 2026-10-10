"""记录单次命令运行；浏览器验收仅表示外部声明。"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .cache import dependency_fingerprints


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def baseline(expanded) -> dict:
    fingerprints = dependency_fingerprints(expanded)
    scope = 'entry_and_resolved_dependencies'
    if expanded.source.identity.kind in {'git', 'worktree'}:
        root = expanded.source.root
        fingerprints = {p.relative_to(root).as_posix():
                        ('symlink:' + os.readlink(p) if p.is_symlink() else hashlib.sha256(p.read_bytes()).hexdigest())
                        for p in root.rglob('*') if p.is_file() or p.is_symlink()}
        scope = 'materialized_snapshot'
    return {**asdict(expanded.source.identity), 'entry': expanded.source.entry,
            'content_fingerprint': digest(fingerprints), 'fingerprint_scope': scope}


def artifact_hash(root: Path) -> str:
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in ('diff.json', 'diagnostics.json', 'report.html') if (root / name).is_file()})


def new_manifest() -> dict:
    return {'schema_version': '1.0', 'exit_code': None, 'status': 'running', 'baselines': {},
            'stages': {name: 'not_executed' for name in ('sources', 'parse', 'compare', 'compile', 'report', 'upload', 'preview')},
            'artifacts': {}, 'degradation_reasons': [],
            'browser_acceptance': {'status': 'not_executed', 'evidence': [], 'kind': 'agent_declaration'}}


def write_manifest(root: Path, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.run-', suffix='.tmp', dir=root)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        os.replace(temporary, root / 'run.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def finish(root: Path, data: dict, code: int) -> None:
    running = 'running' in data['stages'].values()
    data['exit_code'] = None if running else code
    data['status'] = 'running' if running else 'complete' if code == 0 else 'degraded' if code == 2 else 'failed'
    data['report_fingerprint'] = artifact_hash(root)
    # 预览期间独立命令可能已写入验收声明；同一报告才可继承。
    try:
        existing = json.loads((root / 'run.json').read_text(encoding='utf-8'))
        if existing.get('report_fingerprint') == data['report_fingerprint']:
            data['browser_acceptance'] = existing['browser_acceptance']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data['artifacts'] = {p.name: str(p.resolve()) for p in root.iterdir() if p.is_file() and p.name != 'run.json' and not p.name.startswith('.')}
    write_manifest(root, data)


def finish_failure(root: Path, data: dict, code: int) -> None:
    try:
        finish(root, data, code)
    except OSError as exc:
        print(f'latex-review: 无法写入运行清单：{exc}', file=sys.stderr)
