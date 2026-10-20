"""Explicit subprocess creation and cancellation for managed workers."""
import os
import signal
import subprocess
from threading import RLock
import time

_lock = RLock()
_processes = set()
_closed = False
OWNER_FD = None
_enabled = False


def install():
    """Enable ownership tracking without changing the subprocess module."""
    global _enabled
    _enabled = True


class _ManagedProcess(subprocess.Popen):
    def wait(self, *args, **kwargs):
        result = super().wait(*args, **kwargs)
        with _lock:
            _processes.discard(self)
        return result


def popen(*args, **kwargs):
    if not _enabled:
        return subprocess.Popen(*args, **kwargs)
    with _lock:
        if _closed:
            raise OSError('工作进程正在停止，拒绝新任务')
        process = _ManagedProcess(*args, **{**kwargs, 'start_new_session': True})
        _processes.add(process)
        return process


def run(*args, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """The subprocess.run interface, with explicit ownership when enabled."""
    if not _enabled:
        return subprocess.run(*args, input=input, capture_output=capture_output,
                              timeout=timeout, check=check, **kwargs)
    if input is not None:
        if 'stdin' in kwargs:
            raise ValueError('stdin and input arguments may not both be used')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if 'stdout' in kwargs or 'stderr' in kwargs:
            raise ValueError('stdout and stderr arguments may not be used with capture_output')
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with popen(*args, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            raise
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, process.args, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


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
