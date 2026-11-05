import sqlite3

import pytest

from latex_review.checks import capture_snapshot, build_check_document, evaluate_current
from latex_review.checks.store import CheckStore


def test_store_durable_historical_runs_and_recovery(tmp_path):
    path = tmp_path / 'main.tex'
    path.write_text(r'\ref{missing}')
    report = evaluate_current(build_check_document(capture_snapshot(path)))
    store_path = tmp_path / 'state' / 'checks.sqlite3'
    store = CheckStore(store_path)
    paper_id = report['input']['paper_id']
    with store.paper_lock(paper_id):
        run_id = store.save_report(report)
        pending = store.begin_run(paper_id)
        excluded = store.begin_run(paper_id)
        assert store.recover_interrupted(paper_id, exclude=[excluded]) == 1
        assert store.get_run(pending)['status'] == 'interrupted'
        store.finish_failed(excluded, {'code': 'cancelled'}, status='cancelled')
    reopened = CheckStore(store_path)
    assert reopened.get_run(run_id)['report'] == report
    assert reopened.latest(paper_id)['run_id'] == run_id
    with pytest.raises(ValueError):
        reopened.save_report(report, run_id=run_id)
    with sqlite3.connect(store_path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM finding_instances').fetchone()[0] == 1


def test_locks_serialize_independent_store_instances(tmp_path):
    from threading import Event, Thread
    first = CheckStore(tmp_path / 'checks.sqlite3')
    second = CheckStore(first.path)
    waiting, entered = Event(), Event()
    def contend():
        waiting.set()
        with second.paper_lock('a' * 64):
            entered.set()
    with first.paper_lock('a' * 64):
        thread = Thread(target=contend)
        thread.start()
        assert waiting.wait(2)
        assert not entered.wait(0.05)
    thread.join(2)
    assert entered.is_set() and not thread.is_alive()


def test_future_database_version_rejected_preserved(tmp_path):
    path = tmp_path / 'future.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA user_version=42')
    with pytest.raises(ValueError, match='version 42'):
        CheckStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 42


def test_old_async_completion_does_not_overwrite_current(tmp_path):
    path = tmp_path / 'main.tex'
    path.write_text('Text')
    report = evaluate_current(build_check_document(capture_snapshot(path)))
    store = CheckStore(tmp_path / 'checks.sqlite3')
    paper_id = report['input']['paper_id']
    old = store.begin_run(paper_id)
    new = store.begin_run(paper_id)
    store.save_report(report, run_id=new)
    store.save_report(report, run_id=old)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT run_id FROM current_runs').fetchone()[0] == new
