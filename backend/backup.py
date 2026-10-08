"""Daily database backups.

The database holds customers' points (money they paid), so it's copied once a day:
- a consistent snapshot via SQLite's online backup API (safe while the server is running),
- the newest BACKUP_KEEP copies kept in DATA_DIR/backups (protects against bad writes),
- uploaded off-site to S3-compatible storage when BACKUP_S3_* is set (protects against
  losing the disk). Photos and models are not backed up: they can be regenerated or rescanned.

Restore: stop the server, copy a backup over DATA_DIR/app.db, start the server.
"""
import gzip
import logging
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone

import config

log = logging.getLogger("scanforge.backup")
BACKUP_DIR = config.BASE_DIR / "backups"


def snapshot() -> "Path":  # noqa: F821
    """Write a gzipped, consistent copy of the database and return its path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    raw = BACKUP_DIR / f"app-{stamp}.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    gz = raw.with_suffix(".db.gz")
    with open(raw, "rb") as f, gzip.open(gz, "wb") as out:
        shutil.copyfileobj(f, out)
    raw.unlink()
    return gz


def prune(keep: int):
    copies = sorted(BACKUP_DIR.glob("app-*.db.gz"))
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)


def upload(path) -> bool:
    """Copy a backup to S3-compatible storage. Returns False when off-site backup isn't set up."""
    if not (config.BACKUP_S3_BUCKET and config.BACKUP_S3_KEY_ID and config.BACKUP_S3_SECRET):
        return False
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=config.BACKUP_S3_ENDPOINT or None,
        aws_access_key_id=config.BACKUP_S3_KEY_ID,
        aws_secret_access_key=config.BACKUP_S3_SECRET,
    )
    s3.upload_file(str(path), config.BACKUP_S3_BUCKET, f"scanforge/{path.name}")
    return True


def run_once() -> "Path":  # noqa: F821
    path = snapshot()
    prune(config.BACKUP_KEEP)
    offsite = upload(path)
    log.info("backup %s written%s", path.name, " and uploaded off-site" if offsite else " (local only)")
    return path


def _last_backup_age() -> float:
    copies = sorted(BACKUP_DIR.glob("app-*.db.gz"))
    return time.time() - copies[-1].stat().st_mtime if copies else float("inf")


def _loop():
    while True:
        try:
            if _last_backup_age() >= config.BACKUP_EVERY_HOURS * 3600:
                run_once()
        except Exception:  # noqa: BLE001 - a failed backup must never take the server down
            log.exception("backup failed")
        time.sleep(600)


def start():
    if config.BACKUP_EVERY_HOURS > 0:
        threading.Thread(target=_loop, name="backup", daemon=True).start()
