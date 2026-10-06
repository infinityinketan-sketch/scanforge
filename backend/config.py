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

# ---------- 3D services (see providers.py) ----------
# Quick tier: Tripo (default when its key is set) or fal.ai TRELLIS (open source, cheapest)
TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "")
TRIPO_MODEL = os.getenv("TRIPO_MODEL", "v3.1-20260211")
# Which Tripo view slot each quarter of the walk-around fills, in capture order.
TRIPO_VIEW_ORDER = os.getenv("TRIPO_VIEW_ORDER", "front,left,back,right")
FAL_KEY = os.getenv("FAL_KEY", "")
QUICK_PROVIDER = os.getenv("QUICK_PROVIDER", "")          # "", "tripo" or "fal_trellis"
# High-accuracy tier: KIRI Engine
KIRI_API_KEY = os.getenv("KIRI_API_KEY", "")
KIRI_SCAN = os.getenv("KIRI_SCAN", "photo")                # "photo" or "featureless" (shiny objects)
KIRI_MODEL_QUALITY = int(os.getenv("KIRI_MODEL_QUALITY", "0"))      # 0 high, 1 medium, 2 low, 3 ultra
KIRI_TEXTURE_QUALITY = int(os.getenv("KIRI_TEXTURE_QUALITY", "1"))  # 0 4K, 1 2K, 2 1K, 3 8K
DEFAULT_TIER = os.getenv("DEFAULT_TIER", "quick")

# Export unlock product per tier ("tier=product,…"), so a high-accuracy scan can cost more.
TIER_PRODUCTS = dict(
    pair.split("=", 1)
    for pair in os.getenv("TIER_PRODUCTS", "quick=export_unlock,hq=export_unlock_hq,diy=export_unlock").split(",")
    if "=" in pair
)
EXPORT_PRODUCT_IDS |= set(TIER_PRODUCTS.values())


def product_for_tier(tier: str | None) -> str:
    return TIER_PRODUCTS.get(tier or "diy", "export_unlock")


# Google service account
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SERVICE_ACCOUNT_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "")

# Dev ONLY
DEV_BILLING = os.getenv("ALLOW_DEV_BILLING", "0")
