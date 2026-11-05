"""Writing checks: independent JSON results, no automatic source modifications."""
from .document import CheckDocument, build_check_document
from .engine import evaluate_current
from .model import compare_reports, validate_report
from .snapshot import ContentSnapshot, capture_snapshot, snapshot_from_project, canonical_check_configuration

__all__ = ["CheckDocument", "ContentSnapshot", "build_check_document", "capture_snapshot",
           "snapshot_from_project", "canonical_check_configuration", "evaluate_current", "compare_reports", "validate_report"]
