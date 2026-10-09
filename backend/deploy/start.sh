#!/bin/sh
# Production entry point (Dockerfile CMD).
# With storage credentials, Litestream streams every database change off-site within seconds,
# and a server that starts with no database (new disk, lost disk) restores the latest copy first.
set -e
cd "$(dirname "$0")/.."            # the backend folder (main.py)
export DATA_DIR="${DATA_DIR:-/var/data}"
mkdir -p "$DATA_DIR"
APP="uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"
CONFIG="$PWD/deploy/litestream.yml"

# LITESTREAM_* if set, otherwise the R2 bucket used for photos and models.
export LITESTREAM_ENDPOINT="${LITESTREAM_ENDPOINT:-$R2_ENDPOINT}"
export LITESTREAM_BUCKET="${LITESTREAM_BUCKET:-$R2_BUCKET}"
export LITESTREAM_ACCESS_KEY_ID="${LITESTREAM_ACCESS_KEY_ID:-$R2_KEY_ID}"
export LITESTREAM_SECRET_ACCESS_KEY="${LITESTREAM_SECRET_ACCESS_KEY:-$R2_SECRET}"
export LITESTREAM_REGION="${LITESTREAM_REGION:-${R2_REGION:-auto}}"

if [ -n "$LITESTREAM_BUCKET" ] && [ -n "$LITESTREAM_ACCESS_KEY_ID" ] && command -v litestream >/dev/null; then
  if [ ! -f "$DATA_DIR/app.db" ]; then
    echo "No database on disk: restoring the latest off-site copy (if any)"
    litestream restore -config "$CONFIG" -if-replica-exists "$DATA_DIR/app.db"
  fi
  exec litestream replicate -config "$CONFIG" -exec "$APP"
fi

echo "WARNING: Litestream not configured; the database is only on this disk (plus daily backups)" >&2
exec $APP
