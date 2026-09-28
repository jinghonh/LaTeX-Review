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

__all__ = [
    "SCHEMA_VERSION", "ChangeDetail", "ComparisonSource", "Diagnostic",
    "DiagnosticsDocument", "PrimaryChange", "ReviewDocument", "ReviewNode",
    "SourceLocation", "Summary", "build_summary", "dumps", "validate_document",
]
