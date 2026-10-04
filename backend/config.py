import os
from pathlib import Path

BASE_DIR = Path(os.getenv("DATA_DIR", "./data"))
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "app.db"

# HMAC secret for signed photo/result/download URLs and the worker manifest key
_DEFAULT_SECRET = "change-me-in-production"
SECRET = os.getenv("SECRET_KEY", _DEFAULT_SECRET)
SECRET_IS_DEFAULT = SECRET == _DEFAULT_SECRET

# Public URL of this backend
API_BASE = os.getenv("API_BASE", "")

# RunPod serverless
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT = os.getenv("RUNPOD_ENDPOINT_ID", "")

# Signed photo-fetch window in seconds
PHOTO_FETCH_TTL = int(os.getenv("PHOTO_FETCH_TTL", "3600"))

# Google Play
PLAY_PACKAGE = os.getenv("PLAY_PACKAGE_NAME", "com.scanforge.app")
# Comma-separated in-app product ids that unlock an export
EXPORT_PRODUCT_IDS = {
    p.strip() for p in os.getenv("EXPORT_PRODUCT_IDS", "export_unlock").split(",") if p.strip()
}

# Upload limits
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "200"))
MAX_PHOTO_BYTES = int(os.getenv("MAX_PHOTO_MB", "25")) * 1024 * 1024
MIN_PHOTOS = int(os.getenv("MIN_PHOTOS", "8"))

# How long a RunPod job may run before it is marked failed (seconds)
PROCESSING_TIMEOUT = int(os.getenv("PROCESSING_TIMEOUT", "1800"))

# Google service account
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SERVICE_ACCOUNT_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "")

# Dev ONLY
DEV_BILLING = os.getenv("ALLOW_DEV_BILLING", "0")
