"""LaTeX Review 公共接口。"""

from .contract import (
    SCHEMA_VERSION,
    ChangeDetail,
    ComparisonSource,
    Diagnostic,
    DiagnosticsDocument,
    PrimaryChange,
    ReviewDocument,
    ReviewNode,
    SourceLocation,
    Summary,
    build_summary,
    dumps,
    validate_document,
)
from .source_map import MappedRange, OriginRange, SourceMap, SourceSegment
from .sources import Dependency, ExpandedProject, ProjectSource, SourceError, SourceIssue, SourcePair, expand_project, resolve_sources
from .structure import ParsedNode, ParsedProject, parse_project
from .preview import PreviewResult, render_preview

__all__ = [
    "SCHEMA_VERSION", "ChangeDetail", "ComparisonSource", "Diagnostic",
    "DiagnosticsDocument", "PrimaryChange", "ReviewDocument", "ReviewNode",
    "SourceLocation", "Summary", "build_summary", "dumps", "validate_document",
    "Dependency", "ExpandedProject", "ProjectSource", "SourceError", "SourceIssue", "SourcePair",
    "MappedRange", "OriginRange", "SourceMap", "SourceSegment", "expand_project", "resolve_sources",
    "ParsedNode", "ParsedProject", "PreviewResult", "parse_project", "render_preview",
]
