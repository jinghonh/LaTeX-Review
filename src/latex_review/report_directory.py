"""Staged report-directory updates; callers hold locks and validate content."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
import uuid


@dataclass
class ReportCandidate:
    directory: Path
    previous_report: Path | None = None


@contextmanager
def report_directory(output, *, keep_previous=False):
    """Commit on normal exit; discard on failure, restoring the old report.

    The output parent must exist. Build and validate in candidate.directory.
    previous_report is available after a successful retained-backup commit.
    A failed rollback leaves the backup intact and reports its recovery path.
    """
    output = Path(output)
    candidate = ReportCandidate(Path(tempfile.mkdtemp(
        prefix='.' + output.name + '.stage-', dir=output.parent)))
    backup = output.parent / ('.' + output.name + '.before-' + uuid.uuid4().hex)
    try:
        yield candidate
        had_output = output.exists()
        if had_output:
            os.replace(output, backup)
        try:
            os.replace(candidate.directory, output)
        except BaseException:
            if had_output:
                try:
                    os.replace(backup, output)
                except OSError as exc:
                    raise OSError(f'报告目录回退失败；旧报告保留在 {backup}：{exc}') from exc
            raise
        if had_output:
            if keep_previous:
                candidate.previous_report = backup
            else:
                shutil.rmtree(backup)
    finally:
        if candidate.directory.exists():
            shutil.rmtree(candidate.directory)
