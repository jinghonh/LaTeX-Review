"""Bounded, paper-local cache for deterministic PDF conversions."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from functools import lru_cache
from hashlib import sha256
import json
import os
import resource
from pathlib import Path
import stat
import shutil
import subprocess
import struct
import tempfile
import time
import zlib

MAX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 512
MAX_RESULT = 12 * 1024 * 1024
MAX_AGE = 30 * 24 * 3600
IMPLEMENTATION = 1


@lru_cache(maxsize=32)
def _binary_digest(path: str, identity: tuple) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def converter_identity(converter: str, timeout: float = 4) -> dict:
    path = Path(converter).resolve(strict=True)
    s = path.stat()
    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, int(timeout) + 1), max(1, int(timeout) + 1)))
    with tempfile.TemporaryDirectory(prefix='latex-review-pdf-version-') as temporary, tempfile.TemporaryFile() as stream:
        try:
            probe = subprocess.run([str(path), '-v' if path.name == 'pdftoppm' else '--version'],
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   timeout=timeout, check=False, preexec_fn=limits, cwd=temporary)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('PDF 转换器版本检查超时') from exc
        if probe.returncode:
            raise ValueError(f'PDF 转换器版本检查失败：退出状态 {probe.returncode}')
        stream.seek(0)
        version = stream.read(4096).decode('utf-8', 'replace')
    return {'path': str(path), 'version': version, 'sha256': _binary_digest(str(path),
            (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns))}


def valid_png(data: bytes) -> bool:
    """Check complete PNG chunks, including CRCs, without decoding untrusted pixels."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    pos = 8
    first = True
    while pos + 12 <= len(data):
        size = struct.unpack('>I', data[pos:pos + 4])[0]
        end = pos + 12 + size
        if end > len(data):
            return False
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:end - 4]
        if zlib.crc32(kind + body) != struct.unpack('>I', data[end - 4:end])[0]:
            return False
        if first and (kind != b'IHDR' or size != 13):
            return False
        first = False
        pos = end
        if kind == b'IEND':
            return size == 0 and pos == len(data)
    return False


@contextmanager
def _cache(directory: Path):
    # An ordinary output is its own scope; .latex-review outputs share only that paper.
    output = directory.absolute()
    root = next((p for p in (output, *output.parents) if p.name == '.latex-review'), output)
    cache = root / 'pdf-cache'
    if any(p.is_symlink() for p in (cache, *cache.parents)):
        raise OSError('PDF cache path contains a symlink')
    cache.mkdir(exist_ok=True)
    fd = os.open(cache / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('PDF cache lock is not a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield cache
    finally:
        os.close(fd)


def _prune(cache: Path) -> None:
    entries = []
    now = time.time()
    for p in cache.iterdir():
        s = p.lstat()
        if stat.S_ISDIR(s.st_mode) and p.name.startswith('.tmp-'):
            shutil.rmtree(p)
            continue
        if not stat.S_ISREG(s.st_mode):
            continue
        if p.name.startswith('.tmp-') or (p.suffix == '.bin' and now - s.st_mtime > MAX_AGE):
            p.unlink()
        elif p.suffix == '.bin':
            entries.append((s.st_mtime, p, s.st_size))
    total = sum(item[2] for item in entries)
    count = len(entries)
    for _, p, size in sorted(entries):
        if total <= MAX_BYTES and count <= MAX_ENTRIES:
            break
        p.unlink(); total -= size; count -= 1


def converted(pdf: Path, directory: Path, converter: str, parameters: dict, produce, validate, *, version_timeout: float = 4) -> bytes:
    """Serialize misses; publish only checked results. Conversion uses the hashed snapshot."""
    content = pdf.read_bytes()
    key = sha256(json.dumps({'implementation': IMPLEMENTATION, 'pdf': sha256(content).hexdigest(),
                            'converter': converter_identity(converter, version_timeout), 'parameters': parameters},
                           sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with _cache(directory) as cache:
        _prune(cache)
        entry = cache / (key + '.bin')
        try:
            s = entry.lstat()
            if stat.S_ISREG(s.st_mode) and 65 < s.st_size <= MAX_RESULT + 65:
                packet = entry.read_bytes()
                data = packet[65:]
                if packet[:65] == sha256(data).hexdigest().encode() + b'\n' and validate(data):
                    os.utime(entry, None)
                    return data
        except (FileNotFoundError, ValueError):
            pass
        # Snapshot and intermediate output are removed even on converter failure.
        with tempfile.TemporaryDirectory(prefix='.tmp-', dir=cache) as tmp:
            snapshot = Path(tmp) / 'input.pdf'
            snapshot.write_bytes(content)
            data = produce(snapshot)
        if not 0 < len(data) <= MAX_RESULT or not validate(data):
            raise ValueError('PDF converter produced an invalid or oversized result')
        fd, name = tempfile.mkstemp(prefix='.tmp-', dir=cache)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(sha256(data).hexdigest().encode() + b'\n' + data)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, entry)
        finally:
            Path(name).unlink(missing_ok=True)
        _prune(cache)
        return data
