"""Archive managed reports at generation boundaries, independently of service health."""
from pathlib import Path

from .local_history import History
from .local_state import read_json, write_json


def marker_path(report):
    report = Path(report).resolve()
    return report.parent / ('.' + report.name + '.history.json')


def capture_report(state, paper_id, report, request, reason):
    state = Path(state)
    error_path = state / 'history-errors' / (paper_id + '.json')
    try:
        result = History(state).capture(paper_id, Path(report), request, reason)
        error_path.unlink(missing_ok=True)
        return result
    except Exception as exc:
        # A history failure must never turn a successful source edit or service
        # replacement into a failed operation. Keep its separate retry signal.
        return history_failure(state, paper_id, report, reason, exc)


def history_failure(state, paper_id, report, reason, error):
    try:
        write_json(Path(state) / 'history-errors' / (paper_id + '.json'),
                   {'paper_id': paper_id, 'message': str(error),
                    'directory': str(report), 'reason': reason})
    except OSError:
        pass
    return {'paper_id': paper_id, 'error': str(error)}


def capture_managed_report(report):
    marker = read_json(marker_path(report))
    if marker:
        return capture_report(marker['state'], marker['paper_id'], report,
                              marker['request'], '正文重建')
    return None
