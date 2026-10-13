"""Compatible content identity and canonical local service identity."""
import hashlib
import json
from pathlib import Path


def identity_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def paper_identity(root, entry, *, require_entry=False):
    root = Path(root).expanduser().resolve(strict=True)
    entry = Path(entry).expanduser()
    entry = (entry if entry.is_absolute() else root / entry).resolve(strict=require_entry)
    if not root.is_dir() or not entry.is_relative_to(root) or entry == root:
        raise ValueError('论文入口必须位于真实项目根目录内')
    if require_entry and not entry.is_file():
        raise ValueError('论文入口不存在')
    relative = entry.relative_to(root).as_posix()
    return {'paper_id': identity_hash({'root': str(root), 'entry': relative}),
            'root': str(root), 'entry': relative}
