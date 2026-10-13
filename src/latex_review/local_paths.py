"""Private per-user local state, independent of the resident service."""
import os
from pathlib import Path
import sys


def state_directory():
    configured = os.environ.get('LATEX_REVIEW_STATE_HOME') or os.environ.get('XDG_STATE_HOME')
    if configured:
        base = Path(configured).expanduser()
        if not base.is_absolute():
            raise ValueError('本地状态目录必须是绝对路径')
        path = base if os.environ.get('LATEX_REVIEW_STATE_HOME') else base / 'latex-review' / 'local-service'
    elif sys.platform == 'darwin':
        path = Path.home() / 'Library' / 'Application Support' / 'latex-review' / 'local-service'
    else:
        path = Path.home() / '.local' / 'state' / 'latex-review' / 'local-service'
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('本地状态目录不可含符号链接')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path
