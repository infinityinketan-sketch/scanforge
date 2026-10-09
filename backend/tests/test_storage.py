"""Direct-to-bucket uploads and downloads (Cloudflare R2 in production), against a local
S3 server from moto. Skipped when moto isn't installed."""
import importlib
import socket
import sys

import pytest

pytest.importorskip("moto.server")
import httpx  # noqa: E402

from test_api import FakeProvider, _account, _buy, _real_jpeg, _tier_client  # noqa: E402

BUCKET = "scanforge-test"


@pytest.fixture(scope="module")
def s3_endpoint():
    from moto.server import ThreadedMotoServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture()
def r2(tmp_path, monkeypatch, s3_endpoint):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    for k, v in {"DATA_DIR": str(tmp_path), "SECRET_KEY": "test-secret", "API_BASE": "http://testserver",
                 "ALLOW_DEV_BILLING": "1", "SCANFORGE_NO_PROCESSOR": "1", "R2_ENDPOINT": s3_endpoint,
                 "R2_BUCKET": BUCKET, "R2_KEY_ID": "test", "R2_SECRET": "test",
                 "R2_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    for k in ("TRIPO_API_KEY", "FAL_KEY", "KIRI_API_KEY", "RUNPOD_ENDPOINT_ID"):
        monkeypatch.delenv(k, raising=False)
    for m in ("config", "db", "billing", "providers", "processor", "backup", "storage", "privacy", "main"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    import storage
    store = storage.get()
    try:
        store.s3.create_bucket(Bucket=BUCKET)
    except store.s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    store.delete([o["key"] for o in store.list("")])
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c, store


def _put(url, data, ctype="image/jpeg"):
    return httpx.put(url, content=data, headers={"Content-Type": ctype}, trust_env=False)


def test_direct_upload_process_and_download(r2, monkeypatch):
    import config
    import processor
    c, store = r2
    fake = FakeProvider(polls_before_done=0)
    _tier_client(c, monkeypatch, fake)
    h = _account(c)
    _buy(c, h, "points_100", "tok-r2")
    job = c.post("/jobs", json={"tier": "quick"}, headers=h).json()["job_id"]

    # Someone else can't get upload links for this scan.
    assert c.post(f"/jobs/{job}/upload-urls", json={"count": 1}, headers=_account(c)).status_code == 403
    r = c.post(f"/jobs/{job}/upload-urls", json={"count": 10}, headers=h).json()
    assert r["direct"] and len(r["urls"]) == 10 and r["content_type"] == "image/jpeg"
    for i, url in enumerate(r["urls"]):
        assert "Authorization" not in url and _put(url, _real_jpeg(i)).status_code == 200
    assert c.post(f"/jobs/{job}/photos/complete", headers=h).json() == {"n_photos": 10, "rejected": 0}
    assert c.post(f"/jobs/{job}/photos/complete", headers=h).json()["n_photos"] == 10   # repeatable
    assert c.get(f"/jobs/{job}").json()["n_photos"] == 10

    assert c.post(f"/jobs/{job}/process", headers=h).json() == {"status": "queued"}
    processor.run_once()                       # pulls the photos in and submits
    assert len(fake.submitted) == 10
    processor.run_once()                       # done: outputs go to the bucket
    keys = {o["key"] for o in store.list(f"jobs/{job}/")}
    assert {f"jobs/{job}/model.glb", f"jobs/{job}/model.stl", f"jobs/{job}/preview.glb"} <= keys
    assert not (config.JOBS_DIR / job).exists()   # server disk freed

    st = c.get(f"/jobs/{job}").json()
    assert st["status"] == "done"
    pv = httpx.get(st["preview_url"], trust_env=False)
    assert pv.status_code == 200 and pv.content[:4] == b"glTF"
    dl = httpx.get(st["download"]["stl"], trust_env=False)
    assert dl.status_code == 200 and f"scanforge_{job}.stl" in dl.headers["content-disposition"]
    assert httpx.get(st["download"]["glb"], trust_env=False).content[:4] == b"glTF"


def test_oversized_and_empty_uploads_are_dropped(r2, monkeypatch):
    import config
    c, store = r2
    monkeypatch.setattr(config, "MAX_PHOTO_BYTES", 2000)
    job = c.post("/jobs").json()["job_id"]
    urls = c.post(f"/jobs/{job}/upload-urls", json={"count": 3}).json()["urls"]
    _put(urls[0], b"x" * 100)
    _put(urls[1], b"x" * 5000)
    _put(urls[2], b"")
    assert c.post(f"/jobs/{job}/photos/complete").json() == {"n_photos": 1, "rejected": 2}
    assert len(store.list(f"jobs/{job}/photos/")) == 1


def test_upload_link_limits(r2, monkeypatch):
    import config
    c, _ = r2
    monkeypatch.setattr(config, "MAX_PHOTOS", 5)
    job = c.post("/jobs").json()["job_id"]
    assert c.post(f"/jobs/{job}/upload-urls", json={"count": 6}).status_code == 413
    assert c.post(f"/jobs/{job}/upload-urls", json={"count": 0}).status_code == 400
    # The content type is signed, so R2 refuses anything but a JPEG upload (moto doesn't check).
    url = c.post(f"/jobs/{job}/upload-urls", json={"count": 1}).json()["urls"][0]
    assert "X-Amz-SignedHeaders=content-type%3Bhost" in url


def test_local_mode_says_upload_through_api(tmp_path, monkeypatch):
    monkeypatch.delenv("R2_BUCKET", raising=False)
    for m in ("config", "storage"):
        sys.modules.pop(m, None)
    import storage
    assert storage.get().direct is False


def test_delete_my_data_and_retention_clear_the_bucket(r2):
    import time

    import config
    import db
    import processor
    c, store = r2
    h = _account(c)
    mine = c.post("/jobs", headers=h).json()["job_id"]
    old = c.post("/jobs").json()["job_id"]
    for job, hdr in ((mine, h), (old, {})):
        url = c.post(f"/jobs/{job}/upload-urls", json={"count": 1}, headers=hdr).json()["urls"][0]
        _put(url, _real_jpeg(1))
    assert len(store.list("jobs/")) == 2

    assert c.delete("/account", headers=h).json()["deleted"]
    assert store.list(f"jobs/{mine}/") == []

    db.update_job(old, created_at=time.time() - (config.RETENTION_DAYS + 1) * 86400)
    assert processor.purge_expired() == 1
    assert store.list("jobs/") == []
