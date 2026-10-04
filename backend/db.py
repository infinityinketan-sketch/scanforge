import sqlite3
import threading
import time

from config import DB_PATH

_local = threading.local()


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _local.conn = c
    return c


def init():
    c = _conn()
    c.execute(
        """CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'created',
            created_at REAL,
            paid INTEGER DEFAULT 0,
            product_id TEXT,
            purchase_token TEXT,
            n_photos INTEGER DEFAULT 0,
            runpod_job TEXT,
            started REAL,
            finished REAL,
            error TEXT)"""
    )
    # A Play purchase token may unlock exactly one job.
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS jobs_purchase_token ON jobs(purchase_token) "
        "WHERE purchase_token IS NOT NULL"
    )
    c.commit()


def create_job(job_id):
    c = _conn()
    c.execute("INSERT INTO jobs(id, created_at) VALUES(?, ?)", (job_id, time.time()))
    c.commit()


def get_job(job_id):
    row = _conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def update_job(job_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    c = _conn()
    c.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
    c.commit()


def job_for_token(token):
    row = _conn().execute("SELECT id FROM jobs WHERE purchase_token=?", (token,)).fetchone()
    return row["id"] if row else None


def claim_status(job_id, from_statuses, to_status, **fields):
    """Atomically move a job between statuses; returns False if it was not in from_statuses."""
    marks = ",".join("?" for _ in from_statuses)
    sets = ", ".join(["status=?"] + [f"{k}=?" for k in fields])
    c = _conn()
    cur = c.execute(
        f"UPDATE jobs SET {sets} WHERE id=? AND status IN ({marks})",
        (to_status, *fields.values(), job_id, *from_statuses),
    )
    c.commit()
    return cur.rowcount == 1
