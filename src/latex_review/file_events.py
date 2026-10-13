"""Native project events with a visible, metadata-only safety fallback."""
from __future__ import annotations

import os
from pathlib import Path
from threading import Condition, Event, Thread
import time

IGNORED = {'.git', '.venv', 'venv', '__pycache__', '.latex-review'}


class ProjectEvents:
    def __init__(self, root: Path, output: Path, cache_directory: Path,
                 *, force_polling: bool = False, audit_interval: float = 30,
                 extra_files: tuple[Path, ...] = ()):
        self.extra_files = tuple(path.absolute() for path in extra_files)
        self.root = root.resolve()
        from .local_paths import state_directory
        self.excluded = (output.resolve(), cache_directory.resolve(), state_directory())
        self.audit_interval = audit_interval
        self.condition = Condition()
        self.stop = Event()
        self.ready = Event()
        self.generation = 0
        self.warning = ''
        self.force_polling = force_polling
        self.thread = Thread(target=self._run, daemon=True)

    def relevant(self, path: str | Path) -> bool:
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root):
            return False
        relative = path.relative_to(self.root)
        output_name = self.excluded[0].name
        if any(part.startswith(tuple("." + output_name + suffix for suffix in (".stage-", ".before-", ".rebuild-")))
               or part == "." + output_name + ".rebuild.lock" for part in relative.parts):
            return False
        return not any(part in IGNORED for part in relative.parts) and not any(
            path.is_relative_to(excluded) for excluded in self.excluded)

    def extra_snapshot(self) -> tuple:
        values = []
        for path in self.extra_files:
            try:
                value = path.stat()
                values.append((value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns))
            except FileNotFoundError:
                values.append(None)
        return tuple(values)

    def snapshot(self) -> dict:
        """Never read contents. Include inode/ctime for atomic or timestamp-preserving saves."""
        result = {"extra_files": self.extra_snapshot()}
        def fail(error):
            raise error
        for current, dirs, names in os.walk(self.root, followlinks=False, onerror=fail):
            folder = Path(current)
            dirs[:] = [name for name in dirs if self.relevant(folder / name)]
            for name in (*dirs, *names):
                path = folder / name
                if not self.relevant(path):
                    continue
                try:
                    value = path.stat()
                    link = path.lstat()
                    result[str(path)] = (value.st_dev, value.st_ino, value.st_size,
                                         value.st_mtime_ns, value.st_ctime_ns,
                                         link.st_ino, link.st_mtime_ns)
                except FileNotFoundError:
                    result[str(path)] = None
        return result

    def _changed(self):
        with self.condition:
            self.generation += 1
            self.condition.notify_all()

    def _fallback(self, reason: str):
        fallback_warning = '文件事件监听不可用，已降级为0.7秒元数据检查：' + reason
        self.warning = fallback_warning
        try:
            previous = self.snapshot()
        except OSError:
            previous = None
        self._changed()
        self.ready.set()
        while not self.stop.is_set():
            try:
                current = self.snapshot()
                if current != previous or self.warning != fallback_warning:
                    self.warning = fallback_warning
                    self._changed()
                previous = current
            except OSError as exc:
                self.warning = '文件检查失败，预览可能过期：' + str(exc)
                previous = None
                self._changed()
            self.stop.wait(0.7)

    def _run(self):
        try:
            if self.force_polling:
                raise RuntimeError('用户选择元数据轮询')
            from watchfiles._rust_notify import RustNotify
            # Construct before the initial build so its changes cannot fall into a startup gap.
            with RustNotify([str(self.root)], False, False, 700, True, False) as watcher:
                previous = self.snapshot()
                identity = self.root.stat().st_ino
                extra = self.extra_snapshot()
                self.ready.set()
                audited = time.monotonic()
                while not self.stop.is_set():
                    changes = watcher.watch(300, 100, 1000, self.stop)
                    if isinstance(changes, set):
                        paths = [path for _, path in changes if self.relevant(path)]
                        if any(Path(os.path.abspath(path)) != self.root for path in paths):
                            self._changed()
                            previous = self.snapshot()
                        elif paths:
                            # macOS can aggregate excluded-child notices at the root.
                            current = self.snapshot()
                            if current != previous:
                                self._changed()
                            previous = current
                    current_extra = self.extra_snapshot()
                    if current_extra != extra:
                        extra = current_extra
                        self._changed()
                    if self.root.stat().st_ino != identity:
                        raise RuntimeError('项目根目录已重建')
                    if time.monotonic() - audited >= self.audit_interval:
                        current = self.snapshot()
                        if current != previous:
                            self._changed()
                        previous = current
                        audited = time.monotonic()
        except Exception as exc:
            if not self.stop.is_set():
                self._fallback(str(exc))
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        self.ready.wait()
        return self

    def __exit__(self, *args):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join()

    def wait(self, previous: int, timeout: float = 0.7) -> int:
        with self.condition:
            self.condition.wait_for(lambda: self.generation != previous or self.stop.is_set(), timeout)
            return self.generation
