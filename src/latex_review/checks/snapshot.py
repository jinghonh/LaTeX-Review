"""Consistent current-disk snapshots; never normalize source text or execute TeX."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ..contract import ComparisonSource
from ..identity import identity_hash, paper_identity
from ..macros import validate_macros
from ..sources import ProjectSource, SourceError
from ..structure import ParsedProject, parse_project

PARSER_VERSION = "writing-check-document-1/" + version("plasTeX")


def canonical_check_configuration(configuration):
    """Detach only settings that affect check facts, knowledge or AI authority.

    Diff rendering, compile defaults, translation and output preferences must not
    fork the identity of an otherwise identical live/CLI/diff-page check input.
    """
    keys = ("checks", "knowledge", "semantic", "macros", "source", "project")
    return {key: deepcopy(configuration[key]) for key in keys if key in configuration}


@dataclass(frozen=True)
class ContentSnapshot:
    paper_id: str
    entry: str
    content_id: str
    files: Mapping[str, bytes]
    parsed: ParsedProject
    configuration: dict
    read_at: str

    @property
    def identity(self) -> dict:
        return {"paper_id": self.paper_id, "entry": self.entry,
                "content_id": self.content_id, "parser_version": PARSER_VERSION,
                "files": {name: sha256(raw).hexdigest() for name, raw in sorted(self.files.items())}}


def snapshot_from_project(project, *, raw_files=None, configuration=None, include_title=False):
    """Freeze an existing parse without re-reading disk or parsing it again.

    Callers lacking capture-time resource bytes get explicit incomplete coverage,
    rather than pretending the parser's mutable-disk bibliography is verified.
    Compilation metadata belongs to legacy rendering, not canonical source facts.
    A fresh clone also drops any cached numbering from the renderer's project.
    """
    project = replace(project, compiled_numbering={})
    expanded = project.expanded
    ident = paper_identity(expanded.source.worktree_origin or expanded.source.root, expanded.source.entry)
    raw = {name: text.encode("utf-8") for name, text in expanded.source_map.files.items()}
    raw.update(raw_files or {})
    for name, text in expanded.source_map.files.items():
        if raw[name].decode("utf-8") != text:
            raise ValueError("capture bytes disagree with ParsedProject")
    config = canonical_check_configuration(configuration if configuration is not None else expanded.source.configuration or {})
    missing = sorted({d.file for d in expanded.dependencies} - raw.keys())
    if missing:
        config["_snapshot_unverified_dependencies"] = missing
    content_id = identity_hash({"paper_id": ident["paper_id"], "entry": ident["entry"],
                                "files": {n: sha256(b).hexdigest() for n, b in sorted(raw.items())},
                                "expansion": _expansion_identity(expanded),
                                "configuration": config, "parser_version": PARSER_VERSION,
                                "include_title": include_title})
    return ContentSnapshot(ident["paper_id"], ident["entry"], content_id, MappingProxyType(raw),
                           project, config, datetime.now(timezone.utc).isoformat())


def _raw_files(expanded):
    names = set(expanded.source_map.files) | {d.file for d in expanded.dependencies}
    result = {}
    for name in sorted(names):
        path = (expanded.source.root / name).resolve()
        if not path.is_relative_to(expanded.source.root):
            raise SourceError("source_changed", "new", "依赖路径已离开项目根目录", name)
        result[name] = path.read_bytes()
    return result


def _expansion_identity(expanded):
    return {"text": expanded.text,
            "segments": [asdict(s) for s in expanded.source_map.segments],
            "dependencies": [asdict(d) for d in expanded.dependencies],
            "diagnostics": [asdict(d) for d in expanded.diagnostics],
            "external_labels": expanded.external_labels,
            "external_prefixes": expanded.external_prefixes,
            "uncertain_external_prefixes": expanded.uncertain_external_prefixes}


def capture_snapshot(entry, *, root=None, configuration=None, retries=2, source=None, include_title=True) -> ContentSnapshot:
    """Capture a parsed, byte-verified current-disk snapshot."""
    return _capture(entry, root=root, configuration=configuration, retries=retries,
                    source=source, include_title=include_title, parse=True)


def capture_content_id(entry, *, root=None, configuration=None, retries=2, source=None, include_title=True) -> str:
    """Verify the same complete input identity without parsing it a second time.

    Both expansion/byte checks, include-graph discovery, profile checks, parser
    version and title policy are identical to capture_snapshot. Only a caller
    needing an identity (not a new document) should use this verification path.
    """
    return _capture(entry, root=root, configuration=configuration, retries=retries,
                    source=source, include_title=include_title, parse=False)


def _capture(entry, *, root, configuration, retries, source, include_title, parse):
    """Expand with the existing reader and optionally parse per capture attempt.

    Re-expand and byte-verify after parsing (including bibliography reads). This also
    detects a formerly missing dependency appearing, conditional includes changing,
    and repeated includes read inconsistently. No source directories are written.
    """
    if retries < 0:
        raise ValueError("retries must be nonnegative")
    path = Path(entry).expanduser()
    if source is None:
        root = Path(root).expanduser().resolve() if root else path.resolve().parent
        ident = paper_identity(root, path if path.is_absolute() else root / path, require_entry=True)
        config = deepcopy(dict(configuration or {}))
        source = ProjectSource("new", root, ident["entry"], ComparisonSource("directory", str(root)), configuration=config)
    else:
        if Path(entry).as_posix() != source.entry:
            raise ValueError("entry must match supplied ProjectSource")
        root = source.root
        config = deepcopy(dict(source.configuration or {}) if configuration is None else dict(configuration))
        source = replace(source, configuration=config)
        ident = paper_identity(source.worktree_origin or root, source.entry,
                               require_entry=source.worktree_origin is None)
    last = None
    for _ in range(retries + 1):
        try:
            from ..project_profile import settings
            effective = settings(source)
            expanded = source.expand()
            raw = _raw_files(expanded)
            for name, text in expanded.source_map.files.items():
                if raw[name].decode("utf-8") != text:
                    raise ValueError("source changed during expansion")
            for segment in expanded.source_map.segments:
                if expanded.text[segment.expanded_start:segment.expanded_end] != expanded.source_map.files[segment.file][segment.source_start:segment.source_end]:
                    raise ValueError("source changed between include instances")
            macros = validate_macros(effective.get("macros", {}))
            parsed = parse_project(expanded, macros=macros, include_title=include_title) if parse else None
            verification = source.expand()
            if (effective != settings(source) or raw != _raw_files(verification) or
                    _expansion_identity(expanded) != _expansion_identity(verification)):
                raise ValueError("source changed during parsing")
            check_config = canonical_check_configuration(effective)
            content_id = identity_hash({"paper_id": ident["paper_id"], "entry": ident["entry"],
                                        "files": {n: sha256(b).hexdigest() for n, b in raw.items()},
                                        "expansion": _expansion_identity(expanded),
                                        "configuration": check_config, "parser_version": PARSER_VERSION,
                                        "include_title": include_title})
            if not parse:
                return content_id
            return ContentSnapshot(ident["paper_id"], ident["entry"], content_id,
                                   MappingProxyType(raw), parsed, check_config,
                                   datetime.now(timezone.utc).isoformat())
        except (OSError, UnicodeError, ValueError, SourceError) as exc:
            last = exc
    raise SourceError("source_changed", "new", f"无法取得一致源码快照：{last}", ident["entry"]) from last
