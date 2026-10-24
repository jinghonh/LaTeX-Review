"""Report-scoped translation lifecycle shared by preview and annotation updates.

The session follows its report: when translation-units.json is replaced (a rebuild in this
or another process), the next access refreshes it. Nobody else needs to call refresh().
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from .translation import TranslationConfig, TranslationError, TranslationManager, validate_manifest


class TranslationSession:
    def __init__(self, report: Path, config: TranslationConfig, directory: Path):
        self.report, self.config, self.directory = report, config, directory
        self.lock = RLock()
        self.refreshing = False
        self.closed = False
        self.message = ""
        self.loaded = self._manifest_key()
        self.manager = self._load()

    @property
    def _manifest(self) -> Path:
        return self.report / "translation-units.json"

    def _manifest_key(self):
        try:
            stat = self._manifest.lstat()
        except FileNotFoundError:
            return None
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _follow_report(self) -> None:
        if not self.closed and not self.refreshing and self._manifest_key() != self.loaded:
            self.refresh()

    def _load(self) -> TranslationManager:
        manifest = self._manifest
        if manifest.is_symlink():
            raise ValueError("翻译清单不可为符号链接")
        units = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else []
        return TranslationManager(validate_manifest(units), self.config, self.directory)

    def _available(self) -> TranslationManager:
        if self.closed:
            raise TranslationError("翻译服务已关闭")
        if self.refreshing:
            raise TranslationError("翻译内容正在刷新，请稍后重试")
        if self.manager is None:
            raise TranslationError(self.message)
        return self.manager

    def submit(self, unit_ids: list[str], *, retry: bool = False) -> None:
        self._follow_report()
        with self.lock:
            self._available().submit(unit_ids, retry=retry)

    def cancel(self) -> None:
        self._follow_report()
        with self.lock:
            self._available().cancel()

    def snapshot(self) -> dict:
        self._follow_report()
        with self.lock:
            if not self.closed and not self.refreshing and self.manager is not None:
                return self.manager.snapshot()
            return {"enabled": True, "available": False, "states": {}, "results": {},
                    "message": ("翻译服务已关闭" if self.closed else
                                "翻译内容正在刷新，请稍后重试" if self.refreshing else self.message)}

    def refresh(self) -> str:
        """Cancel the old generation; recover only matching cached results."""
        with self.lock:
            if self.closed:
                return "翻译服务已关闭"
            if self.refreshing:
                return "翻译内容正在刷新，请稍后重试"
            self.refreshing = True
            # Record the generation before reading it, so a write racing this load is seen next time.
            self.loaded = self._manifest_key()
            previous, self.manager = self.manager, None
            if previous is not None:
                previous.close()
        replacement = None
        warning = ""
        try:
            replacement = self._load()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            warning = "翻译服务刷新失败：" + str(exc)
        finally:
            with self.lock:
                if self.closed:
                    if replacement is not None:
                        replacement.close()
                else:
                    self.manager = replacement
                    self.message = warning
                self.refreshing = False
        return warning

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.manager is not None:
                self.manager.close()

    # Existing Python readers inspect the active content and completed cache.
    @property
    def units(self):
        self._follow_report()
        with self.lock:
            return self._available().units

    @property
    def keys(self):
        self._follow_report()
        with self.lock:
            return self._available().keys

    @property
    def results(self):
        self._follow_report()
        with self.lock:
            return self._available().results
