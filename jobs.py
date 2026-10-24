# language: Python, file: jobs.py, target: Python 3.10+
import json
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    account TEXT,
    password TEXT,
    proxy TEXT,
    result TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    submitted_by TEXT,
    batch_id TEXT,
    idx INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, idx);
"""


class JobStore:
    def __init__(self, db_path):
        self._path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or '.',
                    exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self._path, timeout=30.0,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, kind, account=None, password=None, proxy=None,
               submitted_by=None, batch_id=None, idx=None):
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute("""INSERT INTO jobs
                (id, kind, status, account, password, proxy,
                 created_at, updated_at, submitted_by, batch_id, idx)
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, kind, account, password, proxy,
                 now, now, submitted_by, batch_id, idx))
        return job_id

    def claim_next(self):
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE status='queued' "
                "ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE jobs SET status='running', updated_at=? "
                      "WHERE id=? AND status='queued'",
                      (time.time(), row['id']))
            return dict(row)

    def get(self, job_id):
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?",
                            (job_id,)).fetchone()
            return dict(row) if row else None

    def finish(self, job_id, result=None, error=None):
        status = 'failed' if error else 'done'
        with self._lock, self._conn() as c:
            c.execute("UPDATE jobs SET status=?, result=?, error=?, "
                      "updated_at=? WHERE id=?",
                      (status, json.dumps(result) if result else None,
                       error, time.time(), job_id))

    def cancel(self, job_id):
        with self._lock, self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='cancelled', updated_at=? "
                "WHERE id=? AND status IN ('queued','running')",
                (time.time(), job_id))
            return cur.rowcount > 0

    def batch_status(self, batch_id):
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT status, result FROM jobs WHERE batch_id=?",
                             (batch_id,)).fetchall()
        total = len(rows)
        counts = {'queued': 0, 'running': 0, 'done': 0, 'failed': 0,
                  'cancelled': 0}
        valid = invalid = errors = 0
        for r in rows:
            counts[r['status']] = counts.get(r['status'], 0) + 1
            if r['status'] == 'done' and r['result']:
                res = json.loads(r['result'])
                st = res.get('status')
                if st == 'valid':
                    valid += 1
                elif st == 'invalid':
                    invalid += 1
                else:
                    errors += 1
        return {'batch_id': batch_id, 'total': total, 'counts': counts,
                'valid': valid, 'invalid': invalid, 'errors': errors,
                'finished': (counts['done'] + counts['failed']
                             + counts['cancelled']) == total}

    def recover_orphans(self):
        with self._lock, self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='queued', updated_at=? "
                "WHERE status='running'", (time.time(),))
            return cur.rowcount