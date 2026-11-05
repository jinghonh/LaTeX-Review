"""Namespaced durable entry tasks and human finding reviews; never rewrite runs."""
import json
from datetime import datetime, timezone


class ServiceState:
    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS check_service_version (version INTEGER NOT NULL)')
            row = db.execute('SELECT version FROM check_service_version').fetchone()
            if row and row[0] != 1:
                raise ValueError('unsupported check service state version')
            if not row:
                db.execute('INSERT INTO check_service_version VALUES(1)')
            db.execute('CREATE TABLE IF NOT EXISTS check_service_tasks (run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS check_finding_reviews (sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, finding_key TEXT NOT NULL, input_id TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL)')

    def task(self, owner, value):
        with self.store._connect() as db:
            db.execute('INSERT INTO check_service_tasks VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state=excluded.state,payload=excluded.payload',
                       (value['run_id'], owner, value['state'], json.dumps(value, ensure_ascii=False)))

    def get_task(self, run_id):
        with self.store._connect() as db:
            row = db.execute('SELECT payload FROM check_service_tasks WHERE run_id=?', (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def recover(self, owner):
        with self.store._connect() as db:
            for row in db.execute("SELECT run_id,payload FROM check_service_tasks WHERE owner!=? AND state IN ('queued','running','cancelling')", (owner,)).fetchall():
                value = json.loads(row['payload'])
                value.update(state='interrupted', error={'code': 'interrupted', 'message': '本地入口已重启；任务未自动恢复'})
                db.execute("UPDATE check_service_tasks SET state='interrupted',payload=? WHERE run_id=?", (json.dumps(value, ensure_ascii=False), row['run_id']))

    def review(self, request_id):
        with self.store._connect() as db:
            row = db.execute('SELECT payload FROM check_finding_reviews WHERE request_id=?', (request_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_review(self, value):
        with self.store._connect() as db:
            db.execute('INSERT INTO check_finding_reviews(request_id,finding_key,input_id,payload,at) VALUES(?,?,?,?,?)',
                       (value['request_id'], value['finding_key'], value['input_id'], json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def overlay(self, report):
        with self.store._connect() as db:
            rows = db.execute('SELECT finding_key,payload FROM check_finding_reviews WHERE input_id=? ORDER BY sequence', (report['input_id'],)).fetchall()
        reviews = {row['finding_key']: json.loads(row['payload']) for row in rows}
        for finding in report['findings']:
            review = reviews.get(finding['finding_key'])
            if review:
                finding['review_state'] = {'confirm': 'confirmed', 'reject': 'rejected', 'waive': 'waived', 'defer': 'unreviewed'}[review['action']]
                if review['action'] == 'confirm':
                    finding['assessment'] = 'author_confirmed'
        return report
