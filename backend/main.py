import hashlib
import os
import hmac
import logging
import time
import uuid
from pathlib import Path

import httpx
import secrets

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

import backup
import config
import db
import privacy
import processor
import providers
import storage
from billing import verify_pack, verify_purchase

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
    problems = config.production_problems()
    if problems:
        # Refuse to serve real customers with test settings.
        raise RuntimeError("Unsafe production settings: " + "; ".join(problems))
    db.init()
    if os.getenv("SCANFORGE_NO_PROCESSOR") != "1":
        processor.start()
        backup.start()
    if config.SECRET_IS_DEFAULT:
        log.warning("SECRET_KEY is not set: signed URLs are forgeable; manifest and dev-pay are disabled")
    if not config.API_BASE:
        log.warning("API_BASE is not set: jobs cannot be processed")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/tiers")
def tiers():
    """Processing options this server can offer (depends on which API keys are set)."""
    return {"default": config.DEFAULT_TIER, "tiers": providers.available_tiers()}


# ---------- jobs ----------
# ---------- accounts and points ----------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _account(authorization: str | None, required: bool = True) -> str | None:
    """The caller's account from 'Authorization: Bearer <token>'."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    account = db.account_for_token(_token_hash(token)) if token else None
    if required and not account:
        raise HTTPException(401, "sign-in required")
    return account


@app.post("/accounts")
def create_account():
    """A private account for this app install. The token is shown once; the app keeps it."""
    account_id = uuid.uuid4().hex[:16]
    token = secrets.token_urlsafe(32)
    db.create_account(account_id, _token_hash(token))
    if config.WELCOME_POINTS:
        db.add_entry(account_id, config.WELCOME_POINTS, "bonus", f"welcome:{account_id}", "Welcome bonus")
    return {"account_id": account_id, "token": token}


def _packs():
    return [{"product_id": pid, "points": pts, "price": config.PACK_PRICES.get(pid, "")}
            for pid, pts in sorted(config.POINT_PACKS.items(), key=lambda kv: kv[1])]


@app.get("/account")
def account_summary(authorization: str | None = Header(None)):
    account = _account(authorization)
    acc = db.get_account(account)
    return {"account_id": account, "balance": db.balance(account), "packs": _packs(),
            "consent_version": acc["consent_version"], "privacy_version": config.PRIVACY_VERSION,
            "privacy_url": f"{config.API_BASE}/privacy"}


# ---------- privacy ----------
@app.get("/privacy", response_class=HTMLResponse)
def privacy_notice():
    return privacy.notice_html()


class ConsentReq(BaseModel):
    version: str


@app.post("/account/consent")
def give_consent(req: ConsentReq, authorization: str | None = Header(None)):
    """The customer agreed to the privacy notice shown in the app (recorded with the time)."""
    account = _account(authorization)
    if req.version != config.PRIVACY_VERSION:
        raise HTTPException(409, "the privacy notice has changed; please review it again")
    db.record_consent(account, req.version)
    return {"consent_version": req.version}


@app.delete("/account")
def delete_account(authorization: str | None = Header(None)):
    """Withdraw consent and erase: photos and models deleted now, token revoked, remaining
    points forfeited. Payment records stay in the ledger (accounting law)."""
    account = _account(authorization)
    store = storage.get()
    for job in db.jobs_for_account(account):
        store.delete_job(job["id"])
    forfeited = db.close_account(account)
    log.info("account %s deleted (%d points forfeited)", account, forfeited)
    return {"deleted": True, "forfeited_points": forfeited}


@app.get("/account/history")
def account_history(limit: int = 100, authorization: str | None = Header(None)):
    account = _account(authorization)
    return {"balance": db.balance(account),
            "entries": db.history(account, max(1, min(limit, 500)))}


class PackReq(BaseModel):
    product_id: str
    token: str


@app.post("/account/purchase")
def buy_points(req: PackReq, authorization: str | None = Header(None)):
    account = _account(authorization)
    points = config.POINT_PACKS.get(req.product_id)
    if not points:
        raise HTTPException(400, "unknown point pack")
    if not verify_pack(account, req.product_id, req.token):
        raise HTTPException(400, "purchase verification failed")
    # The ledger refuses a second credit for the same purchase token.
    if not db.add_entry(account, points, "purchase", f"play:{req.token}", f"Bought {points} points"):
        return {"credited": 0, "balance": db.balance(account)}
    return {"credited": points, "balance": db.balance(account)}


class CreateReq(BaseModel):
    tier: str | None = None


def _service_tier(tier: str | None) -> bool:
    """Tiers run by a 3D service: paid for before processing."""
    return tier in providers.TIERS


@app.post("/jobs")
def create_job(req: CreateReq | None = Body(None), authorization: str | None = Header(None)):
    tier = req.tier if req else None
    if tier is not None and providers.provider_for(tier) is None:
        raise HTTPException(400, f"'{tier}' processing is not available on this server")
    account = _account(authorization, required=False)
    job_id = uuid.uuid4().hex[:16]
    db.create_job(job_id)
    if tier or account:
        db.update_job(job_id, tier=tier, account_id=account)
    return {"job_id": job_id, "tier": tier}


def _open_job(job_id: str, authorization: str | None) -> dict:
    """A job still taking photos, checked against the caller's account when it has an owner."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["account_id"] and _account(authorization, required=False) != job["account_id"]:
        raise HTTPException(403, "this scan belongs to another account")
    if job["status"] != "created":
        raise HTTPException(409, "job already submitted")
    return job


def _local_photos(job_id: str) -> list[Path]:
    pdir = config.JOBS_DIR / job_id / "photos"
    return [p for p in pdir.iterdir() if p.suffix.lower() in PHOTO_EXT] if pdir.is_dir() else []


class UploadUrlsReq(BaseModel):
    count: int


@app.post("/jobs/{job_id}/upload-urls")
def upload_urls(job_id: str, req: UploadUrlsReq, authorization: str | None = Header(None)):
    """Presigned links so the phone uploads photos straight to file storage.
    {"direct": false} means this server takes photos itself: use POST /jobs/{id}/photos."""
    store = storage.get()
    if not store.direct:
        return {"direct": False}
    _open_job(job_id, authorization)
    if req.count < 1:
        raise HTTPException(400, "count must be at least 1")
    have = len(store.list(storage.photo_prefix(job_id))) + len(_local_photos(job_id))
    if have + req.count > config.MAX_PHOTOS:
        raise HTTPException(413, f"at most {config.MAX_PHOTOS} photos per job")
    stamp = int(time.time() * 1000)
    urls = [store.put_url(f"{storage.photo_prefix(job_id)}{stamp}_{i:04d}.jpg", storage.PHOTO_TYPE, 3600)
            for i in range(req.count)]
    return {"direct": True, "urls": urls, "content_type": storage.PHOTO_TYPE,
            "max_bytes": config.MAX_PHOTO_BYTES}


@app.post("/jobs/{job_id}/photos/complete")
def photos_complete(job_id: str, authorization: str | None = Header(None)):
    """After direct uploads: count what arrived (dropping oversized or empty files). Safe to repeat."""
    store = storage.get()
    job = _open_job(job_id, authorization)
    if not store.direct:
        return {"n_photos": job["n_photos"]}
    objs = store.list(storage.photo_prefix(job_id))
    bad = [o["key"] for o in objs if not 0 < o["size"] <= config.MAX_PHOTO_BYTES]
    store.delete(bad)
    n = len(objs) - len(bad) + len(_local_photos(job_id))
    db.update_job(job_id, n_photos=n)
    return {"n_photos": n, "rejected": len(bad)}


@app.post("/jobs/{job_id}/photos")
async def upload_photos(job_id: str, photos: list[UploadFile] = File(...),
                        authorization: str | None = Header(None)):
    job = _open_job(job_id, authorization)
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


class ProcessReq(BaseModel):
    tier: str | None = None


@app.post("/jobs/{job_id}/process")
async def process(job_id: str, req: ProcessReq | None = Body(None),
                  authorization: str | None = Header(None)):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    asked = job["tier"] or (req.tier if req and req.tier else None)
    tier = asked or config.DEFAULT_TIER
    prov = providers.provider_for(tier) if _service_tier(tier) else None
    if _service_tier(tier) and prov is None:
        if asked:
            raise HTTPException(400, f"'{tier}' processing is not available on this server")
        tier = "diy"          # no service configured: fall back to the self-hosted pipeline
    elif not _service_tier(tier):
        tier = "diy"
    if prov and job["status"] == "failed":
        raise HTTPException(409, "This scan failed and its points were refunded. Please start a new scan.")
    # Service tiers cost us money per run, so they're paid for before processing starts:
    # with points from the owner's balance (or, legacy, a per-scan purchase).
    if prov and not (job["paid"] and job["product_id"] in (config.product_for_tier(tier), "dev", "points")):
        account = _account(authorization)
        if job["account_id"] and job["account_id"] != account:
            raise HTTPException(403, "this scan belongs to another account")
        if job["n_photos"] < providers.min_photos(tier):
            raise HTTPException(400, f"need at least {providers.min_photos(tier)} photos for this option")
        cost = config.points_for_tier(tier)
        ok, bal = db.spend(account, cost, job_id, f"{providers.TIERS[tier]['name']} scan")
        if not ok:
            raise HTTPException(402, f"Not enough points: this scan needs {cost}, you have {bal}.")
        db.update_job(job_id, paid=1, product_id="points", cost=cost, account_id=account, tier=tier)
        job = db.get_job(job_id)
    min_photos = providers.min_photos(tier) if prov else config.MIN_PHOTOS
    if job["n_photos"] < min_photos:
        raise HTTPException(400, f"need at least {min_photos} photos for this option")
    if not prov and not config.API_BASE:
        raise HTTPException(500, "API_BASE env var not set")
    # Atomic: a double tap or retry can't start two jobs. Failed jobs may be retried.
    if not db.claim_status(job_id, ("created", "failed"), "queued", error=None, tier=tier,
                           provider=prov.name if prov else None, provider_task=None, progress=0):
        return {"status": job["status"]}
    if prov:
        return {"status": "queued"}   # the background processor takes it from here

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
    limit = providers.TIERS.get(job["tier"], {}).get("timeout", config.PROCESSING_TIMEOUT)
    if job["started"] and time.time() - job["started"] > limit:
        if db.claim_status(job["id"], ("processing",), "failed", finished=time.time(),
                           error=processor.customer_message("processing timeout")):
            processor.refund(job["id"])
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
        "tier": job["tier"],
        "progress": job["progress"] or 0,
        "export_product_id": config.product_for_tier(job["tier"]),
        "pay_before": _service_tier(job["tier"]),
        "price": config.price_for_tier(job["tier"]),
        "points": config.points_for_tier(job["tier"]),
    }
    if job.get("purged"):
        out["expired"] = True   # files deleted after the retention period
    elif job["status"] == "done":
        jdir = config.JOBS_DIR / job_id
        store = storage.get()
        # Files still on this server go through signed API links; files in R2 are fetched
        # straight from the bucket (no server bandwidth).
        remote = store.direct and not (jdir / "model.glb").is_file()
        out["preview_url"] = (store.get_url(f"jobs/{job_id}/preview.glb", 3600) if remote
                              else _signed("preview", job_id, f"/jobs/{job_id}/preview", 3600))
        if job["paid"]:
            out["download"] = {
                fmt: (store.get_url(f"jobs/{job_id}/model.{fmt}", 900, f"scanforge_{job_id}.{fmt}")
                      if remote else
                      _signed("download", job_id, f"/jobs/{job_id}/download", 900, f"format={fmt}&"))
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
    if req.product_id != config.product_for_tier(job["tier"]):
        raise HTTPException(400, "this purchase doesn't match this kind of scan")
    # Refuse before the app acknowledges the purchase, so Play refunds it automatically.
    if _service_tier(job["tier"]) and job["n_photos"] < providers.min_photos(job["tier"]):
        raise HTTPException(400, f"need at least {providers.min_photos(job['tier'])} photos for this option")
    if not verify_purchase(req.job_id, req.product_id, req.token):
        raise HTTPException(400, "purchase verification failed")
    db.update_job(req.job_id, paid=1, product_id=req.product_id, purchase_token=req.token)
    return {"paid": True}
