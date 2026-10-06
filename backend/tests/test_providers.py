"""Provider clients against mock HTTP servers that answer like the documented APIs."""
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def prov(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    for m in ("config", "providers"):
        sys.modules.pop(m, None)
    import providers
    return providers


def _photos(tmp_path, n):
    from PIL import Image
    out = []
    for i in range(n):
        p = tmp_path / f"{i:03d}.jpg"
        Image.effect_noise((80, 60), 30 + i).convert("RGB").save(p)
        out.append(p)
    return out


_REAL_CLIENT = httpx.Client


def _mock(monkeypatch, handler):
    real = _REAL_CLIENT

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", client)


def test_tripo(prov, monkeypatch, tmp_path):
    seen = {"files": 0}

    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "Bearer tk"
        if req.url.path == "/v3/files":
            seen["files"] += 1
            assert b'name="file"' in req.content
            return httpx.Response(200, json={"code": 0, "data": {"file_token": f"file_{seen['files']}"}})
        if req.url.path == "/v3/generation/multiview-to-model":
            seen["body"] = json.loads(req.content)
            return httpx.Response(201, json={"code": 0, "data": {"task_id": "task_abc"}})
        if req.url.path == "/v3/tasks/task_abc":
            seen["polls"] = seen.get("polls", 0) + 1
            if seen["polls"] == 1:
                return httpx.Response(200, json={"code": 0, "data": {"status": "running", "progress": 55}})
            return httpx.Response(200, json={"code": 0, "data": {
                "status": "success", "progress": 100,
                "output": {"model_url": "https://cdn.tripo3d.ai/output/model_pbr.glb"}}})
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    t = prov.Tripo("tk")
    assert t.submit(_photos(tmp_path, 30)) == "task_abc"
    body = seen["body"]
    assert seen["files"] == 4
    assert body["inputs"] == [{"front": "file_1"}, {"left": "file_2"}, {"back": "file_3"}, {"right": "file_4"}]
    assert body["model"] == "v3.1-20260211" and body["texture"] is True
    assert t.poll("task_abc") == prov.Poll("running", 55)
    assert t.poll("task_abc").url.endswith("model_pbr.glb")


def test_tripo_failure_and_bad_key(prov, monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"code": 0, "data": {"status": "failed"}}))
    assert prov.Tripo("k").poll("t").state == "failed"
    _mock(monkeypatch, lambda r: httpx.Response(401, json={"code": 1002, "message": "bad key"}))
    with pytest.raises(prov.ProviderError, match="key"):
        prov.Tripo("k").poll("t")


def test_kiri(prov, monkeypatch, tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "Bearer kk"
        p = req.url.path
        if p == "/api/v1/open/photo/image":
            seen["n_files"] = req.content.count(b'name="imagesFiles"')
            seen["mask"] = b'name="isMask"\r\n\r\n1' in req.content
            seen["fmt"] = b'name="fileFormat"\r\n\r\nglb' in req.content
            return httpx.Response(200, json={"code": 0, "msg": "success", "ok": True,
                                             "data": {"serialize": "abc123", "calculateType": 1}})
        if p == "/api/v1/open/model/getStatus":
            assert req.url.params["serialize"] == "abc123"
            seen["status_calls"] = seen.get("status_calls", 0) + 1
            st = 0 if seen["status_calls"] == 1 else 2
            return httpx.Response(200, json={"code": 0, "data": {"serialize": "abc123", "status": st}, "ok": True})
        if p == "/api/v1/open/model/getModelZip":
            return httpx.Response(200, json={"code": 0, "data": {"modelUrl": "https://dl/x.zip", "serialize": "abc123"}})
        return httpx.Response(404)

    _mock(monkeypatch, handler)
    k = prov.Kiri("kk")
    with pytest.raises(prov.ProviderError, match="20"):
        k.submit(_photos(tmp_path, 12))
    assert k.submit(_photos(tmp_path, 25)) == "abc123"
    assert seen == {"n_files": 25, "mask": True, "fmt": True}
    assert k.poll("abc123").state == "running"
    done = k.poll("abc123")
    assert done.state == "success" and done.url == "https://dl/x.zip"

    # The zip may hold the GLB in a subfolder alongside other files.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
        z.writestr("scan/model.glb", b"glTF-fake")
    monkeypatch.setattr(prov, "_download", lambda url, dest: Path(dest).write_bytes(buf.getvalue()))
    out = tmp_path / "m.glb"
    k.fetch("https://dl/x.zip", out)
    assert out.read_bytes() == b"glTF-fake"


def test_kiri_expired_and_no_credit(prov, monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(200, json={"code": 0, "data": {"status": 4}}))
    assert prov.Kiri("k").poll("s").state == "failed"
    _mock(monkeypatch, lambda r: httpx.Response(403, json={"code": 403, "msg": "insufficient"}))
    with pytest.raises(prov.ProviderError, match="credit"):
        prov.Kiri("k").poll("s")


def test_fal_trellis(prov, monkeypatch, tmp_path):
    def handler(req: httpx.Request):
        assert req.headers["authorization"] == "Key fk"
        if req.method == "POST":
            body = json.loads(req.content)
            assert len(body["image_urls"]) == 4 and body["image_urls"][0].startswith("data:image/jpeg;base64,")
            return httpx.Response(200, json={"request_id": "r1", "status_url": "https://queue.fal.run/x/requests/r1/status",
                                             "response_url": "https://queue.fal.run/x/requests/r1"})
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"model_mesh": {"url": "https://fal.media/m.glb"}})

    _mock(monkeypatch, handler)
    f = prov.FalTrellis("fk")
    task = f.submit(_photos(tmp_path, 16))
    assert f.poll(task) == prov.Poll("success", 100, url="https://fal.media/m.glb")
