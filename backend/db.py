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
