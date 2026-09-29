"""按来源依赖指纹复用结构解析；缓存文件只含经校验的 JSON。"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile

from .contract import Diagnostic, ReviewNode, SourceLocation
from .source_map import MappedRange, OriginRange
from .sources import ExpandedProject, SourceError
from .structure import ParsedNode, ParsedProject, parse_project


CACHE_FORMAT = 1
_PARSER_FILES = ("sources.py", "source_map.py", "structure.py", "matching.py", "comparison.py",
                 "structured_diff.py", "text_diff.py", "contract.py")
_RENDERER_FILES = ("preview.py", "report.py")


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_envelope(path: Path, key: str) -> dict:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("缓存信封无效")
    parsed = envelope.get("parsed")
    if (envelope.get("format") != CACHE_FORMAT or envelope.get("key") != key or
            not isinstance(parsed, dict) or not isinstance(parsed.get("nodes"), list) or
            not isinstance(parsed.get("diagnostics"), list) or not isinstance(parsed.get("labels"), dict) or
            envelope.get("checksum") != _digest(_json(parsed))):
        raise ValueError("缓存校验失败")
    return envelope


def is_managed_cache_file(path: Path) -> bool:
    """清理前确认文件内容确实是与文件名匹配的完整缓存信封。"""
    try:
        _read_envelope(path, path.stem)
        return True
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def implementation_versions() -> dict[str, str]:
    root = Path(__file__).parent
    return {"parser": _digest(_json({name: _digest((root / name).read_bytes()) for name in _PARSER_FILES})),
            "renderer": _digest(_json({name: _digest((root / name).read_bytes()) for name in _RENDERER_FILES})),
            "plastex": version("plasTeX")}


def dependency_fingerprints(expanded: ExpandedProject) -> dict[str, str]:
    """从当前快照读取全部实际依赖；缺失候选由展开诊断和正文键覆盖。"""
    files = {expanded.source.entry, *(dep.file for dep in expanded.dependencies)}
    result = {}
    root = expanded.source.root.resolve()
    for name in sorted(files):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise SourceError("dependency_outside_root", expanded.source.side, f"依赖越出项目目录：{name}", name)
        try:
            result[name] = _digest(path.read_bytes())
        except OSError as exc:
            raise SourceError("source_changed", expanded.source.side, f"快照依赖在处理期间不可读：{name}", name) from exc
        if name in expanded.source_map.files and result[name] != _digest(expanded.source_map.files[name].encode("utf-8")):
            raise SourceError("source_changed", expanded.source.side, f"源码在展开期间变化：{name}", name)
    return result


def _location(data: dict | None) -> SourceLocation | None:
    return SourceLocation(**data) if data is not None else None


def _diagnostic(data: dict) -> Diagnostic:
    return Diagnostic(data["code"], data["severity"], data["message"],
                      _location(data.get("source_old")), _location(data.get("source_new")))


def _parsed_from_json(expanded: ExpandedProject, data: dict) -> ParsedProject:
    nodes = []
    for item in data["nodes"]:
        review_data = item["review"]
        review_data["source"] = _location(review_data["source"])
        for field in ("child_ids", "section_path"):
            review_data[field] = tuple(review_data[field])
        review = ReviewNode(**review_data)
        if (not 0 <= item["expanded_start"] <= item["expanded_end"] <= len(expanded.text) or
                review.raw_latex != expanded.text[item["expanded_start"]:item["expanded_end"]]):
            raise ValueError("缓存节点与来源不一致")
        origins = tuple(MappedRange(origin["expanded_start"], origin["expanded_end"],
                                    OriginRange(**origin["origin"])) for origin in item["origins"])
        nodes.append(ParsedNode(review, item["expanded_start"], item["expanded_end"], origins,
                                tuple(item["labels"]), tuple(item["citations"]),
                                tuple(item["references"]), tuple(item["assets"]),
                                tuple(_diagnostic(value) for value in item["diagnostics"])))
    return ParsedProject(expanded, tuple(nodes), tuple(_diagnostic(value) for value in data["diagnostics"]),
                         dict(data["labels"]))


def _parse_dependencies(expanded: ExpandedProject) -> list[str]:
    """沿包含边从入口遍历；图片和文献不改变结构树，可独立使审阅键失效。"""
    edges: dict[str, set[str]] = {}
    for dependency in expanded.dependencies:
        if dependency.kind == "include":
            edges.setdefault(dependency.referenced_from, set()).add(dependency.file)
    pending = [expanded.source.entry]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(edges.get(current, ()))
    return sorted(visited)


class ParseCache:
    def __init__(self, directory: Path, *, enabled: bool = True, config: dict | None = None):
        self.directory = directory
        self.enabled = enabled
        self.config = config or {}
        self.versions = implementation_versions()
        self.events: list[dict] = []

    def parse(self, expanded: ExpandedProject, *, parser=parse_project) -> ParsedProject:
        fingerprints = dependency_fingerprints(expanded)
        graph = [{"kind": dep.kind, "file": dep.file, "from": dep.referenced_from,
                  "instance": dep.include_instance} for dep in expanded.dependencies]
        tex_files = _parse_dependencies(expanded)
        parse_input = {"format": CACHE_FORMAT, "versions": self.versions, "entry": expanded.source.entry,
                       "side": expanded.source.side, "config": self.config,
                       "files": {name: fingerprints[name] for name in tex_files},
                       "text": _digest(expanded.text.encode("utf-8")),
                       "segments": [asdict(segment) for segment in expanded.source_map.segments],
                       "diagnostics": [asdict(issue) for issue in expanded.diagnostics]}
        parse_key = _digest(_json(parse_input))
        review_key = _digest(_json({"parse": parse_key, "dependencies": fingerprints, "graph": graph}))
        path = self.directory / f"{parse_key}.json"
        state = "bypass" if not self.enabled else "miss"
        parsed = None
        if self.enabled:
            try:
                envelope = _read_envelope(path, parse_key)
                parsed = _parsed_from_json(expanded, envelope["parsed"])
                state = "hit"
            except FileNotFoundError:
                pass
            except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError, AttributeError):
                state = "corrupt"
        if parsed is None:
            parsed = parser(expanded)
            if self.enabled:
                payload = {"nodes": [asdict(node) for node in parsed.nodes],
                           "diagnostics": [asdict(diagnostic) for diagnostic in parsed.diagnostics],
                           "labels": parsed.labels}
                envelope = {"format": CACHE_FORMAT, "key": parse_key, "parsed": payload,
                            "checksum": _digest(_json(payload))}
                temporary = None
                try:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile("wb", dir=self.directory, prefix=".write-", suffix=".tmp",
                                                     delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(_json(envelope))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                except OSError:
                    state = "unavailable"
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
        self.events.append({"side": expanded.source.side, "source": asdict(expanded.source.identity),
                            "state": state, "parse_key": parse_key, "review_key": review_key,
                            "dependencies": fingerprints, "graph": graph})
        return parsed

    def metadata(self) -> dict:
        return {"format": CACHE_FORMAT, "enabled": self.enabled, "versions": self.versions,
                "sides": self.events}
