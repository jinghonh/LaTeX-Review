"""Cross-process output ownership, including overlapping ancestor paths."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
from threading import RLock
import time

from .identity import identity_hash
from .local_paths import state_directory
from .local_protocol import valid_id
from .local_state import file_lock, lock_busy, read_json, write_json

_guard = RLock()
_owned = {}
_generation = ContextVar('latex_review_generation', default=None)


@contextmanager
def generation_guard(stack):
    token = _generation.set(stack)
    try:
        yield
    finally:
        _generation.reset(token)


def release_generation():
    """Upload calls this after freezing bytes, before any network operation."""
    if stack := _generation.get():
        stack.close()


@contextmanager
def output_lease(output, *, wait=True, timeout=120, persistent=False):
    output = Path(output).expanduser().resolve()
    key = (os.getpid(), str(output))
    lease = None
    with _guard:
        if key in _owned:
            _owned[key][1] += 1
            nested = True
        else:
            nested = False
    if not nested:
        state = state_directory()
        deadline = time.monotonic() + timeout
        while True:
            with _guard, file_lock(state / 'claims.lock'):
                claims = read_json(state / 'claims.json', [])
                if (not isinstance(claims, list) or any(not isinstance(claim, dict)
                        or not valid_id(claim.get('key')) or not isinstance(claim.get('output'), str)
                        or not Path(claim['output']).is_absolute() for claim in claims)):
                    raise ValueError('输出认领索引损坏')
                active = [claim for claim in claims if lock_busy(state / 'output-locks' / (claim['key'] + '.lock'))]
                overlaps = [claim for claim in active if output.is_relative_to(Path(claim['output']))
                            or Path(claim['output']).is_relative_to(output)]
                if not overlaps:
                    lock_key = identity_hash({'output': str(output)})
                    lease = file_lock(state / 'output-locks' / (lock_key + '.lock'), blocking=False)
                    lease.__enter__()
                    active.append({'key': lock_key, 'output': str(output), 'pid': os.getpid(), 'persistent': persistent})
                    try:
                        write_json(state / 'claims.json', active)
                    except BaseException:
                        lease.__exit__(None, None, None)
                        raise
                    _owned[key] = [lease, 1]
                    break
                # Recheck same-process acquisition under the guard after waiting.
                if key in _owned:
                    _owned[key][1] += 1
                    nested = True
                    break
            if not wait or any(c.get('persistent') for c in overlaps) or time.monotonic() >= deadline:
                raise ValueError(f'输出目录已被活动任务认领：{output}')
            time.sleep(0.05)
    try:
        yield output
    finally:
        with _guard:
            _owned[key][1] -= 1
            if _owned[key][1] == 0:
                held = _owned.pop(key)[0]
                held.__exit__(None, None, None)
