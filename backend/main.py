import hashlib
import hmac
import logging
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
import db
from billing import verify_purchase

log = logging.getLogger("scanforge")
app = FastAPI(title="ScanForge API")
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic"}
RUNPOD = "https://api.runpod.ai/v2"


# ---------- signed-url helpers (HMAC) ----------
# Each signature is bound to a purpose, so a link handed out for one thing (e.g. a paid
# download) can't be replayed against another endpoint (e.g. uploading results).
def _sig(purpose: str, job_id: str, exp: int) -> str:
    msg = f"{purpose}:{job_id}:{exp}".encode()
    return hmac.new(config.SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _check_sig(purpose: str, job_id: str, exp: int, sig: str):
    if time.time() > exp or not hmac.compare_digest(_sig(purpose, job_id, exp), sig or ""):
        raise HTTPException(403, "bad or expired signature")


def _signed(purpose: str, job_id: str, path: str, ttl: int, extra: str = "") -> str:
    exp = int(time.time()) + ttl
    return f"{config.API_BASE}{path}?{extra}exp={exp}&sig={_sig(purpose, job_id, exp)}"


def _check_key(key: str):
    """Admin key for the worker manifest and dev-pay. Disabled while SECRET_KEY is the default."""
    if config.SECRET_IS_DEFAULT or not hmac.compare_digest(key or "", config.SECRET):
        raise HTTPException(403)


def _photo_urls(job_id: str):
    pdir = config.JOBS_DIR / job_id / "photos"
    if not pdir.is_dir():
        return []
    return [
        _signed("photo", job_id, f"/jobs/{job_id}/photo/{p.name}", config.PHOTO_FETCH_TTL)
        for p in sorted(pdir.iterdir())
        if p.suffix.lower() in PHOTO_EXT
    ]


def _result_url(job_id: str):
    return _signed("result", job_id, f"/jobs/{job_id}/result", config.PROCESSING_TIMEOUT + 3600)


@app.on_event("startup")
def startup():
    db.init()
    if config.SECRET_IS_DEFAULT:
        log.warning("SECRET_KEY is not set: signed URLs are forgeable; manifest and dev-pay are disabled")
    if not config.API_BASE:
        log.warning("API_BASE is not set: jobs cannot be processed")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- jobs ----------
@app.post("/jobs")
def create_job():
    job_id = uuid.uuid4().hex[:16]
    db.create_job(job_id)
    return {"job_id": job_id}


@app.post("/jobs/{job_id}/photos")
async def upload_photos(job_id: str, photos: list[UploadFile] = File(...)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] != "created":
        raise HTTPException(409, "job already submitted")
    if job["n_photos"] + len(photos) > config.MAX_PHOTOS:
        raise HTTPException(413, f"at most {config.MAX_PHOTOS} photos per job")
    pdir = config.JOBS_DIR / job_id / "photos"
    pdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in photos:
        ext = Path(f.filename or "photo.jpg").suffix.lower() or ".jpg"
        if ext not in PHOTO_EXT:
            continue
        data = await f.read(config.MAX_PHOTO_BYTES + 1)
        if len(data) > config.MAX_PHOTO_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than {config.MAX_PHOTO_BYTES // 2**20} MB")
        (pdir / f"{int(time.time() * 1000)}_{n:04d}{ext}").write_bytes(data)
        n += 1
    db.update_job(job_id, n_photos=job["n_photos"] + n)
    return {"uploaded": n}


@app.post("/jobs/{job_id}/process")
async def process(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["n_photos"] < config.MIN_PHOTOS:
        raise HTTPException(400, f"need at least {config.MIN_PHOTOS} photos")
    if not config.API_BASE:
        raise HTTPException(500, "API_BASE env var not set")
    # Atomic: a double tap or retry can't start two GPU jobs. Failed jobs may be retried.
    if not db.claim_status(job_id, ("created", "failed"), "queued", error=None):
        return {"status": job["status"]}

    if not config.RUNPOD_ENDPOINT:
        # Manual / Colab mode: an external worker reads /manifest and POSTs to the result URL.
        return {"status": "queued"}

    payload = {
        "input": {"job_id": job_id, "photo_urls": _photo_urls(job_id), "result_url": _result_url(job_id)}
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{RUNPOD}/{config.RUNPOD_ENDPOINT}/run",
                json=payload,
                headers={"Authorization": f"Bearer {config.RUNPOD_API_KEY}"},
            )
            r.raise_for_status()
        db.update_job(job_id, status="processing", started=time.time(), runpod_job=r.json()["id"])
    except Exception as e:  # noqa: BLE001
        db.update_job(job_id, status="failed", error=f"could not start GPU job: {str(e)[:300]}")
    return {"status": db.get_job(job_id)["status"]}


async def _check_progress(job: dict) -> dict:
    """Catch jobs that died without reporting back (worker crash, OOM, timeout, restart)."""
    if job["status"] != "processing":
        return job
    if job["started"] and time.time() - job["started"] > config.PROCESSING_TIMEOUT:
        db.update_job(job["id"], status="failed", error="processing timeout")
        return db.get_job(job["id"])
    if job["runpod_job"] and config.RUNPOD_ENDPOINT:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{RUNPOD}/{config.RUNPOD_ENDPOINT}/status/{job['runpod_job']}",
                    headers={"Authorization": f"Bearer {config.RUNPOD_API_KEY}"},
                )
            state = r.json().get("status")
            if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
                # The worker may still have posted a result; only fail if it hasn't.
                if db.claim_status(job["id"], ("processing",), "failed", error=f"GPU job {state.lower()}"):
                    return db.get_job(job["id"])
        except Exception:  # noqa: BLE001 - status polling is best effort
            pass
    return job


@app.get("/jobs/{job_id}/photo/{name}")
def fetch_photo(job_id: str, name: str, exp: int, sig: str):
    _check_sig("photo", job_id, exp, sig)
    path = (config.JOBS_DIR / job_id / "photos" / name).resolve()
    if not path.is_file() or config.JOBS_DIR.resolve() not in path.parents:
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/jobs/{job_id}/result")
async def receive_result(
    job_id: str,
    exp: int,
    sig: str,
    glb: UploadFile | None = File(None),
    stl: UploadFile | None = File(None),
    preview: UploadFile | None = File(None),
    log_text: str = Form("", alias="log"),
):
    _check_sig("result", job_id, exp, sig)
    if not db.get_job(job_id):
        raise HTTPException(404)
    jdir = config.JOBS_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    if log_text:
        (jdir / "pipeline.log").write_text(log_text)

    # The worker posts only a log when it fails. Without both models, this is a failure.
    if not (glb and stl):
        tail = [ln for ln in log_text.strip().splitlines() if ln.strip()][-1:] or ["worker reported no model"]
        db.update_job(job_id, status="failed", error=tail[0][:500], finished=time.time())
        return {"ok": False}

    (jdir / "model.glb").write_bytes(await glb.read())
    (jdir / "model.stl").write_bytes(await stl.read())
    if preview:
        (jdir / "preview.glb").write_bytes(await preview.read())
    db.update_job(job_id, status="done", error=None, finished=time.time())
    return {"ok": True}


@app.get("/jobs/{job_id}/manifest")
def manifest(job_id: str, key: str):
    """Work manifest for a Colab/manual worker. Requires SECRET_KEY."""
    _check_key(key)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return {
        "job_id": job_id,
        "status": job["status"],
        "n_photos": job["n_photos"],
        "photo_urls": _photo_urls(job_id),
        "result_url": _result_url(job_id),
    }


@app.post("/jobs/{job_id}/dev-pay")
def dev_pay(job_id: str, key: str):
    """DEV ONLY: mark a job paid without Play. Only exists while ALLOW_DEV_BILLING=1."""
    if config.DEV_BILLING != "1":
        raise HTTPException(404)
    _check_key(key)
    if not db.get_job(job_id):
        raise HTTPException(404)
    db.update_job(job_id, paid=1, product_id="dev")
    return {"paid": True}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    job = await _check_progress(job)
    out = {
        "job_id": job_id,
        "status": job["status"],
        "n_photos": job["n_photos"],
        "error": job["error"],
        "paid": bool(job["paid"]),
    }
    if job["status"] == "done":
        out["preview_url"] = _signed("preview", job_id, f"/jobs/{job_id}/preview", 3600)
        if job["paid"]:
            out["download"] = {
                fmt: _signed("download", job_id, f"/jobs/{job_id}/download", 900, f"format={fmt}&")
                for fmt in ("glb", "stl")
            }
    return out


@app.get("/jobs/{job_id}/preview")
def preview(job_id: str, exp: int, sig: str):
    _check_sig("preview", job_id, exp, sig)
    path = config.JOBS_DIR / job_id / "preview.glb"
    if not path.is_file():
        raise HTTPException(404)
    # Scene Viewer fetches cross-origin.
    return FileResponse(path, media_type="model/gltf-binary", headers={"Access-Control-Allow-Origin": "*"})


@app.get("/jobs/{job_id}/download")
def download(job_id: str, format: str, exp: int, sig: str):
    _check_sig("download", job_id, exp, sig)
    job = db.get_job(job_id)
    if not job or not job["paid"]:
        raise HTTPException(402, "payment required")
    if format not in ("glb", "stl"):
        raise HTTPException(400, "format must be glb or stl")
    path = config.JOBS_DIR / job_id / f"model.{format}"
    if not path.is_file():
        raise HTTPException(404, "file not ready")
    return FileResponse(path, filename=f"scanforge_{job_id}.{format}")


# ---------- billing ----------
class VerifyReq(BaseModel):
    job_id: str
    product_id: str
    token: str


@app.post("/billing/verify")
def billing_verify(req: VerifyReq):
    job = db.get_job(req.job_id)
    if not job:
        raise HTTPException(404)
    if job["paid"]:
        return {"paid": True}
    # One purchase unlocks one job: refuse a token that already paid for another scan.
    owner = db.job_for_token(req.token)
    if owner and owner != req.job_id:
        raise HTTPException(409, "purchase already used for another scan")
    if not verify_purchase(req.job_id, req.product_id, req.token):
        raise HTTPException(400, "purchase verification failed")
    db.update_job(req.job_id, paid=1, product_id=req.product_id, purchase_token=req.token)
    return {"paid": True}
