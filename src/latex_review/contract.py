"""供解析、比较和报告模块共用的 1.0 数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from importlib.resources import files
import json
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Literal, Mapping

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "1.0"
Category = Literal["text", "equation", "figure", "table", "citation", "comment"]
Kind = Literal["added", "removed", "modified", "moved"]


@dataclass(frozen=True)
class SourceLocation:
    """原始源码位置；未知坐标以 null 和明确理由表示。"""

    file: str | None
    start_line: int | None
    end_line: int | None
    start_column: int | None = None
    end_column: int | None = None
    confidence: float = 1.0
    uncertainty_reason: str | None = None

    def __post_init__(self) -> None:
        if self.file is not None and (not self.file or PurePosixPath(self.file).is_absolute() or PureWindowsPath(self.file).drive or self.file.startswith("\\")):
            raise ValueError("源码文件必须是非空相对路径")
        if not 0 <= self.confidence <= 1:
            raise ValueError("源码定位置信度必须在 0 到 1 之间")
        if (self.file is None or self.start_line is None or self.end_line is None) and not self.uncertainty_reason:
            raise ValueError("源码位置不完整时必须提供不确定理由")
        if self.start_line is not None and self.end_line is not None and self.start_line > self.end_line:
            raise ValueError("源码起始行不得晚于结束行")


@dataclass(frozen=True)
class ComparisonSource:
    kind: Literal["git", "worktree", "directory", "file"]
    identifier: str


@dataclass(frozen=True)
class ReviewNode:
    id: str
    type: str
    raw_latex: str
    parent_id: str | None
    child_ids: tuple[str, ...]
    section_path: tuple[str, ...]
    source: SourceLocation
    normalized_latex: str | None = None
    plain_text: str | None = None


@dataclass(frozen=True)
class ChangeDetail:
    id: str
    category: Category
    kind: Kind
    old_text: str | None
    new_text: str | None
    summary: str
    source_old: SourceLocation | None = None
    source_new: SourceLocation | None = None


@dataclass(frozen=True)
class PrimaryChange:
    id: str
    kind: Kind
    node_type: str
    old_node_id: str | None
    new_node_id: str | None
    source_old: SourceLocation | None
    source_new: SourceLocation | None
    matching_confidence: float
    categories: tuple[Category, ...]
    details: tuple[ChangeDetail, ...]
    summary: str

    def __post_init__(self) -> None:
        if not 0 <= self.matching_confidence <= 1:
            raise ValueError("节点匹配置信度必须在 0 到 1 之间")
        if self.kind == "added" and (self.old_node_id is not None or self.source_old is not None or self.new_node_id is None or self.source_new is None):
            raise ValueError("新增主变更只能有新侧节点和位置")
        if self.kind == "removed" and (self.new_node_id is not None or self.source_new is not None or self.old_node_id is None or self.source_old is None):
            raise ValueError("删除主变更只能有旧侧节点和位置")
        if self.kind in ("modified", "moved") and (self.old_node_id is None or self.new_node_id is None or self.source_old is None or self.source_new is None):
            raise ValueError("修改和移动主变更必须有双侧节点和位置")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    source_old: SourceLocation | None = None
    source_new: SourceLocation | None = None


@dataclass(frozen=True)
class Summary:
    changes: int
    category_hits: Mapping[str, int]
    added_words: int
    removed_words: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "category_hits", MappingProxyType(dict(self.category_hits)))


@dataclass(frozen=True)
class ReviewDocument:
    entry: str
    old: ComparisonSource
    new: ComparisonSource
    nodes_old: tuple[ReviewNode, ...]
    nodes_new: tuple[ReviewNode, ...]
    changes: tuple[PrimaryChange, ...]
    diagnostics: tuple[Diagnostic, ...]
    summary: Summary
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class DiagnosticsDocument:
    diagnostics: tuple[Diagnostic, ...]
    schema_version: str = SCHEMA_VERSION


def build_summary(changes: tuple[PrimaryChange, ...], *, added_words: int = 0, removed_words: int = 0) -> Summary:
    """每项主变更计一次；类别取主变更与明细并集，各类别可重叠。"""
    ids = [change.id for change in changes]
    if len(ids) != len(set(ids)):
        raise ValueError("主变更 ID 不得重复")
    if min(added_words, removed_words) < 0:
        raise ValueError("词数不得为负")
    categories = {category for change in changes for category in (*change.categories, *(d.category for d in change.details))}
    hits = {category: sum(category in set((*change.categories, *(d.category for d in change.details))) for change in changes) for category in sorted(categories)}
    return Summary(len(changes), hits, added_words, removed_words)


def _ordered(value: object) -> object:
    if isinstance(value, ReviewDocument):
        result = {
            "schema_version": value.schema_version,
            "entry": value.entry,
            "old": asdict(value.old),
            "new": asdict(value.new),
            "summary": {
                "changes": value.summary.changes,
                "category_hits": dict(value.summary.category_hits),
                "added_words": value.summary.added_words,
                "removed_words": value.summary.removed_words,
            },
        }
        result["nodes_old"] = [asdict(node) for node in sorted(value.nodes_old, key=lambda n: n.id)]
        result["nodes_new"] = [asdict(node) for node in sorted(value.nodes_new, key=lambda n: n.id)]
        result["changes"] = []
        for change in sorted(value.changes, key=lambda c: c.id):
            item = asdict(change)
            item["details"] = []
            for detail in sorted(change.details, key=lambda d: d.id):
                detail_data = asdict(detail)
                for side in ("source_old", "source_new"):
                    if detail_data[side] is None:
                        del detail_data[side]
                item["details"].append(detail_data)
            item["categories"] = sorted(set(change.categories))
            result["changes"].append(item)
        result["diagnostics"] = [asdict(d) for d in sorted(value.diagnostics, key=lambda d: (d.code, d.message))]
        return result
    if isinstance(value, DiagnosticsDocument):
        return {"schema_version": value.schema_version, "diagnostics": [asdict(d) for d in sorted(value.diagnostics, key=lambda d: (d.code, d.message))]}
    if is_dataclass(value):
        return asdict(value)
    return value


def validate_document(value: ReviewDocument | DiagnosticsDocument | dict) -> None:
    """验证 JSON Schema 和跨字段计数规则。"""
    data = json.loads(json.dumps(_ordered(value), ensure_ascii=False))
    name = "diagnostics.schema.json" if isinstance(value, DiagnosticsDocument) or "entry" not in data else "diff.schema.json"
    schema = json.loads(files("latex_review").joinpath("schemas", name).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(data)
    if name == "diff.schema.json":
        for side in ("nodes_old", "nodes_new"):
            nodes = data[side]
            by_id = {node["id"]: node for node in nodes}
            if len(by_id) != len(nodes):
                raise ValueError(f"{side} 的节点 ID 不得重复")
            for node in nodes:
                parent_id = node["parent_id"]
                if parent_id is not None and (parent_id not in by_id or node["id"] not in by_id[parent_id]["child_ids"]):
                    raise ValueError(f"{side} 的父子节点关系不一致")
                for child_id in node["child_ids"]:
                    if child_id not in by_id or by_id[child_id]["parent_id"] != node["id"]:
                        raise ValueError(f"{side} 的子节点关系不一致")
        changes = data["changes"]
        ids = [change["id"] for change in changes]
        if len(ids) != len(set(ids)):
            raise ValueError("主变更 ID 不得重复")
        old_ids = {node["id"] for node in data["nodes_old"]}
        new_ids = {node["id"] for node in data["nodes_new"]}
        for change in changes:
            if change["old_node_id"] is not None and change["old_node_id"] not in old_ids:
                raise ValueError("主变更引用了不存在的旧侧节点")
            if change["new_node_id"] is not None and change["new_node_id"] not in new_ids:
                raise ValueError("主变更引用了不存在的新侧节点")
        if data["summary"]["changes"] != len(changes):
            raise ValueError("总数必须等于主变更数量")
        all_categories = {category for change in changes for category in (change["categories"] + [d["category"] for d in change["details"]])}
        if set(data["summary"]["category_hits"]) != all_categories:
            raise ValueError("分类命中项与主变更不一致")
        for category, count in data["summary"]["category_hits"].items():
            actual = sum(category in set(change["categories"] + [d["category"] for d in change["details"]]) for change in changes)
            if count != actual:
                raise ValueError(f"分类 {category} 命中数不一致")


def dumps(value: ReviewDocument | DiagnosticsDocument) -> str:
    validate_document(value)
    return json.dumps(_ordered(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
