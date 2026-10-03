import asyncio
import hashlib
import hmac
import time
import uuid
from pathlib import Path

import httpx
import db
from billing import verify_purchase
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config

app = FastAPI(title="ScanForge API")
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic"}


# ---------- signed-url helpers (HMAC) ----------
def _sig(msg_id: str, exp: int) -> str:
    msg = f"{msg_id}:{exp}".encode()
    return hmac.new(config.SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _check_sig(msg_id: str, exp: int, sig: str):
    if time.time() > exp or not hmac.compare_digest(_sig(msg_id, exp), sig or ""):
        raise HTTPException(403, "bad or expired signature")


def _photo_urls(job_id: str):
    exp = int(time.time()) + config.PHOTO_FETCH_TTL
    urls = []
    pdir = config.JOBS_DIR / job_id / "photos"
    if pdir.is_dir():
        for p in sorted(pdir.iterdir()):
            if p.suffix.lower() in PHOTO_EXT:
                urls.append(
                    f"{config.API_BASE}/jobs/{job_id}/photo/{p.name}?exp={exp}&sig={_sig(job_id, exp)}"
                )
    return urls


@app.on_event("startup")
def startup():
    db.init()


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
    pdir = config.JOBS_DIR / job_id / "photos"
    pdir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in photos:
        ext = Path(f.filename or "photo.jpg").suffix.lower() or ".jpg"
        if ext not in PHOTO_EXT:
            continue
        dest = pdir / f"{int(time.time() * 1000)}_{n}{ext}"
        dest.write_bytes(await f.read())
        n += 1
    db.update_job(job_id, n_photos=job["n_photos"] + n)
    return {"uploaded": n}


@app.post("/jobs/{job_id}/process")
async def process(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["n_photos"] < 8:
        raise HTTPException(400, "need at least 8 photos")
    if not config.API_BASE:
        raise HTTPException(500, "API_BASE env var not set")
    db.update_job(job_id, status="queued")
    asyncio.create_task(_run_job(job_id))
    return {"status": "queued"}


async def _run_job(job_id: str):
    if not config.RUNPOD_ENDPOINT:
        # Manual / Colab mode: wait until an external worker POSTs results.
        return
    db.update_job(job_id, status="processing", started=time.time())
    exp = int(time.time()) + config.PHOTO_FETCH_TTL
    result_url = f"{config.API_BASE}/jobs/{job_id}/result?exp={exp}&sig={_sig(job_id, exp)}"
    payload = {
        "input": {
            "job_id": job_id,
            "photo_urls": _photo_urls(job_id),
            "result_url": result_url,
        }
    }
    headers = {"Authorization": f"Bearer {config.RUNPOD_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.runpod.ai/v2/{config.RUNPOD_ENDPOINT}/run",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            db.update_job(job_id, runpod_job=r.json()["id"])
        await asyncio.sleep(1800)
        job = db.get_job(job_id)
        if job and job["status"] == "processing":
            db.update_job(job_id, status="failed", error="processing timeout")
    except Exception as e:  # noqa: BLE001
        db.update_job(job_id, status="failed", error=str(e)[:500])


@app.get("/jobs/{job_id}/photo/{name}")
def fetch_photo(job_id: str, name: str, exp: int, sig: str):
    _check_sig(job_id, exp, sig)
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
    log: str = Form(""),
):
    _check_sig(job_id, exp, sig)
    jdir = config.JOBS_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    if glb:
        (jdir / "model.glb").write_bytes(await glb.read())
    if stl:
        (jdir / "model.stl").write_bytes(await stl.read())
    if preview:
        (jdir / "preview.glb").write_bytes(await preview.read())
    if log:
        (jdir / "pipeline.log").write_text(log)
    db.update_job(job_id, status="done", finished=time.time())
    return {"ok": True}


@app.get("/jobs/{job_id}/manifest")
def manifest(job_id: str, key: str):
    """Work manifest for the Colab/RunPod worker. Requires the backend secret."""
    if key != config.SECRET:
        raise HTTPException(403)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    exp = int(time.time()) + config.PHOTO_FETCH_TTL
    return {
        "job_id": job_id,
        "status": job["status"],
        "n_photos": job["n_photos"],
        "photo_urls": _photo_urls(job_id),
        "result_url": f"{config.API_BASE}/jobs/{job_id}/result?exp={exp}&sig={_sig(job_id, exp)}",
    }


@app.post("/jobs/{job_id}/dev-pay")
def dev_pay(job_id: str, key: str):
    """DEV ONLY: mark a job paid without Play. Only exists while ALLOW_DEV_BILLING=1."""
    if config.DEV_BILLING != "1":
        raise HTTPException(404)
    if key != config.SECRET:
        raise HTTPException(403)
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    db.update_job(job_id, paid=1, product_id="dev")
    return {"paid": True}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    out = {
        "job_id": job_id,
        "status": job["status"],
        "n_photos": job["n_photos"],
        "error": job["error"],
        "paid": bool(job["paid"]),
    }
    if job["status"] == "done":
        exp = int(time.time()) + 3600
        out["preview_url"] = (
            f"{config.API_BASE}/jobs/{job_id}/preview?exp={exp}"
            f"&sig={_sig(job_id + '/preview', exp)}"
        )
    if job["paid"]:
        exp = int(time.time()) + 900
        out["download"] = {
            "glb": f"{config.API_BASE}/jobs/{job_id}/download?format=glb&exp={exp}&sig={_sig(job_id, exp)}",
            "stl": f"{config.API_BASE}/jobs/{job_id}/download?format=stl&exp={exp}&sig={_sig(job_id, exp)}",
        }
    return out


@app.get("/jobs/{job_id}/preview")
def preview(job_id: str, exp: int, sig: str):
    _check_sig(job_id + "/preview", exp, sig)
    path = config.JOBS_DIR / job_id / "preview.glb"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="model/gltf-binary")


@app.get("/jobs/{job_id}/download")
def download(job_id: str, format: str, exp: int, sig: str):
    _check_sig(job_id, exp, sig)
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
    if not verify_purchase(req.product_id, req.token):
        raise HTTPException(400, "purchase verification failed")
    db.update_job(req.job_id, paid=1, product_id=req.product_id, purchase_token=req.token)
    return {"paid": True}
