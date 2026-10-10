"""End-to-end API tests: python -m pytest backend/tests (no GPU, Play or RunPod needed)."""
import importlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("API_BASE", "http://testserver")
    monkeypatch.setenv("ALLOW_DEV_BILLING", "1")  # no service account → dev verification
    monkeypatch.setenv("SCANFORGE_NO_PROCESSOR", "1")  # tests drive processor.run_once() themselves
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    for k in ("TRIPO_API_KEY", "FAL_KEY", "KIRI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for m in ("config", "db", "billing", "providers", "processor", "backup", "storage", "privacy", "main"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


def _path(url):
    u = urlparse(url)
    return u.path + "?" + u.query


def _new_job(c, n=10):
    job = c.post("/jobs").json()["job_id"]
    files = [("photos", (f"p{i}.jpg", b"\xff\xd8fake", "image/jpeg")) for i in range(n)]
    assert c.post(f"/jobs/{job}/photos", files=files).json() == {"uploaded": n}
    return job


def test_full_flow(client):
    c = client
    job = _new_job(c)
    assert c.post(f"/jobs/{job}/process").json()["status"] == "queued"
    # Second tap must not start a second job, and uploads are now closed.
    assert c.post(f"/jobs/{job}/process").json()["status"] == "queued"
    assert c.post(f"/jobs/{job}/photos", files=[("photos", ("x.jpg", b"x", "image/jpeg"))]).status_code == 409

    m = c.get(f"/jobs/{job}/manifest", params={"key": "test-secret"}).json()
    assert len(m["photo_urls"]) == 10
    assert c.get(_path(m["photo_urls"][0])).content == b"\xff\xd8fake"

    files = {
        "glb": ("model.glb", b"FULL", "application/octet-stream"),
        "stl": ("model.stl", b"STL", "application/octet-stream"),
        "preview": ("preview.glb", b"LOW", "application/octet-stream"),
    }
    assert c.post(_path(m["result_url"]), files=files, data={"log": "ok"}).json() == {"ok": True}

    s = c.get(f"/jobs/{job}").json()
    assert s["status"] == "done" and not s["paid"] and "download" not in s
    assert c.get(_path(s["preview_url"])).content == b"LOW"

    assert c.post("/billing/verify", json={"job_id": job, "product_id": "export_unlock", "token": "T1"}).json() == {"paid": True}
    s = c.get(f"/jobs/{job}").json()
    assert c.get(_path(s["download"]["glb"])).content == b"FULL"  # preview must not overwrite the model
    assert c.get(_path(s["download"]["stl"])).content == b"STL"


def test_purchase_token_cannot_unlock_two_jobs(client):
    a, b = _new_job(client), _new_job(client)
    assert client.post("/billing/verify", json={"job_id": a, "product_id": "export_unlock", "token": "T"}).status_code == 200
    assert client.post("/billing/verify", json={"job_id": b, "product_id": "export_unlock", "token": "T"}).status_code == 409


def test_unknown_product_rejected(client):
    job = _new_job(client)
    r = client.post("/billing/verify", json={"job_id": job, "product_id": "cheap_sticker", "token": "T"})
    assert r.status_code == 400


def test_worker_failure_marks_job_failed(client):
    job = _new_job(client)
    client.post(f"/jobs/{job}/process")
    m = client.get(f"/jobs/{job}/manifest", params={"key": "test-secret"}).json()
    client.post(_path(m["result_url"]), data={"log": "trace...\nerror: could not match photos"})
    s = client.get(f"/jobs/{job}").json()
    assert s["status"] == "failed" and "could not match" in s["error"]
    # Failed jobs can be retried.
    assert client.post(f"/jobs/{job}/process").json()["status"] == "queued"


def test_signatures_are_purpose_bound(client):
    job = _new_job(client)
    client.post(f"/jobs/{job}/process")
    m = client.get(f"/jobs/{job}/manifest", params={"key": "test-secret"}).json()
    q = parse_qs(urlparse(m["photo_urls"][0]).query)
    # A photo-fetch signature must not authorise posting results.
    r = client.post(f"/jobs/{job}/result?exp={q['exp'][0]}&sig={q['sig'][0]}", data={"log": "x"})
    assert r.status_code == 403
    assert client.get(f"/jobs/{job}/manifest", params={"key": "wrong"}).status_code == 403


def test_too_few_photos(client):
    job = _new_job(client, n=3)
    assert client.post(f"/jobs/{job}/process").status_code == 400


# ---------- provider tiers (fake services, no network) ----------
def _real_jpeg(seed):
    import io
    from PIL import Image
    img = Image.effect_noise((64, 48), 40 + seed).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _textured_glb(path):
    import numpy as np
    import trimesh
    from PIL import Image
    m = trimesh.creation.box()
    uv = np.random.default_rng(0).random((len(m.vertices), 2))
    tex = Image.new("RGB", (1024, 1024), (200, 80, 40))
    m.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(baseColorTexture=tex))
    m.export(path)


class FakeProvider:
    name = "fake"

    def __init__(self, outcome="success", polls_before_done=1):
        self.outcome, self.left, self.submitted = outcome, polls_before_done, None

    def submit(self, photos):
        self.submitted = photos
        return "task-1"

    def poll(self, task):
        import providers
        assert task == "task-1"
        if self.left > 0:
            self.left -= 1
            return providers.Poll("running", 40)
        if self.outcome == "failed":
            return providers.Poll("failed", error="service said no")
        return providers.Poll("success", 100, url="https://example/model.glb")

    def fetch(self, url, dest):
        _textured_glb(dest)


def _tier_client(client, monkeypatch, fake, tiers=("quick", "hq")):
    import providers
    monkeypatch.setattr(providers, "provider_for", lambda t: fake if t in tiers else None)
    return client


def _new_real_job(c, n, tier=None):
    job = c.post("/jobs", json={"tier": tier} if tier else None).json()["job_id"]
    files = [("photos", (f"p{i}.jpg", _real_jpeg(i), "image/jpeg")) for i in range(n)]
    c.post(f"/jobs/{job}/photos", files=files)
    return job


def test_tiers_listed_only_when_configured(client, monkeypatch):
    assert client.get("/tiers").json()["tiers"] == []
    fake = FakeProvider()
    _tier_client(client, monkeypatch, fake, tiers=("quick",))
    t = client.get("/tiers").json()["tiers"]
    assert [x["id"] for x in t] == ["quick"] and t[0]["export_product_id"] == "scan_quick"


def test_quick_tier_end_to_end(client, monkeypatch):
    import processor
    fake = FakeProvider(polls_before_done=1)
    c = _tier_client(client, monkeypatch, fake)
    job = _new_real_job(c, 12, tier="quick")
    assert c.get(f"/jobs/{job}").json()["pay_before"] is True
    # Nothing runs (and nothing is spent at the service) until the scan is paid for.
    assert c.post(f"/jobs/{job}/process").status_code == 401   # no account, no points
    bad = c.post("/billing/verify", json={"job_id": job, "product_id": "scan_hq", "token": "T"})
    assert bad.status_code == 400
    assert c.post("/billing/verify", json={"job_id": job, "product_id": "scan_quick", "token": "T"}).json() == {"paid": True}
    assert c.post(f"/jobs/{job}/process").json() == {"status": "queued"}

    processor.run_once()                       # submit
    assert len(fake.submitted) == 12
    s = c.get(f"/jobs/{job}").json()
    assert s["status"] == "processing" and s["tier"] == "quick"
    processor.run_once()                       # still running
    assert c.get(f"/jobs/{job}").json()["progress"] == 40
    processor.run_once()                       # done: download + STL + preview
    s = c.get(f"/jobs/{job}").json()
    assert s["status"] == "done", s
    preview = c.get(_path(s["preview_url"])).content
    assert preview[:4] == b"glTF"

    # Paid up front, so downloads are available as soon as it's done.
    glb = c.get(_path(s["download"]["glb"])).content
    stl = c.get(_path(s["download"]["stl"])).content
    assert glb[:4] == b"glTF" and len(stl) > 500
    import io
    import trimesh
    printed = trimesh.load(io.BytesIO(stl), file_type="stl")
    assert abs(max(printed.extents) - 100) < 0.01      # sized for a slicer (mm)
    assert len(preview) < len(glb)             # preview carries a smaller texture


def test_hq_needs_20_photos_with_kiri_and_reports_failure(client, monkeypatch):
    import config
    import processor
    monkeypatch.setattr(config, "PREMIUM_SERVICE", "kiri")
    fake = FakeProvider(outcome="failed", polls_before_done=0)
    c = _tier_client(client, monkeypatch, fake)
    few = _new_real_job(c, 12, tier="hq")
    # Too few photos: the purchase is refused, so the app never acknowledges it (Play refunds).
    r = c.post("/billing/verify", json={"job_id": few, "product_id": "scan_hq", "token": "T0"})
    assert r.status_code == 400 and "20" in r.json()["detail"]

    job = _new_real_job(c, 20, tier="hq")
    assert c.post("/billing/verify", json={"job_id": job, "product_id": "scan_hq", "token": "T1"}).status_code == 200
    assert c.post(f"/jobs/{job}/process").json() == {"status": "queued"}
    processor.run_once()
    processor.run_once()
    s = c.get(f"/jobs/{job}").json()
    assert s["status"] == "failed" and "service said no" in s["error"]
    assert s["export_product_id"] == "scan_hq"


def test_unconfigured_tier_rejected(client):
    assert client.post("/jobs", json={"tier": "hq"}).status_code == 400
    job = _new_job(client)
    r = client.post(f"/jobs/{job}/process", json={"tier": "hq"})
    assert r.status_code == 400


def test_budget_tier_uses_trellis(client, monkeypatch):
    import config
    import providers
    monkeypatch.setattr(config, "FAL_KEY", "fk")
    monkeypatch.setattr(config, "TRIPO_API_KEY", "")
    assert isinstance(providers.provider_for("basic"), providers.FalTrellis)
    assert providers.provider_for("quick") is None
    t = client.get("/tiers").json()["tiers"]
    # One fal key runs Basic (TRELLIS) and Premium (Rodin).
    assert [x["id"] for x in t] == ["basic", "hq"] and t[0]["export_product_id"] == "scan_basic"
    assert isinstance(providers.provider_for("hq"), providers.FalRodin)
    assert t[1]["min_photos"] == 8


def test_pick_views_spreads_round_the_loop(tmp_path):
    import providers
    paths = []
    for i in range(40):
        p = tmp_path / f"{i:03d}.jpg"
        p.write_bytes(_real_jpeg(i))
        paths.append(p)
    views = providers.pick_views(paths, 4, 2048)
    assert len(views) == 4 and all(v[:2] == b"\xff\xd8" for v in views)


def test_customer_never_sees_service_names(client, monkeypatch):
    import processor
    m = processor.customer_message
    assert m("Tripo error 403/2010: You don't have enough credit to create this task") == \
        "Processing is temporarily unavailable. Please try again later."
    assert m("Tripo: API key rejected").startswith("Processing is temporarily unavailable")
    out = m("KIRI Engine scan failed: try more photos with good overlap")
    assert "KIRI" not in out and "more photos" in out


def test_tiers_show_price_and_quality_not_services(client, monkeypatch):
    fake = FakeProvider()
    _tier_client(client, monkeypatch, fake)
    t = {x["id"]: x for x in client.get("/tiers").json()["tiers"]}
    assert t["quick"]["price"] == "₹99" and t["quick"]["quality"] == 4 and t["hq"]["eta"]
    text = " ".join(x["name"] + x["detail"] for x in t.values()).lower()
    for name in ("tripo", "kiri", "trellis", "fal"):
        assert name not in text



# ---------- accounts and points ----------
def _account(c):
    r = c.post("/accounts").json()
    return {"Authorization": f"Bearer {r['token']}"}


def _buy(c, h, product, token):
    return c.post("/account/purchase", json={"product_id": product, "token": token}, headers=h)


def test_buy_points_and_history(client):
    c = client
    h = _account(c)
    w = c.get("/account", headers=h).json()
    assert w["balance"] == 0 and [p["points"] for p in w["packs"]] == [100, 300, 1000]
    assert w["packs"][0] == {"product_id": "points_100", "points": 100, "price": "₹99"}
    assert _buy(c, h, "points_100", "tok-1").json() == {"credited": 100, "balance": 100}
    # The same purchase can't be credited twice (e.g. app retried after a timeout).
    assert _buy(c, h, "points_100", "tok-1").json() == {"credited": 0, "balance": 100}
    assert _buy(c, h, "free_points", "tok-2").status_code == 400
    assert c.get("/account").status_code == 401
    assert c.get("/account", headers={"Authorization": "Bearer nope"}).status_code == 401
    hist = c.get("/account/history", headers=h).json()
    assert hist["balance"] == 100 and [e["amount"] for e in hist["entries"]] == [100]


def test_scan_spends_points(client, monkeypatch):
    import processor
    fake = FakeProvider(polls_before_done=0)
    c = _tier_client(client, monkeypatch, fake)
    h = _account(c)
    job = c.post("/jobs", json={"tier": "quick"}, headers=h).json()["job_id"]
    files = [("photos", (f"p{i}.jpg", _real_jpeg(i), "image/jpeg")) for i in range(12)]
    c.post(f"/jobs/{job}/photos", files=files, headers=h)
    assert c.get(f"/jobs/{job}").json()["points"] == 99

    r = c.post(f"/jobs/{job}/process", headers=h)
    assert r.status_code == 402 and "needs 99, you have 0" in r.json()["detail"]
    _buy(c, h, "points_100", "tok-a")
    other = _account(c)
    _buy(c, other, "points_300", "tok-b")
    assert c.post(f"/jobs/{job}/process", headers=other).status_code == 403   # not your scan
    assert c.post(f"/jobs/{job}/process", headers=h).json() == {"status": "queued"}
    assert c.post(f"/jobs/{job}/process", headers=h).json()["status"] == "queued"  # no double charge
    assert c.get("/account", headers=h).json()["balance"] == 1
    processor.run_once()
    processor.run_once()
    assert c.get(f"/jobs/{job}").json()["status"] == "done"
    notes = [(e["amount"], e["note"]) for e in c.get("/account/history", headers=h).json()["entries"]]
    assert notes == [(-99, "Standard scan"), (100, "Bought 100 points")]


def test_failed_scan_refunds_points(client, monkeypatch):
    import processor
    fake = FakeProvider(outcome="failed", polls_before_done=0)
    c = _tier_client(client, monkeypatch, fake)
    h = _account(c)
    _buy(c, h, "points_300", "tok-c")
    job = c.post("/jobs", json={"tier": "hq"}, headers=h).json()["job_id"]
    files = [("photos", (f"p{i}.jpg", _real_jpeg(i), "image/jpeg")) for i in range(20)]
    c.post(f"/jobs/{job}/photos", files=files, headers=h)
    assert c.post(f"/jobs/{job}/process", headers=h).json() == {"status": "queued"}
    assert c.get("/account", headers=h).json()["balance"] == 51
    processor.run_once()
    processor.run_once()
    assert c.get(f"/jobs/{job}").json()["status"] == "failed"
    hist = c.get("/account/history", headers=h).json()
    assert hist["balance"] == 300
    assert [e["amount"] for e in hist["entries"]] == [249, -249, 300]
    # A refunded scan can't be rerun for free.
    assert c.post(f"/jobs/{job}/process", headers=h).status_code == 409
    processor.refund(job)   # refunding twice does nothing
    assert c.get("/account", headers=h).json()["balance"] == 300



# ---------- production safety and backups ----------
def test_production_refuses_test_settings(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "DEV_BILLING", "1")
    monkeypatch.setattr(config, "SECRET_IS_DEFAULT", True)
    monkeypatch.setattr(config, "SERVICE_ACCOUNT_JSON", "")
    monkeypatch.setattr(config, "API_BASE", "http://x")
    problems = " | ".join(config.production_problems())
    monkeypatch.setattr(config, "PRIVACY_CONTACT_EMAIL", "")
    for word in ("ALLOW_DEV_BILLING", "SECRET_KEY", "service account", "https", "PRIVACY_CONTACT_EMAIL"):
        assert word in problems
    monkeypatch.setattr(config, "ENVIRONMENT", "development")
    assert config.production_problems() == []


def test_backup_snapshot_restores_points(client, monkeypatch):
    import gzip
    import sqlite3
    import backup
    import config
    h = _account(client)
    _buy(client, h, "points_300", "tok-backup")
    for _ in range(3):
        path = backup.run_once()
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    backup.prune(config.BACKUP_KEEP)
    assert len(list(backup.BACKUP_DIR.glob("app-*.db.gz"))) == 2
    restored = path.with_suffix("").with_suffix(".restored.db")
    restored.write_bytes(gzip.decompress(path.read_bytes()))
    total = sqlite3.connect(restored).execute("SELECT SUM(amount) FROM ledger").fetchone()[0]
    assert total == 300
    assert backup.upload(path) is False      # no off-site storage configured in tests


# ---------- privacy ----------
def test_privacy_notice_and_consent(client, monkeypatch):
    import config
    c = client
    monkeypatch.setattr(config, "PRIVACY_CONTACT_EMAIL", "privacy@example.com")
    page = c.get("/privacy")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    for text in ("privacy@example.com", "90 days", "Delete my data", "Data Protection Board"):
        assert text in page.text
    assert "tripo" not in page.text.lower() and "kiri" not in page.text.lower()

    h = _account(c)
    acc = c.get("/account", headers=h).json()
    assert acc["consent_version"] is None and acc["privacy_version"] == config.PRIVACY_VERSION
    assert c.post("/account/consent", json={"version": "old"}, headers=h).status_code == 409
    assert c.post("/account/consent", json={"version": config.PRIVACY_VERSION}, headers=h).status_code == 200
    assert c.get("/account", headers=h).json()["consent_version"] == config.PRIVACY_VERSION


def test_delete_my_data(client, monkeypatch):
    import config
    import db
    c = client
    h = _account(c)
    account = c.get("/account", headers=h).json()["account_id"]
    _buy(c, h, "points_300", "tok-del")
    job = c.post("/jobs", headers=h).json()["job_id"]
    c.post(f"/jobs/{job}/photos", files=[("photos", ("p.jpg", b"\xff\xd8x", "image/jpeg"))], headers=h)
    assert (config.JOBS_DIR / job / "photos").is_dir()

    r = c.delete("/account", headers=h).json()
    assert r == {"deleted": True, "forfeited_points": 300}
    assert not (config.JOBS_DIR / job).exists()          # photos gone
    assert c.get(f"/jobs/{job}").status_code == 404
    assert c.get("/account", headers=h).status_code == 401   # token revoked
    # Payment records remain, and the balance is closed to zero.
    kinds = [e["kind"] for e in db.history(account)]
    assert kinds == ["closed", "purchase"] and db.balance(account) == 0
    # A purchase token can't be replayed onto a new account after deletion.
    h2 = _account(c)
    assert _buy(c, h2, "points_300", "tok-del").json()["credited"] == 0


def test_files_deleted_after_retention(client, monkeypatch):
    import time

    import config
    import db
    import processor
    c = client
    old = _new_job(c)
    new = _new_job(c)
    db.update_job(old, created_at=time.time() - (config.RETENTION_DAYS + 1) * 86400, status="done")
    assert processor.purge_expired() == 1
    assert not (config.JOBS_DIR / old).exists() and (config.JOBS_DIR / new).exists()
    st = c.get(f"/jobs/{old}").json()
    assert st["expired"] and "preview_url" not in st and "download" not in st
    assert processor.purge_expired() == 0                 # once only
