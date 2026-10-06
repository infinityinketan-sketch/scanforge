"""Third-party 3D reconstruction services, one per quality tier.

Every provider has the same three steps, all synchronous (they run in the background
processor thread, never in a request):

    submit(photos)   -> task      a string the provider needs to find the job again
    poll(task)       -> Poll      running (with progress), success (with a model URL) or failed
    fetch(url, dest) -> None      download the finished model as a GLB to `dest`

Tiers:
    quick  Tripo multiview-to-model (or fal.ai TRELLIS, open source): ~1–3 min, shape is AI-generated
    hq     KIRI Engine Photo Scan: real photogrammetry with background removal, ~$1/scan
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

import config

TIMEOUT = httpx.Timeout(60.0, connect=20.0)


@dataclass
class Poll:
    state: str            # "running" | "success" | "failed"
    progress: int = 0     # 0–100 when the provider reports it
    url: str = ""         # model download URL on success
    error: str = ""       # reason on failure


class ProviderError(Exception):
    """A permanent failure: the job should be marked failed with this message."""


# ---------- photo selection (generative services take only a few views) ----------
def _sharpness(img) -> float:
    from PIL import ImageFilter, ImageStat

    small = img.convert("L")
    small.thumbnail((256, 256))
    return ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).var[0]


def pick_views(photos: list[Path], n_views: int, max_side: int) -> list[bytes]:
    """Evenly spaced photos around the capture loop, taking the sharpest frame near each
    target position, downscaled to max_side and re-encoded as JPEG."""
    from PIL import Image, ImageOps

    out = []
    n = len(photos)
    for k in range(n_views):
        centre = round(k * n / n_views) % n
        window = [photos[(centre + d) % n] for d in (-2, -1, 0, 1, 2)] if n >= 10 else [photos[centre]]
        best, best_score = None, -1.0
        for p in dict.fromkeys(window):   # unique, order kept
            try:
                img = ImageOps.exif_transpose(Image.open(p))
                score = _sharpness(img)
            except Exception:  # noqa: BLE001 - skip unreadable files
                continue
            if score > best_score:
                best, best_score = img, score
        if best is None:
            raise ProviderError("could not read the photos")
        best = best.convert("RGB")
        best.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        best.save(buf, "JPEG", quality=92)
        out.append(buf.getvalue())
    return out


def _check(r: httpx.Response, service: str) -> dict:
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 401:
        raise ProviderError(f"{service}: API key rejected")
    if r.status_code in (402, 403) and service == "KIRI Engine":
        raise ProviderError("KIRI Engine: not enough credit")
    if r.status_code >= 400 or body.get("code") not in (0, None):
        msg = body.get("message") or body.get("msg") or r.text[:200]
        raise ProviderError(f"{service} error {r.status_code}/{body.get('code')}: {msg}")
    return body


# ---------- Tripo (quick tier) ----------
class Tripo:
    name = "tripo"
    base = "https://openapi.tripo3d.com/v3"

    def __init__(self, key: str):
        self.h = {"Authorization": f"Bearer {key}"}

    def submit(self, photos: list[Path]) -> str:
        # Capture order is a walk around the object moving to the user's right, so a quarter
        # of the way round the camera sees the object's left side.
        slots = [s.strip() for s in config.TRIPO_VIEW_ORDER.split(",")]
        views = pick_views(photos, len(slots), 2048)
        inputs = []
        with httpx.Client(timeout=TIMEOUT, headers=self.h) as c:
            for slot, jpg in zip(slots, views):
                r = c.post(f"{self.base}/files", files={"file": (f"{slot}.jpg", jpg, "image/jpeg")})
                inputs.append({slot: _check(r, "Tripo")["data"]["file_token"]})
            body = {
                "model": config.TRIPO_MODEL,
                "inputs": inputs,
                "texture": True,
                "pbr": True,
                "texture_quality": "standard",
            }
            r = c.post(f"{self.base}/generation/multiview-to-model", json=body)
            return _check(r, "Tripo")["data"]["task_id"]

    def poll(self, task: str) -> Poll:
        with httpx.Client(timeout=TIMEOUT, headers=self.h) as c:
            data = _check(c.get(f"{self.base}/tasks/{task}"), "Tripo")["data"]
        status = data.get("status")
        if status == "success":
            url = (data.get("output") or {}).get("model_url")
            if not url:
                return Poll("failed", error="Tripo finished without a model")
            return Poll("success", 100, url=url)
        if status in ("failed", "cancelled", "banned", "expired", "unknown"):
            return Poll("failed", error=f"Tripo task {status}")
        return Poll("running", int(data.get("progress") or 0))

    def fetch(self, url: str, dest: Path):
        _download(url, dest)


# ---------- fal.ai TRELLIS (open-source alternative for the quick tier) ----------
class FalTrellis:
    name = "fal_trellis"
    model = "fal-ai/trellis/multi"

    def __init__(self, key: str):
        self.h = {"Authorization": f"Key {key}"}

    def submit(self, photos: list[Path]) -> str:
        views = pick_views(photos, 4, 1024)
        urls = ["data:image/jpeg;base64," + base64.b64encode(v).decode() for v in views]
        with httpx.Client(timeout=TIMEOUT, headers=self.h) as c:
            r = c.post(f"https://queue.fal.run/{self.model}", json={"image_urls": urls})
            body = _check(r, "fal.ai")
        return json.dumps({"status_url": body["status_url"], "response_url": body["response_url"]})

    def poll(self, task: str) -> Poll:
        t = json.loads(task)
        with httpx.Client(timeout=TIMEOUT, headers=self.h) as c:
            st = _check(c.get(t["status_url"]), "fal.ai")
            if st.get("status") != "COMPLETED":
                return Poll("running")
            res = c.get(t["response_url"])
            if res.status_code >= 400:
                return Poll("failed", error=f"fal.ai: {res.text[:200]}")
            url = ((res.json() or {}).get("model_mesh") or {}).get("url")
        return Poll("success", 100, url=url) if url else Poll("failed", error="fal.ai returned no model")

    def fetch(self, url: str, dest: Path):
        _download(url, dest)


# ---------- KIRI Engine (high-accuracy tier) ----------
KIRI_STATUS = {-1: "uploading", 0: "processing", 1: "failed", 2: "success", 3: "queuing", 4: "expired"}
KIRI_MIN_PHOTOS = 20
KIRI_MAX_PHOTOS = 300


class Kiri:
    name = "kiri"
    base = "https://api.kiriengine.app/api/v1/open"

    def __init__(self, key: str):
        self.h = {"Authorization": f"Bearer {key}"}

    def submit(self, photos: list[Path]) -> str:
        if len(photos) < KIRI_MIN_PHOTOS:
            raise ProviderError(f"High-accuracy scans need at least {KIRI_MIN_PHOTOS} photos")
        photos = photos[:KIRI_MAX_PHOTOS]
        files = [("imagesFiles", (p.name, p.read_bytes(), "image/jpeg")) for p in photos]
        if config.KIRI_SCAN == "featureless":
            path, data = "featureless/image", {"fileFormat": "GLB"}
        else:
            path = "photo/image"
            data = {
                "modelQuality": config.KIRI_MODEL_QUALITY,
                "textureQuality": config.KIRI_TEXTURE_QUALITY,
                "fileFormat": "glb",
                "isMask": 1,            # automatic object masking: drops floor/table/background
                "textureSmoothing": 0,
            }
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0), headers=self.h) as c:
            r = c.post(f"{self.base}/{path}", files=files, data={k: str(v) for k, v in data.items()})
            return _check(r, "KIRI Engine")["data"]["serialize"]

    def poll(self, task: str) -> Poll:
        with httpx.Client(timeout=TIMEOUT, headers=self.h) as c:
            data = _check(c.get(f"{self.base}/model/getStatus", params={"serialize": task}), "KIRI Engine")["data"]
            state = KIRI_STATUS.get(data.get("status"), "processing")
            if state == "success":
                z = _check(c.get(f"{self.base}/model/getModelZip", params={"serialize": task}), "KIRI Engine")
                return Poll("success", 100, url=z["data"]["modelUrl"])
        if state in ("failed", "expired"):
            return Poll("failed", error=f"KIRI Engine scan {state}: try more photos with good overlap")
        return Poll("running")

    def fetch(self, url: str, dest: Path):
        """KIRI returns a zip; pull the GLB out of it (or convert another mesh format)."""
        raw = dest.with_suffix(".zip")
        _download(url, raw)
        with zipfile.ZipFile(raw) as z:
            names = z.namelist()
            glb = next((n for n in names if n.lower().endswith(".glb")), None)
            if glb:
                dest.write_bytes(z.read(glb))
            else:
                mesh = next((n for n in names if n.lower().endswith((".obj", ".gltf", ".ply", ".fbx"))), None)
                if not mesh:
                    raise ProviderError(f"KIRI Engine zip had no model ({', '.join(names)[:200]})")
                tmp = dest.parent / "kiri_unzipped"
                z.extractall(tmp)
                import trimesh

                trimesh.load(tmp / mesh).export(dest, file_type="glb")
        raw.unlink(missing_ok=True)


def _download(url: str, dest: Path):
    with httpx.stream("GET", url, timeout=httpx.Timeout(300.0, connect=20.0), follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


# ---------- tier registry ----------
TIERS = {
    "quick": {
        "name": "Quick AI model",
        "detail": "About 2 minutes. AI builds a clean, complete model from 4 of your photos; "
                  "fine details and exact proportions are approximated.",
        "min_photos": 8,
        "timeout": 1200,
    },
    "hq": {
        "name": "High-accuracy scan",
        "detail": "10–40 minutes. True 3D reconstruction from all your photos with automatic "
                  "background removal; best for exact copies and 3D printing.",
        "min_photos": KIRI_MIN_PHOTOS,
        "timeout": 4 * 3600,
    },
}


def provider_for(tier: str):
    """The configured provider for a tier, or None if its API key isn't set."""
    if tier == "quick":
        choice = config.QUICK_PROVIDER
        if choice in ("", "tripo") and config.TRIPO_API_KEY:
            return Tripo(config.TRIPO_API_KEY)
        if choice in ("", "fal_trellis") and config.FAL_KEY:
            return FalTrellis(config.FAL_KEY)
        return None
    if tier == "hq":
        return Kiri(config.KIRI_API_KEY) if config.KIRI_API_KEY else None
    return None


def available_tiers() -> list[dict]:
    out = []
    for tid, t in TIERS.items():
        if provider_for(tid):
            out.append({"id": tid, "name": t["name"], "detail": t["detail"], "min_photos": t["min_photos"],
                        "export_product_id": config.product_for_tier(tid)})
    return out
