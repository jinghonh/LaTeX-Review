"""Share identical report resources within one paper using replaceable hard links."""
from hashlib import sha256
import os
from pathlib import Path
import secrets
import stat


def deduplicate_resource(path: Path) -> bool:
    """Keep report URLs unchanged; unsupported filesystems retain the original copy."""
    path = path.absolute()
    root = next((p for p in path.parents if p.name == '.latex-review'), None)
    if root is None:
        return False
    cache = root / 'resource-cache'
    temporary = path.with_name('.resource-link-' + secrets.token_hex(12))
    try:
        if any(p.is_symlink() for p in (path, *path.parents)):
            return False
        cache.mkdir(exist_ok=True)
        if cache.is_symlink() or not cache.is_dir():
            return False
        digest = sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        entry = cache / digest.hexdigest()
        try:
            os.link(path, entry)
        except FileExistsError:
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode):
                return False
            # Detect externally edited cache entries before sharing their contents.
            cached = sha256()
            with entry.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    cached.update(block)
            if cached.digest() != digest.digest():
                return False
        os.link(entry, temporary)
        os.replace(temporary, path)
        return True
    except OSError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
