"""Authoritative project-root knowledge, independent from check runs and source text.

Reads never create files. Writes require BOTH the revision and canonical content
hash returned by read(), under a cross-process root lock. Manual whitespace/key
ordering edits are harmless; meaningful edits conflict even without a revision bump.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.resources import files
import json
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from uuid import uuid4

from jsonschema import Draft202012Validator

COLLECTIONS = ('concepts', 'symbol_bindings', 'occurrences', 'relations', 'exceptions')
_SCHEMA = json.loads(files('latex_review').joinpath('schemas/knowledge.schema.json').read_text(encoding='utf-8'))
_VALIDATOR = Draft202012Validator(_SCHEMA)


class RegistryError(ValueError):
    code = 'knowledge_error'


class KnowledgeValidationError(RegistryError):
    code = 'knowledge_validation'


class KnowledgeConflict(RegistryError):
    code = 'knowledge_conflict'


class KnowledgeCorrupt(RegistryError):
    code = 'knowledge_corrupt'


class KnowledgeSourceConflict(RegistryError):
    code = 'knowledge_source_conflict'


def root_identity(root: Path | str) -> str:
    """Root-only namespace. Deliberately NOT the entry-specific paper_id."""
    return hashlib.sha256(str(Path(root).expanduser().resolve()).encode('utf-8')).hexdigest()


def empty_registry() -> dict:
    return {'schema_version': '1.0', 'revision': 0,
            **{name: [] for name in (*COLLECTIONS, 'decisions')}}


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def canonical_content_hash(registry: dict) -> str:
    return hashlib.sha256(_canonical(registry)).hexdigest()


def _same_import_item(collection, current, incoming):
    return current == incoming or (collection == 'decisions' and current.get('imported') is True
                                   and current == {**incoming, 'imported': True})


def _relative(path: str) -> bool:
    return bool(path) and not (PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
                              or '\\' in path or '..' in PurePosixPath(path).parts)


def validate_registry(registry: dict) -> dict:
    """Validate the same strict schema used by disk, API, and imports."""
    errors = sorted(_VALIDATOR.iter_errors(registry), key=lambda e: str(list(e.path)))
    if errors:
        e = errors[0]
        raise KnowledgeValidationError(f"/{'/'.join(map(str, e.path))}: {e.message}")
    for name in (*COLLECTIONS, 'decisions'):
        ids = [item['id'] for item in registry[name]]
        if len(ids) != len(set(ids)):
            raise KnowledgeValidationError(f'/{name}: duplicate IDs')
    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        if not isinstance(value, dict):
            return
        if 'file_sha256' in value:
            if not _relative(value['file']) or not _relative(value['entry']):
                raise KnowledgeValidationError('evidence file and entry must be relative project paths')
            if value['end'] < value['start'] or len(value['quote']) != value['end'] - value['start']:
                raise KnowledgeValidationError('evidence quote must match Unicode [start,end) length')
            if value['reliability'] == 'unknown' and not value.get('reason'):
                raise KnowledgeValidationError('unknown evidence requires a reason')
        if 'start_order' in value and 'end_order' in value and value['end_order'] < value['start_order']:
            raise KnowledgeValidationError('scope end_order precedes start_order')
        for item in value.values():
            walk(item)
    walk(registry)
    # Cross-object references may intentionally be stale after migration; do not
    # silently resolve them to a similarly named concept or entry.
    return registry


def _read_backup_nofollow(path: Path) -> bytes:
    """Read a regular backup through directory FDs, never following symlinks.

    The active path check alone cannot protect its sibling backup, and an
    is_symlink()+read_bytes() check would still race a replaced backup link.
    Every parent and the final component are opened with O_NOFOLLOW so neither
    a static link nor a concurrent link swap can disclose another file.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('knowledge backup must be a regular file')
            with os.fdopen(fd, 'rb', closefd=False) as handle:
                return handle.read()
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _decode(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise KnowledgeValidationError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs)
    return validate_registry(value)


class KnowledgeStore:
    """Single explicitly selected source, shared by all entries of one root.

    ``state_dir`` is the application's persistent state root (not cache or a
    replaceable report directory). ``path`` optionally overrides the selected
    JSON path; external paths must remain outside the paper root. In read_only
    mode explicit project selection fails rather than silently switching.
    """
    def __init__(self, root, state_dir, *, source='auto', read_only=False, path=None):
        self.root = Path(root).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.root_identity = root_identity(self.root)
        # os.access alone grants root write access even when authors made a paper
        # read-only. Honor directory mode intent as well as read-only mounts.
        read_only = bool(read_only or not (self.root.stat().st_mode & 0o222)
                         or (os.statvfs(self.root).f_flag & getattr(os, 'ST_RDONLY', 1)))
        self.read_only = read_only
        if source not in ('auto', 'project', 'external'):
            raise KnowledgeValidationError('source must be auto, project, or external')
        self.selection = source
        self.project_path = self.root / '.latex-review-knowledge.json'
        self.external_dir = self.state_dir / 'knowledge' / self.root_identity
        self.external_path = self.external_dir / 'knowledge.json'
        self.source = ('external' if read_only or (self.external_path.exists() and not self.project_path.exists())
                       else 'project') if source == 'auto' else source
        if read_only and self.source == 'project':
            raise KnowledgeValidationError('read-only project requires external knowledge storage')
        chosen = Path(path).expanduser() if path is not None else (
            self.project_path if self.source == 'project' else self.external_path)
        if not chosen.is_absolute():
            chosen = (self.root if self.source == 'project' else self.external_dir) / chosen
        self.path = chosen.absolute()
        resolved = self.path.resolve()
        if self.path.suffix.lower() != '.json':
            raise KnowledgeValidationError('knowledge path must be a JSON file, never source text')
        if self.source == 'project' and not resolved.is_relative_to(self.root):
            raise KnowledgeValidationError('project knowledge path escapes project root')
        if self.source == 'external' and resolved.is_relative_to(self.root):
            raise KnowledgeValidationError('external knowledge must be outside project root')
        if self.state_dir.is_relative_to(self.root):
            raise KnowledgeValidationError('persistent state directory must be outside project root')
        self.backup_path = self.path.with_name(self.path.name + '.bak')
        self.lock_path = self.external_dir / 'registry.lock'

    def _check_source(self):
        if self.source == 'project' and (not (self.root.stat().st_mode & 0o222)
                                        or (os.statvfs(self.root).f_flag & getattr(os, 'ST_RDONLY', 1))):
            raise KnowledgeValidationError('read-only project requires external knowledge storage')
        if self.selection == 'auto' and self.project_path.exists() and self.external_path.exists():
            raise KnowledgeSourceConflict('both project and external knowledge exist; select one source explicitly')
        if self.path.is_symlink() or self.path.resolve() != self.path:
            raise KnowledgeValidationError('knowledge path must not traverse symlinks')

    def read(self) -> dict:
        self._check_source()
        base = {'source': self.source, 'path': str(self.path), 'root_identity': self.root_identity,
                'alternatives': [str(p) for p in (self.project_path, self.external_path)
                                 if p != self.path and p.exists()]}
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            registry = empty_registry()
            return {**base, 'registry': registry, 'content_hash': canonical_content_hash(registry),
                    'revision': 0, 'exists': False, 'status': 'missing'}
        try:
            registry = _decode(raw)
        except (ValueError, UnicodeError) as exc:
            # Corruption is never silently treated as writable empty knowledge.
            return {**base, 'registry': empty_registry(), 'content_hash': 'corrupt:' + hashlib.sha256(raw).hexdigest(),
                    'revision': 0, 'exists': True, 'status': 'corrupt', 'error': str(exc)}
        return {**base, 'registry': registry, 'content_hash': canonical_content_hash(registry),
                'revision': registry['revision'], 'exists': True, 'status': 'ok'}

    @contextmanager
    def _lock(self):
        self.external_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open('a+b') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _condition(current, expected_revision, expected_hash):
        if type(expected_revision) is not int or not isinstance(expected_hash, str):
            raise KnowledgeValidationError('expected_revision and expected_hash are required')
        if current['revision'] != expected_revision or current['content_hash'] != expected_hash:
            raise KnowledgeConflict(f"knowledge changed: expected revision/hash {expected_revision}/{expected_hash}, "
                                    f"current {current['revision']}/{current['content_hash']}; refresh and retry")

    @staticmethod
    def _atomic(path: Path, raw: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp uses exclusive creation; replace replaces a destination symlink
        # itself rather than following it (including the sibling .bak path).
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            # mkstemp returned this exact verified directory-local path.
            if temporary.exists():
                temporary.unlink()

    def _write_locked(self, registry, current):
        if current['status'] == 'corrupt':
            raise KnowledgeCorrupt('knowledge is corrupt; preserve original and explicitly recover a backup')
        value = deepcopy(registry)
        value['revision'] = current['revision'] + 1
        validate_registry(value)
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if current['exists']:
            previous = self.path.read_bytes()
            # Recheck non-cooperating manual edits immediately before replacement.
            try:
                observed = canonical_content_hash(_decode(previous))
            except (ValueError, UnicodeError) as exc:
                raise KnowledgeConflict('knowledge was manually corrupted during update') from exc
            if observed != current['content_hash']:
                raise KnowledgeConflict('knowledge was manually edited during update')
            self._atomic(self.backup_path, previous)
        elif self.path.exists():
            raise KnowledgeConflict('knowledge was created during update')
        # Backups may be slow; a non-cooperating editor can save while they flush.
        self._condition(self.read(), current['revision'], current['content_hash'])
        self._atomic(self.path, raw)
        return self.read()

    def write(self, registry: dict, *, expected_revision: int, expected_hash: str) -> dict:
        validate_registry(registry)
        with self._lock():
            current = self.read()
            self._condition(current, expected_revision, expected_hash)
            if registry['revision'] != expected_revision:
                raise KnowledgeConflict('submitted registry revision differs from expected revision')
            if registry['decisions'] != current['registry']['decisions']:
                raise KnowledgeConflict('decision audit history is immutable; use decide/undo or explicit import')
            return self._write_locked(registry, current)

    def export(self) -> dict:
        current = self.read()
        if current['status'] == 'corrupt':
            raise KnowledgeCorrupt(current['error'])
        return deepcopy(current['registry'])

    def preview_import(self, registry: dict) -> dict:
        validate_registry(registry)
        current = self.read()
        if current['status'] == 'corrupt':
            raise KnowledgeCorrupt(current['error'])
        additions, identical, conflicts = [], [], []
        for collection in (*COLLECTIONS, 'decisions'):
            existing = {item['id']: item for item in current['registry'][collection]}
            for item in registry[collection]:
                key = {'collection': collection, 'id': item['id']}
                if item['id'] not in existing:
                    additions.append(key)
                elif _same_import_item(collection, existing[item['id']], item):
                    identical.append(key)
                else:
                    conflicts.append({**key, 'current': deepcopy(existing[item['id']]), 'incoming': deepcopy(item)})
        return {'revision': current['revision'], 'content_hash': current['content_hash'],
                'additions': additions, 'identical': identical, 'conflicts': conflicts}

    def import_registry(self, registry: dict, *, expected_revision: int, expected_hash: str,
                        resolutions: dict | None = None) -> dict:
        """Resolve each conflict explicitly as {'collection/id': 'current'|'incoming'}.

        Imported source anchors are retained, never assumed valid for a moved root;
        confirmed evidence-backed facts become stale pending revalidation.
        """
        validate_registry(registry)
        with self._lock():
            current = self.read()
            self._condition(current, expected_revision, expected_hash)
            preview = self.preview_import(registry)
            resolutions = resolutions or {}
            keys = {f"{x['collection']}/{x['id']}" for x in preview['conflicts']}
            if set(resolutions) != keys or any(x not in ('current', 'incoming') for x in resolutions.values()):
                raise KnowledgeConflict('import conflicts require explicit current/incoming resolution for every conflicting ID')
            result = deepcopy(current['registry'])
            for collection in (*COLLECTIONS, 'decisions'):
                positions = {item['id']: n for n, item in enumerate(result[collection])}
                for item in registry[collection]:
                    index = positions.get(item['id'])
                    if index is not None and _same_import_item(collection, result[collection][index], item):
                        continue
                    if index is not None and resolutions[f"{collection}/{item['id']}"] == 'current':
                        continue
                    if collection == 'decisions' and index is not None:
                        raise KnowledgeConflict('existing decision audit history cannot be replaced by import')
                    imported = deepcopy(item)
                    if collection in ('concepts', 'symbol_bindings', 'occurrences', 'relations'):
                        if imported.get('evidence') or imported.get('definitions'):
                            imported['status'] = 'stale'
                    if collection == 'exceptions':
                        imported['status'] = 'stale'
                    if collection == 'decisions':
                        # Keep original before/after as historical evidence, not an
                        # executable path that can reactivate foreign-root anchors.
                        imported['imported'] = True
                    if index is None:
                        result[collection].append(imported)
                    else:
                        result[collection][index] = imported
            return self._write_locked(result, current)

    def decide(self, collection: str, object_id: str, changes: dict | None, *, action: str,
               reason: str, author: str, evidence_fingerprint: str, scope: dict,
               expected_revision: int, expected_hash: str, request_id: str | None = None,
               input_id: str | None = None) -> dict:
        """Apply an audited object change; changes=None deletes it. No source writes.

        Callers validate current evidence/input identity before author confirmation.
        This storage boundary checks only knowledge identity, not source freshness.
        """
        if collection not in COLLECTIONS:
            raise KnowledgeValidationError('decision collection is not mutable')
        with self._lock():
            current = self.read()
            self._condition(current, expected_revision, expected_hash)
            if current['status'] == 'corrupt':
                raise KnowledgeCorrupt(current['error'])
            value = deepcopy(current['registry'])
            if request_id and any(d.get('request_id') == request_id for d in value['decisions']):
                raise KnowledgeConflict('request_id already used; refresh existing decision')
            index = next((i for i, x in enumerate(value[collection]) if x['id'] == object_id), None)
            before = deepcopy(value[collection][index]) if index is not None else None
            after = ({**(before or {}), **deepcopy(changes), 'id': object_id} if changes is not None else None)
            if index is not None:
                value[collection].pop(index)
            if after is not None:
                value[collection].insert(index if index is not None else len(value[collection]), after)
            decision = {'id': uuid4().hex, 'collection': collection, 'object_id': object_id, 'action': action,
                        'reason': reason, 'author': author, 'timestamp': datetime.now(timezone.utc).isoformat(),
                        'evidence_fingerprint': evidence_fingerprint, 'scope': deepcopy(scope),
                        'before': before, 'after': after}
            if request_id:
                decision['request_id'] = request_id
            if input_id:
                decision['input_id'] = input_id
            value['decisions'].append(decision)
            return self._write_locked(value, current)

    def undo_decision(self, decision_id: str, *, expected_revision: int, expected_hash: str,
                      reason='Author undid decision', author='author', preflight=None) -> dict:
        with self._lock():
            current = self.read()
            self._condition(current, expected_revision, expected_hash)
            if current['status'] == 'corrupt':
                raise KnowledgeCorrupt(current['error'])
            value = deepcopy(current['registry'])
            decision = next((x for x in value['decisions'] if x['id'] == decision_id), None)
            if decision is None:
                raise KnowledgeValidationError('unknown decision ID')
            if decision.get('imported'):
                raise KnowledgeConflict('imported decision history is read-only; reconfirm current evidence in a new decision')
            if any(x.get('undoes') == decision_id for x in value['decisions']):
                raise KnowledgeConflict('decision already undone')
            collection = decision['collection']
            index = next((i for i, x in enumerate(value[collection]) if x['id'] == decision['object_id']), None)
            actual = value[collection][index] if index is not None else None
            if actual != decision['after']:
                raise KnowledgeConflict('object changed after decision; undo would overwrite later edits')
            if index is not None:
                value[collection].pop(index)
            if decision['before'] is not None:
                value[collection].insert(index if index is not None else len(value[collection]), deepcopy(decision['before']))
            value['decisions'].append({**deepcopy(decision), 'id': uuid4().hex, 'action': 'undo',
                                      'reason': reason, 'author': author, 'timestamp': datetime.now(timezone.utc).isoformat(),
                                      'before': deepcopy(decision['after']), 'after': deepcopy(decision['before']),
                                      'undoes': decision_id})
            value['decisions'][-1].pop('request_id', None)
            validate_registry(value)
            if preflight is not None:
                preflight(deepcopy(value))
            return self._write_locked(value, current)

    def recover(self, *, expected_revision: int, expected_hash: str, preflight=None) -> dict:
        """Restore a backup, preserving corrupt bytes under .corrupt.<hash>.

        Optional preflight(registry) runs under the root lock before ANY file is
        replaced. It receives a detached proposed registry, and may reject by
        raising; its return value or mutations cannot alter the committed data.
        """
        with self._lock():
            current = self.read()
            self._condition(current, expected_revision, expected_hash)
            try:
                recovered = _decode(_read_backup_nofollow(self.backup_path))
            except (OSError, ValueError, UnicodeError) as exc:
                raise KnowledgeCorrupt('no valid backup available; original was preserved') from exc
            recovered['revision'] = max(recovered['revision'], current['revision']) + 1
            validate_registry(recovered)
            if preflight is not None:
                preflight(deepcopy(recovered))
            self._condition(self.read(), current['revision'], current['content_hash'])
            if current['exists']:
                raw = self.path.read_bytes()
                if current['status'] == 'corrupt':
                    preserved = self.path.with_name(self.path.name + '.corrupt.' + hashlib.sha256(raw).hexdigest())
                    if not preserved.exists():
                        self._atomic(preserved, raw)
                else:
                    preserved = self.path.with_name(self.path.name + '.recovery.' + current['content_hash'])
                    if not preserved.exists():
                        self._atomic(preserved, raw)
            self._condition(self.read(), current['revision'], current['content_hash'])
            self._atomic(self.path, (json.dumps(recovered, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8'))
            return self.read()
