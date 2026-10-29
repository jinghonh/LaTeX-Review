"""Durable private operation records and immutable static report snapshots.

Capture exceptions belong to the caller's retry surface; current reports are never
modified. Callers must strip private operation fields before returning them over
HTTP, and serve snapshot HTML with a CSP sandbox (allow-scripts, no same-origin).
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .delivery import publication_html, report_resources
from .local_protocol import LocalError
from .local_state import file_lock
from .report_rebuild import rebuild_lock

RETENTION_DAYS = 90
REPORTS_PER_PAPER = 20
_TERMINAL = {'ready', 'complete', 'completed', 'succeeded', 'failed', 'cancelled', 'interrupted', 'stopped'}


def _now():
    return datetime.now(timezone.utc)


def _time(value, default):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _private(value):
    """Never persist credentials; retry recipes reference config paths instead."""
    if isinstance(value, dict):
        return {key: _private(item) for key, item in value.items()
                if not re.search(r'(?:^|_)(?:token|secret|password|credential|credentials|api_key|authorization|cookie)(?:$|_)', key.lower())}
    if isinstance(value, (tuple, list)):
        return [_private(item) for item in value]
    return str(value) if isinstance(value, Path) else value


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_file(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('历史报告文件不可缺失或含符号链接')
    return path


def _cached_results(directory, request):
    manifest = directory / 'translation-units.json'
    if not request.get('cache') or not request.get('config') or not manifest.exists():
        return None, ()
    from .cli import _config
    from .translation import TranslationConfig, cache_key, read_cached_translations, rendered_result, validate_manifest
    configuration = _config(Path(request['config']), bool(request.get('config_explicit', False)))
    if not configuration.get('translation'):
        return None, ()
    config = TranslationConfig.from_mapping(configuration['translation'])
    try:
        units = validate_manifest(json.loads(_safe_file(manifest).read_text(encoding='utf-8')))
    except (OSError, ValueError, KeyError, TypeError):
        # Legacy reports remain readable even when their translation protection
        # rules predate the current cache validator. Never reinterpret old keys.
        return None, ('旧报告的翻译清单不兼容，已保留报告内已有译文；未载入缓存译文。',)
    cached, warnings = read_cached_translations(units, config, Path(request['cache']).expanduser())
    return {u['id']: rendered_result(u, cached[cache_key(u, config)])
            for u in units if not u.get('error') and cache_key(u, config) in cached}, warnings


def upgrade_snapshot_reader(html):
    """Refresh only reader scripts; frozen content and saved bytes stay intact."""
    from .annotations._suggestions import delivery_script
    readers = {
        'report_annotations.js': 'const meta = name => document.querySelector(`meta[name="latex-review-annotation-',
        'report_translation.js': "const manifest = document.getElementById('translation-units')",
        'report_reader.js': "const storageStatus = document.getElementById('review-storage-status')",
        'live_reader.js': "const paper = document.querySelector('.preview-side')",
    }
    def upgrade(match):
        script = match[1]
        if 'window.MathJax={loader:' in script:
            # Hide preference controls; the HTTP sandbox blocks vendor storage.
            return '<script>' + script.replace('window.MathJax={loader:',
                'window.MathJax={options:{enableMenu:false},loader:', 1) + '</script>'
        if '/* Portable annotation contract.' in script:
            return '<script>' + delivery_script() + '</script>'
        for name, marker in readers.items():
            if marker in script:
                return '<script>' + files('latex_review').joinpath(name).read_text(encoding='utf-8') + '</script>'
        return match[0]
    return re.sub(r'<script>(.*?)</script>', upgrade, html, flags=re.S)


def _readonly_html(html):
    """Upgrade embedded readers in imported reports before applying snapshot mode."""
    html = upgrade_snapshot_reader(html)
    html = re.sub(r'''<meta\b(?=[^>]*\bname=["']latex-review-(?:annotation-storage|publication|readonly)["'])[^>]*>''', '', html, flags=re.I)
    tags = ('<meta name="latex-review-readonly" content="snapshot">'
            '<meta name="latex-review-annotation-storage" content="snapshot">'
            '<meta name="latex-review-publication" content="static">'
            '<style>.annotation-editor,.annotation-toolbar,.annotation-card-actions,.annotation-edit,'
            '.annotation-portable-tools,.change-review-editor,#review-import,#review-export,'
            '#review-state,#review-note,.translation-actions,[data-translate-change]'
            '{display:none!important}</style>')
    if re.search(r'<head\b[^>]*>', html, re.I):
        html = re.sub(r'(<head\b[^>]*>)', lambda m: m[0] + tags, html, count=1, flags=re.I)
    else:
        html = tags + html
    banner = ('<aside class="history-readonly-banner" role="note" '
              'style="padding:8px 16px;border-bottom:1px solid #cbd5e1;background:#f1f5f9;color:#334155">'
              '历史快照 · 只读 · 批注和译文固定为保存时内容</aside>')
    if re.search(r'<body\b[^>]*>', html, re.I):
        html = re.sub(r'(<body\b[^>]*>)', lambda m: m[0] + banner, html, count=1, flags=re.I)
    elif re.search(r'</head>', html, re.I):
        html = re.sub(r'</head>', lambda m: m[0] + banner, html, count=1, flags=re.I)
    else:
        html = tags + banner + html[len(tags):]
    return html


class History:
    def __init__(self, state: Path):
        self.root = Path(state).expanduser().resolve() / 'history'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError('历史目录不可为符号链接')
        self.root.chmod(0o700)
        self.snapshots = self.root / 'reports'
        self.snapshots.mkdir(exist_ok=True, mode=0o700)
        if self.snapshots.is_symlink():
            raise ValueError('历史报告目录不可为符号链接')
        self.database = self.root / 'history.sqlite3'
        if self.database.is_symlink():
            raise ValueError('历史索引不可为符号链接')
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, paper TEXT NOT NULL, created REAL NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL, resources TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS paper_heads (paper TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, report_id TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO paper_heads SELECT paper, fingerprint, id FROM reports AS r WHERE rowid=(SELECT rowid FROM reports WHERE paper=r.paper ORDER BY created DESC, rowid DESC LIMIT 1)')
        self.database.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=60)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    @staticmethod
    def _prune_operations(db):
        cutoff = (_now() - timedelta(days=RETENTION_DAYS)).timestamp()
        db.execute('DELETE FROM operations WHERE updated < ?', (cutoff,))

    def save_operation(self, operation: dict) -> None:
        value = _private(operation)
        identifier = value.get('operation_id') or value.get('id')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('操作缺少 ID')
        request = value.get('request', {})
        if request and not isinstance(request.get('config', ''), str):
            raise ValueError('重试配置必须使用文件路径')
        now = _now()
        with self._db() as db:
            prior = db.execute('SELECT data FROM operations WHERE id=?', (identifier,)).fetchone()
            previous = json.loads(prior[0]) if prior else {}
            if 'request' not in value and 'request' in previous:
                value['request'] = previous['request']
            created = _time(value.get('created_at', previous.get('created_at')), now)
            value['created_at'] = created.isoformat()
            updated = _time(value.get('finished_at') or value.get('updated_at'), now)
            value['updated_at'] = updated.isoformat()
            db.execute('INSERT OR REPLACE INTO operations VALUES (?,?,?,?)',
                       (identifier, created.timestamp(), updated.timestamp(), json.dumps(value, ensure_ascii=False)))
            self._prune_operations(db)

    def operations(self) -> list:
        with self._db() as db:
            self._prune_operations(db)
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM operations ORDER BY updated DESC, created DESC, id DESC')]

    def get_operation(self, id) -> dict | None:
        with self._db() as db:
            self._prune_operations(db)
            row = db.execute('SELECT data FROM operations WHERE id=?', (id,)).fetchone()
            return json.loads(row[0]) if row else None

    def recover_operations(self) -> None:
        """Call once at daemon startup, not when constructing rebuild clients."""
        with self._db() as db:
            self._prune_operations(db)
            now = _now()
            for identifier, data in db.execute('SELECT id, data FROM operations').fetchall():
                value = json.loads(data)
                if value.get('state') in _TERMINAL:
                    continue
                value.update(state='failed', interrupted=True, finished_at=now.isoformat(), updated_at=now.isoformat(),
                             message='本地服务已重启，操作已中断，可重试。')
                db.execute('UPDATE operations SET updated=?, data=? WHERE id=?',
                           (now.timestamp(), json.dumps(value, ensure_ascii=False), identifier))

    def capture(self, paper_id, directory: Path, request: dict, reason: str) -> dict:
        directory = Path(directory).expanduser().resolve()
        # The lock order is shared by daemon captures and captures inside rebuilds.
        with rebuild_lock(directory), file_lock(self.root / '.capture.lock'):
            return self._capture(paper_id, directory, request, reason)

    def _capture(self, paper_id, directory, request, reason):
        source_html = _safe_file(directory / 'report.html').read_text(encoding='utf-8')
        resources = report_resources(source_html, directory)
        baselines = {}
        run, diff = directory / 'run.json', directory / 'diff.json'
        if run.exists():
            baselines = json.loads(_safe_file(run).read_text(encoding='utf-8')).get('baselines', {})
        if not baselines and diff.exists():
            data = json.loads(_safe_file(diff).read_text(encoding='utf-8'))
            baselines = {key: data[key] for key in ('old', 'new') if key in data}
        # An annotation/cache update is not a new body revision. Do this before
        # loading caches or embedding the current annotation backup.
        body = re.sub(r'<script type="application/json" id="(?:translation-results|annotation-(?:content|backup|backup-gzip|current-version|assets))">.*?</script>', '', source_html, flags=re.S)
        digest = sha256(body.encode('utf-8'))
        digest.update(json.dumps(baselines, sort_keys=True, ensure_ascii=False).encode('utf-8'))
        for relative, source in sorted(resources.items()):
            content_hash = sha256()
            with _safe_file(source).open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    content_hash.update(block)
            digest.update(relative.encode('utf-8') + b'\0' + content_hash.digest())
        fingerprint = digest.hexdigest()
        with self._db() as db:
            row = db.execute('SELECT r.data, h.fingerprint FROM paper_heads h LEFT JOIN reports r ON r.id=h.report_id WHERE h.paper=?', (paper_id,)).fetchone()
        if row and row[1] == fingerprint:
            # A prior capture may have committed before old-file cleanup failed.
            # Retrying that revision must finish cleanup instead of skipping it.
            self._prune_reports(paper_id)
            return json.loads(row[0]) if row[0] else {'paper_id': paper_id, 'state': 'ready', 'skipped': True}
        results, warnings = _cached_results(directory, request)
        if results is not None:
            saved = re.search(r'<script type="application/json" id="translation-results">(.*?)</script>',
                              source_html, re.S)
            if saved:
                # Cache eviction must not erase translations already carried by
                # the report. Valid cache hits take precedence for matching IDs.
                embedded = json.loads(saved[1])
                if isinstance(embedded, dict):
                    results = {**embedded, **results}
        html = _readonly_html(publication_html(directory, results=results, warnings=warnings, include_assets=False))
        # Frozen annotations do not inline images; all linked resources remain
        # ordinary files, without publication's single-file size limits.
        resources = report_resources(html, directory)
        report_id = uuid4().hex
        stage = Path(tempfile.mkdtemp(prefix='.capture-', dir=self.snapshots))
        destination = self.snapshots / report_id
        committed = False
        try:
            total = 0
            targets = {'report.html': None, **resources}
            for relative, source in sorted(targets.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open('xb') as output:
                    os.chmod(target, 0o600)
                    if source is None:
                        block = html.encode('utf-8')
                        output.write(block)
                        total += len(block)
                    else:
                        with _safe_file(source).open('rb') as stream:
                            while block := stream.read(1024 * 1024):
                                output.write(block)
                                total += len(block)
                    output.flush()
                    os.fsync(output.fileno())
            for folder, _, _ in os.walk(stage, topdown=False):
                _fsync_directory(folder)
            os.replace(stage, destination)
            _fsync_directory(self.snapshots)
            now = _now()
            metadata = dict(report_id=report_id, paper_id=paper_id, created_at=now.isoformat(), reason=reason,
                            bytes=total, baselines=baselines, url=f'/history/{report_id}/report.html', state='ready')
            with self._db() as db:
                db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)',
                           (report_id, paper_id, now.timestamp(), fingerprint, json.dumps(metadata, ensure_ascii=False), json.dumps(list(targets))))
                db.execute('INSERT OR REPLACE INTO paper_heads VALUES (?,?,?)', (paper_id, fingerprint, report_id))
            committed = True
            # Only prune after both files and the new index entry are durable.
            self._prune_reports(paper_id)
            return metadata
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            if not committed:
                shutil.rmtree(destination, ignore_errors=True)

    def reports(self) -> list:
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM reports ORDER BY created DESC, rowid DESC')]

    def _remove_report(self, db, identifier):
        # Keep the index on a filesystem failure so a retry can find the files.
        # All callers hold .capture.lock, serializing capture and deletion.
        try:
            shutil.rmtree(self.snapshots / identifier)
        except FileNotFoundError:
            pass
        db.execute('DELETE FROM reports WHERE id=?', (identifier,))

    def _prune_reports(self, paper_id):
        with self._db() as db:
            stale = [row[0] for row in db.execute(
                'SELECT id FROM reports WHERE paper=? ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET ?',
                (paper_id, REPORTS_PER_PAPER))]
        for identifier in stale:
            with self._db() as db:
                self._remove_report(db, identifier)

    def report_file(self, report_id, relative) -> Path:
        missing = LocalError('历史报告文件不存在', 404)
        if not isinstance(report_id, str) or not re.fullmatch(r'[0-9a-f]{32}', report_id) or not isinstance(relative, str):
            raise missing
        try:
            url = urlsplit(relative)
        except ValueError:
            raise missing from None
        path = PurePosixPath(unquote(url.path))
        if (url.scheme or url.netloc or url.query or url.fragment or path.is_absolute() or '..' in path.parts
                or '\\' in relative or '\x00' in relative or path.as_posix() != relative):
            raise missing
        with self._db() as db:
            row = db.execute('SELECT resources FROM reports WHERE id=?', (report_id,)).fetchone()
        if not row or relative not in json.loads(row[0]):
            raise missing
        target = self.snapshots / report_id / relative
        try:
            return _safe_file(target)
        except (ValueError, OSError):
            raise missing from None

    def delete_report(self, id) -> None:
        with file_lock(self.root / '.capture.lock'):
            with self._db() as db:
                row = db.execute('SELECT id FROM reports WHERE id=?', (id,)).fetchone()
                if not row:
                    return
                self._remove_report(db, row[0])
