"""Durable, versioned SQLite runs; locks are shared by CLI and service callers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import uuid

from .model import canonical_json, validate_report
from ..local_paths import state_directory

STORE_VERSION = 1


def default_store_path(paper_id):
    return state_directory() / "checks" / paper_id / "checks.sqlite3"


class CheckStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock("migration"):
            with self._connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > STORE_VERSION:
                    raise ValueError(f"unsupported checks database version {version}")
                connection.execute("BEGIN EXCLUSIVE")
                if version == 0:
                    for statement in (
                        "CREATE TABLE runs (sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE NOT NULL, paper_id TEXT NOT NULL, input_id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, report_json TEXT, error_json TEXT)",
                        "CREATE INDEX runs_paper ON runs(paper_id, sequence)",
                        "CREATE TABLE finding_instances (run_id TEXT NOT NULL REFERENCES runs(run_id), finding_key TEXT NOT NULL, finding_json TEXT NOT NULL, PRIMARY KEY(run_id, finding_key))",
                        "CREATE TABLE candidates (candidate_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL, payload_json TEXT NOT NULL)",
                        "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT NOT NULL, at TEXT NOT NULL, payload_json TEXT NOT NULL)",
                        "CREATE TABLE current_runs (paper_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id))",
                    ):
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version={STORE_VERSION}")
                    self._event(connection, None, "migration", {"from": 0, "to": STORE_VERSION})

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _lock(self, name):
        path = self.path.with_name(self.path.name + "." + name + ".lock")
        with path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def paper_lock(self, paper_id):
        if not isinstance(paper_id, str) or len(paper_id) != 64 or any(c not in "0123456789abcdef" for c in paper_id):
            raise ValueError("invalid paper_id")
        return self._lock(paper_id)

    @staticmethod
    def _event(connection, run_id, kind, payload):
        connection.execute("INSERT INTO events(run_id,kind,at,payload_json) VALUES(?,?,?,?)",
                           (run_id, kind, datetime.now(timezone.utc).isoformat(), canonical_json(payload)))

    def begin_run(self, paper_id, input_id=None, *, run_id=None):
        run_id = run_id or uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("INSERT INTO runs(run_id,paper_id,input_id,status,started_at) VALUES(?,?,?,'running',?)",
                               (run_id, paper_id, input_id, datetime.now(timezone.utc).isoformat()))
            self._event(connection, run_id, "started", {})
        return run_id

    def save_report(self, report, *, run_id=None, make_current=True):
        validate_report(report)
        paper_id = report["input"]["paper_id"]
        run_id = run_id or self.begin_run(paper_id, report["input_id"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None or run["paper_id"] != paper_id or run["status"] != "running" or run["input_id"] not in (None, report["input_id"]):
                raise ValueError("run identity/state conflict")
            connection.execute("UPDATE runs SET input_id=?,status=?,finished_at=?,report_json=? WHERE run_id=?",
                               (report["input_id"], report["status"] if make_current else "stale", datetime.now(timezone.utc).isoformat(), canonical_json(report), run_id))
            for finding in report["findings"]:
                connection.execute("INSERT INTO finding_instances VALUES(?,?,?)", (run_id, finding["finding_key"], canonical_json(finding)))
            newest = connection.execute("SELECT run_id FROM runs WHERE paper_id=? ORDER BY sequence DESC LIMIT 1", (paper_id,)).fetchone()[0]
            if newest == run_id and make_current:
                connection.execute("INSERT INTO current_runs VALUES(?,?) ON CONFLICT(paper_id) DO UPDATE SET run_id=excluded.run_id", (paper_id, run_id))
            self._event(connection, run_id, "finished", {"status": report["status"]})
        return run_id

    def finish_failed(self, run_id, error, *, status="failed"):
        if status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("invalid terminal status")
        with self._connect() as connection:
            cursor = connection.execute("UPDATE runs SET status=?,finished_at=?,error_json=? WHERE run_id=? AND status='running'",
                                        (status, datetime.now(timezone.utc).isoformat(), canonical_json(error), run_id))
            if cursor.rowcount:
                self._event(connection, run_id, status, error)

    def recover_interrupted(self, paper_id, *, exclude=()):
        """Call only while holding paper_lock, never interrupt another process."""
        with self._connect() as connection:
            rows = connection.execute("SELECT run_id FROM runs WHERE paper_id=? AND status='running'", (paper_id,)).fetchall()
            rows = [row for row in rows if row[0] not in exclude]
            for row in rows:
                connection.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE run_id=?", (datetime.now(timezone.utc).isoformat(), row[0]))
                self._event(connection, row[0], "interrupted", {})
        return len(rows)

    def get_run(self, run_id):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        report_json, error_json = value.pop("report_json"), value.pop("error_json")
        value["report"] = json.loads(report_json) if report_json else None
        value["error"] = json.loads(error_json) if error_json else None
        return value

    def current(self, paper_id):
        with self._connect() as connection:
            row = connection.execute("SELECT run_id FROM current_runs WHERE paper_id=?", (paper_id,)).fetchone()
        return self.get_run(row[0]) if row else None

    def latest(self, paper_id, *, successful=True):
        with self._connect() as connection:
            row = connection.execute("SELECT run_id FROM runs WHERE paper_id=? AND status IN " +
                                     ("('completed')" if successful else "('completed','partial')") + " ORDER BY sequence DESC LIMIT 1", (paper_id,)).fetchone()
        return self.get_run(row[0]) if row else None
