"""Where photos and models live.

LocalStore  files on the server's disk (Colab, tests, self-hosted pipeline). The app uploads
            through the API and downloads through signed API links.
R2Store     Cloudflare R2 (or any S3-compatible bucket). The phone uploads photos and downloads
            models straight from the bucket with short-lived presigned URLs, so the files never
            pass through the server (Render bills outbound bandwidth beyond 5 GB/month; R2 has
            no egress fees). The server only pulls photos in to process them and pushes results.

Keys: jobs/<job>/photos/<nnnn>.jpg, jobs/<job>/model.glb, model.stl, preview.glb
"""
from __future__ import annotations

import shutil
from pathlib import Path

import config

PHOTO_TYPE = "image/jpeg"
OUTPUTS = {"model.glb": "model/gltf-binary", "model.stl": "model/stl", "preview.glb": "model/gltf-binary"}


def photo_prefix(job_id: str) -> str:
    return f"jobs/{job_id}/photos/"


class LocalStore:
    direct = False

    def delete_job(self, job_id: str):
        shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)


class R2Store:
    direct = True

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = config.R2_BUCKET
        self.s3 = boto3.client(
            "s3",
            endpoint_url=config.R2_ENDPOINT or None,
            aws_access_key_id=config.R2_KEY_ID,
            aws_secret_access_key=config.R2_SECRET,
            region_name=config.R2_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    # ---- presigned links for the phone ----
    def put_url(self, key: str, content_type: str, ttl: int = 3600) -> str:
        return self.s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=ttl)

    def get_url(self, key: str, ttl: int = 3600, filename: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    # ---- server side ----
    def list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kw)
            out += [{"key": o["Key"], "size": o["Size"]} for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return out
            token = page["NextContinuationToken"]

    def delete(self, keys: list[str]):
        for i in range(0, len(keys), 1000):
            chunk = keys[i:i + 1000]
            if chunk:
                self.s3.delete_objects(Bucket=self.bucket,
                                       Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def upload(self, path: Path, key: str, content_type: str):
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def download(self, key: str, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.s3.download_file(self.bucket, key, str(path))

    def delete_job(self, job_id: str):
        self.delete([o["key"] for o in self.list(f"jobs/{job_id}/")])
        shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)


_store = None


def get():
    global _store
    if _store is None:
        _store = R2Store() if config.R2_BUCKET else LocalStore()
    return _store


def reset():
    """Tests switch storage settings between cases."""
    global _store
    _store = None
