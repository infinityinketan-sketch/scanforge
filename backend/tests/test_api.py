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
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    for m in ("config", "db", "billing", "main"):
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
