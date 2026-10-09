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
# basic tier: fal.ai TRELLIS (open source) · quick tier: Tripo
TRIPO_API_KEY = os.getenv("TRIPO_API_KEY", "").strip()   # pasted keys often carry a stray newline
TRIPO_MODEL = os.getenv("TRIPO_MODEL", "v3.1-20260211")
TRIPO_BASE = os.getenv("TRIPO_BASE", "https://openapi.tripo3d.ai/v3")
TRIPO_FACE_LIMIT = int(os.getenv("TRIPO_FACE_LIMIT", "300000"))
TRIPO_TEXTURE_QUALITY = os.getenv("TRIPO_TEXTURE_QUALITY", "detailed")   # or "standard" (cheaper)
# Printable size for STL exports (largest dimension, mm). Photo scans carry no real-world scale.
STL_SIZE_MM = float(os.getenv("STL_SIZE_MM", "100"))
# Which Tripo view slot each quarter of the walk-around fills, in capture order.
TRIPO_VIEW_ORDER = os.getenv("TRIPO_VIEW_ORDER", "front,left,back,right")
FAL_KEY = os.getenv("FAL_KEY", "").strip()   # pasted keys often carry a stray newline
# High-accuracy tier: KIRI Engine
KIRI_API_KEY = os.getenv("KIRI_API_KEY", "").strip()   # pasted keys often carry a stray newline
KIRI_SCAN = os.getenv("KIRI_SCAN", "photo")                # "photo" or "featureless" (shiny objects)
KIRI_MODEL_QUALITY = int(os.getenv("KIRI_MODEL_QUALITY", "0"))      # 0 high, 1 medium, 2 low, 3 ultra
KIRI_TEXTURE_QUALITY = int(os.getenv("KIRI_TEXTURE_QUALITY", "1"))  # 0 4K, 1 2K, 2 1K, 3 8K
DEFAULT_TIER = os.getenv("DEFAULT_TIER", "quick")

# Play product per tier ("tier=product,…"). Service tiers are bought before processing;
# the self-hosted "diy" pipeline keeps pay-to-export.
TIER_PRODUCTS = dict(
    pair.split("=", 1)
    for pair in os.getenv(
        "TIER_PRODUCTS", "basic=scan_basic,quick=scan_quick,hq=scan_hq,diy=export_unlock"
    ).split(",")
    if "=" in pair
)
EXPORT_PRODUCT_IDS |= set(TIER_PRODUCTS.values())


# Price label shown to customers per option ("tier=label,…"). Placeholders: set these to match
# the prices of the Play products above.
TIER_PRICES = dict(
    pair.split("=", 1)
    for pair in os.getenv("TIER_PRICES", "basic=₹29,quick=₹99,hq=₹249").split(",")
    if "=" in pair
)


# ---------- points (1 point = ₹1 by default; all values are placeholders) ----------
def _pairs(env, default):
    return dict(p.split("=", 1) for p in os.getenv(env, default).split(",") if "=" in p)


# Points each option costs.
TIER_POINTS = {k: int(v) for k, v in _pairs("TIER_POINTS", "basic=29,quick=99,hq=249").items()}
# Point packs sold through Google Play: product id -> points, and the price label shown.
POINT_PACKS = {k: int(v) for k, v in _pairs(
    "POINT_PACKS", "points_100=100,points_300=300,points_1000=1000").items()}
PACK_PRICES = _pairs("PACK_PRICES", "points_100=₹99,points_300=₹279,points_1000=₹849")


# Free points for a new account (0 = none).
WELCOME_POINTS = int(os.getenv("WELCOME_POINTS", "0"))


def points_for_tier(tier: str | None) -> int:
    return TIER_POINTS.get(tier or "", 0)


def price_for_tier(tier: str | None) -> str:
    return TIER_PRICES.get(tier or "", "")


def product_for_tier(tier: str | None) -> str:
    return TIER_PRODUCTS.get(tier or "diy", "export_unlock")


# Google service account
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SERVICE_ACCOUNT_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "")

# Dev ONLY
DEV_BILLING = os.getenv("ALLOW_DEV_BILLING", "0")

# Photo and model storage (see storage.py). Unset R2_BUCKET = keep files on the server's disk.
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")          # https://<account id>.r2.cloudflarestorage.com
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_KEY_ID = os.getenv("R2_KEY_ID", "")
R2_SECRET = os.getenv("R2_SECRET", "")
R2_REGION = os.getenv("R2_REGION", "auto")
# Photos and models are deleted this many days after a scan finishes (privacy notice promise).
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))

# Privacy notice (GET /privacy). Bump PRIVACY_VERSION when the notice changes: the app asks
# customers to agree again.
PRIVACY_VERSION = os.getenv("PRIVACY_VERSION", "2026-10")
PRIVACY_COMPANY = os.getenv("PRIVACY_COMPANY", "ScanForge")
PRIVACY_CONTACT_EMAIL = os.getenv("PRIVACY_CONTACT_EMAIL", "")

# Database backups (see backup.py). 0 hours turns the daily backup off.
BACKUP_EVERY_HOURS = float(os.getenv("BACKUP_EVERY_HOURS", "24"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))
# Off-site copies go to BACKUP_S3_* if set, otherwise to the R2 bucket used for files.
BACKUP_S3_ENDPOINT = os.getenv("BACKUP_S3_ENDPOINT", "") or R2_ENDPOINT
BACKUP_S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "") or R2_BUCKET
BACKUP_S3_KEY_ID = os.getenv("BACKUP_S3_KEY_ID", "") or R2_KEY_ID
BACKUP_S3_SECRET = os.getenv("BACKUP_S3_SECRET", "") or R2_SECRET

# "production" turns on start-up safety checks (see main.startup).
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


def production_problems() -> list[str]:
    """Settings that must never reach real customers."""
    if ENVIRONMENT != "production":
        return []
    problems = []
    if DEV_BILLING == "1":
        problems.append("ALLOW_DEV_BILLING=1 would let anyone add points without paying")
    if SECRET_IS_DEFAULT:
        problems.append("SECRET_KEY is the default, so signed download links can be forged")
    if not (SERVICE_ACCOUNT_JSON or SERVICE_ACCOUNT_PATH):
        problems.append("no Google service account, so Play purchases can't be verified")
    if not API_BASE.startswith("https://"):
        problems.append("API_BASE must be the public https:// address")
    if not PRIVACY_CONTACT_EMAIL:
        problems.append("PRIVACY_CONTACT_EMAIL must be set: the privacy notice has to say whom to contact")
    return problems
