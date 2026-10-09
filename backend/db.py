import sqlite3
import threading
import time

from config import DB_PATH

_local = threading.local()


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        # WAL: readers don't block the writer, and Litestream replicates from the WAL.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=10000")
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
    # Columns added after the first release; add them to existing databases.
    have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
    for col, decl in (("tier", "TEXT"), ("provider", "TEXT"), ("provider_task", "TEXT"),
                      ("progress", "INTEGER DEFAULT 0"), ("account_id", "TEXT"), ("cost", "INTEGER"),
                      ("purged", "INTEGER DEFAULT 0")):
        if col not in have:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
    # Customer accounts (one per app install for now) and their points ledger.
    c.execute(
        """CREATE TABLE IF NOT EXISTS accounts(
            id TEXT PRIMARY KEY,
            token_hash TEXT UNIQUE NOT NULL,
            created_at REAL)"""
    )
    have = {r["name"] for r in c.execute("PRAGMA table_info(accounts)")}
    for col, decl in (("consent_version", "TEXT"), ("consent_at", "REAL"), ("deleted_at", "REAL")):
        if col not in have:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {col} {decl}")
    c.execute("CREATE INDEX IF NOT EXISTS jobs_account ON jobs(account_id)")
    # Every change to a balance is a row: + purchase / refund / bonus, - scan.
    # The balance is always the sum, so it can't drift from the history the customer sees.
    c.execute(
        """CREATE TABLE IF NOT EXISTS ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            ref TEXT,
            note TEXT,
            created_at REAL)"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS ledger_account ON ledger(account_id, id)")
    # A purchase token credits once; a scan is charged once and refunded at most once.
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ledger_once ON ledger(kind, ref) WHERE ref IS NOT NULL")
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


def jobs_in(statuses, provider_only=True):
    marks = ",".join("?" for _ in statuses)
    q = f"SELECT * FROM jobs WHERE status IN ({marks})"
    if provider_only:
        q += " AND provider IS NOT NULL"
    return [dict(r) for r in _conn().execute(q + " ORDER BY created_at", tuple(statuses)).fetchall()]



# ---------- accounts and points ----------
_money = threading.Lock()   # one balance change at a time (single server process)


def create_account(account_id, token_hash):
    c = _conn()
    c.execute("INSERT INTO accounts(id, token_hash, created_at) VALUES(?, ?, ?)",
              (account_id, token_hash, time.time()))
    c.commit()


def account_for_token(token_hash):
    row = _conn().execute("SELECT id FROM accounts WHERE token_hash=? AND deleted_at IS NULL",
                          (token_hash,)).fetchone()
    return row["id"] if row else None


def get_account(account_id):
    row = _conn().execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row) if row else None


def record_consent(account_id, version):
    c = _conn()
    c.execute("UPDATE accounts SET consent_version=?, consent_at=? WHERE id=?",
              (version, time.time(), account_id))
    c.commit()


def jobs_for_account(account_id):
    return [dict(r) for r in _conn().execute(
        "SELECT * FROM jobs WHERE account_id=?", (account_id,)).fetchall()]


def close_account(account_id):
    """Revoke the account's token and delete its scans. Ledger rows stay: they are the record
    of payments, which accounting law requires us to keep. Returns the points forfeited."""
    with _money:
        c = _conn()
        left = balance(account_id)
        now = time.time()
        if left:
            c.execute("INSERT OR IGNORE INTO ledger(account_id, amount, kind, ref, note, created_at) "
                      "VALUES(?, ?, 'closed', ?, 'Account deleted', ?)", (account_id, -left, account_id, now))
        # The token hash must stay unique and unguessable once revoked.
        c.execute("UPDATE accounts SET deleted_at=?, token_hash='deleted:' || id WHERE id=?", (now, account_id))
        c.execute("DELETE FROM jobs WHERE account_id=?", (account_id,))
        c.commit()
        return left


def jobs_to_purge(cutoff):
    """Finished scans older than the retention period whose files haven't been deleted yet."""
    return [dict(r) for r in _conn().execute(
        "SELECT * FROM jobs WHERE created_at < ? AND COALESCE(purged, 0)=0 "
        "AND status NOT IN ('queued', 'processing')", (cutoff,)).fetchall()]


def balance(account_id):
    row = _conn().execute("SELECT COALESCE(SUM(amount), 0) AS b FROM ledger WHERE account_id=?",
                          (account_id,)).fetchone()
    return int(row["b"])


def history(account_id, limit=100):
    rows = _conn().execute(
        "SELECT id, amount, kind, ref, note, created_at FROM ledger WHERE account_id=? "
        "ORDER BY id DESC LIMIT ?", (account_id, limit)).fetchall()
    return [dict(r) for r in rows]


def add_entry(account_id, amount, kind, ref, note):
    """Record a credit or debit. Returns False if this (kind, ref) was already recorded."""
    with _money:
        c = _conn()
        try:
            c.execute("INSERT INTO ledger(account_id, amount, kind, ref, note, created_at) "
                      "VALUES(?, ?, ?, ?, ?, ?)", (account_id, amount, kind, ref, note, time.time()))
            c.commit()
            return True
        except sqlite3.IntegrityError:
            c.rollback()
            return False


def spend(account_id, amount, ref, note):
    """Debit points if the balance covers it. Returns (ok, balance_after_or_current)."""
    with _money:
        c = _conn()
        have = balance(account_id)
        if have < amount:
            return False, have
        try:
            c.execute("INSERT INTO ledger(account_id, amount, kind, ref, note, created_at) "
                      "VALUES(?, ?, 'scan', ?, ?, ?)", (account_id, -amount, ref, note, time.time()))
            c.commit()
        except sqlite3.IntegrityError:   # already charged for this scan
            c.rollback()
            return True, have
        return True, have - amount
