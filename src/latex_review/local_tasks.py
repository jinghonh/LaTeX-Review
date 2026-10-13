"""Track every subprocess in a managed worker, including separate TeX sessions."""
import os
import signal
import subprocess
from threading import RLock
import time

_lock = RLock()
_processes = set()
_closed = False
OWNER_FD = None
_original = subprocess.Popen


def install():
    """Only installed in the isolated managed worker, never in an upload client."""
    class ManagedProcess(_original):
        def __init__(self, *args, **kwargs):
            # All worker descendants get their own cancellable group. Explicit
            # compilation sessions retain the same boundary as before.
            kwargs['start_new_session'] = True
            with _lock:
                if _closed:
                    raise OSError('工作进程正在停止，拒绝新任务')
                super().__init__(*args, **kwargs)
                _processes.add(self)

        def wait(self, *args, **kwargs):
            result = super().wait(*args, **kwargs)
            with _lock:
                _processes.discard(self)
            return result
    subprocess.Popen = ManagedProcess


def cancel():
    global _closed
    with _lock:
        _closed = True
        processes = list(_processes)
    for proc in processes:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 1
    for proc in processes:
        try:
            proc.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def inherit_owner(fd):
    """Replay processes monitor the same read end; no descendant owns a writer."""
    global OWNER_FD
    from threading import Thread
    OWNER_FD = fd
    os.set_inheritable(fd, False)
    install()
    def watch():
        try:
            while os.read(fd, 1):
                pass
        finally:
            cancel()
    Thread(target=watch, name='replay-owner', daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: Thread(target=cancel, daemon=True).start())
