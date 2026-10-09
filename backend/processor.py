"""Background loop that runs provider-backed jobs (quick / hq tiers).

queued      -> send photos to the provider            -> processing (provider_task saved)
processing  -> poll; on success download + build files -> done
            -> provider failure / timeout              -> failed

All state lives in the database, so a restart picks up where it left off.
"""
import logging
import re
import shutil
import threading
import time

import config
import db
import providers
import storage

log = logging.getLogger("scanforge.processor")
POLL_SECONDS = 5
MAX_TRANSIENT_ERRORS = 6        # network hiccups tolerated per job before giving up
_errors: dict[str, int] = {}


# Problems on our side (keys, credit, outages): the customer can't fix these by rescanning.
_OUR_PROBLEM = ("api key", "credit", "not configured", "unreachable", "error 5", "timeout")
_SERVICE_NAMES = re.compile(r"\b(tripo|kiri engine|kiri|fal\.ai|fal|trellis)\b:?\s*", re.I)


def customer_message(msg: str) -> str:
    """What the app shows: no service names, and no internal details for server-side problems."""
    low = msg.lower()
    if any(k in low for k in _OUR_PROBLEM):
        return "Processing is temporarily unavailable. Please try again later."
    return _SERVICE_NAMES.sub("", msg).strip() or "We couldn't build a model from these photos."


def _fail(job_id: str, msg: str):
    log.warning("job %s failed: %s", job_id, msg)   # full reason stays in the server log
    try:
        (config.JOBS_DIR / job_id / "error.log").write_text(msg)
    except OSError:
        pass
    if db.claim_status(job_id, ("queued", "processing"), "failed", error=customer_message(msg)[:500],
                       finished=time.time()):
        refund(job_id)
    _errors.pop(job_id, None)


def refund(job_id: str):
    """Give back the points a failed scan was charged (once per scan)."""
    job = db.get_job(job_id)
    if job and job["account_id"] and job["product_id"] == "points" and job["cost"]:
        if db.add_entry(job["account_id"], job["cost"], "refund", job_id, "Refund: scan failed"):
            log.info("job %s: refunded %s points", job_id, job["cost"])


def build_outputs(jdir, model_glb):
    """model.glb (as delivered), model.stl (for printing) and a lighter preview.glb."""
    import trimesh

    mesh = trimesh.load(model_glb, force="mesh")
    # Services deliver models about 1 unit tall, which a slicer reads as 1 mm: scale for printing.
    size = float(max(mesh.extents)) if len(mesh.vertices) else 0.0
    if size > 0:
        mesh.apply_scale(config.STL_SIZE_MM / size)
    mesh.export(jdir / "model.stl")
    make_preview(model_glb, jdir / "preview.glb")


def make_preview(src, dest, max_texture=512):
    """Same model with textures shrunk, so the free preview isn't the paid export."""
    try:
        import trimesh
        from PIL import Image

        scene = trimesh.load(src, force="scene")
        for geom in scene.geometry.values():
            mat = getattr(geom.visual, "material", None)
            if mat is None:
                continue
            for attr in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture",
                         "occlusionTexture", "emissiveTexture", "image"):
                img = getattr(mat, attr, None)
                if isinstance(img, Image.Image) and max(img.size) > max_texture:
                    small = img.copy()
                    small.thumbnail((max_texture, max_texture))
                    setattr(mat, attr, small)
        scene.export(dest, file_type="glb")
    except Exception:  # noqa: BLE001 - a full-quality preview beats no preview
        log.exception("preview downscale failed; using the full model")
        shutil.copy(src, dest)


def gather_photos(job_id: str) -> list:
    """The job's photos on local disk, pulling direct uploads in from file storage first
    (inbound transfer is free on R2 and Render)."""
    pdir = config.JOBS_DIR / job_id / "photos"
    store = storage.get()
    if store.direct:
        for obj in store.list(storage.photo_prefix(job_id)):
            dest = pdir / obj["key"].rsplit("/", 1)[-1]
            if not dest.is_file():
                store.download(obj["key"], dest)
    return sorted(p for p in pdir.iterdir() if p.is_file()) if pdir.is_dir() else []


def publish_outputs(job_id: str):
    """With R2: move the finished files to the bucket and free the server's disk."""
    store = storage.get()
    if not store.direct:
        return
    jdir = config.JOBS_DIR / job_id
    for name, ctype in storage.OUTPUTS.items():
        if (jdir / name).is_file():
            store.upload(jdir / name, f"jobs/{job_id}/{name}", ctype)
    shutil.rmtree(jdir, ignore_errors=True)
    if db.get_job(job_id) is None:     # the customer deleted their data meanwhile
        store.delete_job(job_id)


def step(job: dict):
    """Advance one job by one stage. Safe to call repeatedly."""
    job_id = job["id"]
    prov = providers.provider_for(job["tier"])
    if prov is None or prov.name != job["provider"]:
        return _fail(job_id, f"the {job['tier']} service is not configured on the server")
    jdir = config.JOBS_DIR / job_id
    try:
        if job["status"] == "queued":
            photos = gather_photos(job_id)
            task = prov.submit(photos)
            db.claim_status(job_id, ("queued",), "processing", provider_task=task,
                            started=time.time(), progress=0)
            log.info("job %s submitted to %s as %s", job_id, prov.name, task)
            return

        limit = providers.TIERS.get(job["tier"], {}).get("timeout", config.PROCESSING_TIMEOUT)
        if job["started"] and time.time() - job["started"] > limit:
            return _fail(job_id, "processing timeout")
        p = prov.poll(job["provider_task"])
        if p.state == "running":
            if p.progress != job["progress"]:
                db.update_job(job_id, progress=p.progress)
        elif p.state == "failed":
            _fail(job_id, p.error or "the 3D service could not build a model")
        else:
            jdir.mkdir(parents=True, exist_ok=True)   # e.g. a restart on a fresh disk
            tmp = jdir / "download.glb"
            prov.fetch(p.url, tmp)
            build_outputs(jdir, tmp)
            tmp.replace(jdir / "model.glb")
            publish_outputs(job_id)
            db.claim_status(job_id, ("processing",), "done", error=None, progress=100, finished=time.time())
            _errors.pop(job_id, None)
            log.info("job %s done", job_id)
    except providers.ProviderError as e:
        _fail(job_id, str(e))
    except Exception as e:  # noqa: BLE001 - network blips: retry a few times
        n = _errors[job_id] = _errors.get(job_id, 0) + 1
        log.warning("job %s: %s (attempt %d)", job_id, e, n)
        if n >= MAX_TRANSIENT_ERRORS:
            _fail(job_id, f"3D service unreachable: {str(e)[:200]}")


def purge_expired(now=None) -> int:
    """Delete photos and models once a scan is older than RETENTION_DAYS (privacy notice)."""
    now = now or time.time()
    store = storage.get()
    n = 0
    for job in db.jobs_to_purge(now - config.RETENTION_DAYS * 86400):
        try:
            store.delete_job(job["id"])
            db.update_job(job["id"], purged=1)
            n += 1
        except Exception:  # noqa: BLE001 - try again next round
            log.exception("could not delete files of job %s", job["id"])
    if n:
        log.info("deleted files of %d expired scans", n)
    return n


_last_purge = 0.0


def run_once():
    global _last_purge
    for job in db.jobs_in(("queued", "processing")):
        step(job)
    if time.time() - _last_purge > 3600:
        _last_purge = time.time()
        purge_expired()


def _loop():
    while True:
        try:
            run_once()
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("processor loop error")
        time.sleep(POLL_SECONDS)


def start():
    threading.Thread(target=_loop, name="processor", daemon=True).start()
