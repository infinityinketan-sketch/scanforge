import os
from pathlib import Path

BASE_DIR = Path(os.getenv("DATA_DIR", "./data"))
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "app.db"

# HMAC secret for signed photo/result/download URLs
SECRET = os.getenv("SECRET_KEY", "change-me-in-production")

# Public URL of this backend
API_BASE = os.getenv("API_BASE", "")

# RunPod serverless
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_ENDPOINT = os.getenv("RUNPOD_ENDPOINT_ID", "")

# Signed photo-fetch window in seconds
PHOTO_FETCH_TTL = int(os.getenv("PHOTO_FETCH_TTL", "3600"))

# Google Play
PLAY_PACKAGE = os.getenv("PLAY_PACKAGE_NAME", "")

# Google service account
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SERVICE_ACCOUNT_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "")

# Dev ONLY
DEV_BILLING = os.getenv("ALLOW_DEV_BILLING", "0")
