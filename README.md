# ScanForge

Photogrammetry 3D scanning: walk around an object with an Android phone, and get a mesh
back as **GLB** (Blender) and **STL** (3D printing). Preview is free; export is a one-time
Google Play purchase per scan.

```
Android app ──photos──▶ FastAPI backend ──job──▶ RunPod GPU worker (COLMAP + Open3D)
     ▲                       │   ▲                         │
     └──status / links───────┘   └────── GLB + STL ────────┘
```

| Folder | What it is |
|---|---|
| `app/` | Android app (Kotlin, CameraX, Retrofit, Play Billing) |
| `backend/` | FastAPI API: jobs, signed URLs, purchase verification (SQLite) |
| `pipeline/` | RunPod serverless worker image (CUDA COLMAP → Poisson mesh) |

## Android app

Built by the **build-apk** GitHub Action on every push to `app/**` (download the APK from
the run's artifacts), or run it manually with a custom backend URL.

Locally (Android Studio, or Gradle 8.7 + JDK 17 + Android SDK 34):

```bash
gradle :app:assembleDebug -PapiBase=https://scanforge-api.onrender.com -PexportProductId=export_unlock
```

Without `-PapiBase` the app talks to `http://10.0.2.2:8000` (your PC from the emulator).

## Backend

```bash
cd backend
pip install -r requirements.txt
SECRET_KEY=dev API_BASE=http://10.0.2.2:8000 ALLOW_DEV_BILLING=1 uvicorn main:app --host 0.0.0.0
python -m pytest tests     # end-to-end API tests, no GPU / Play needed
```

| Env var | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | **yes** | Signs every URL; also the admin key for `/manifest` and `/dev-pay` |
| `API_BASE` | **yes** | Public URL of this backend (the GPU worker calls back to it) |
| `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID` | for GPU processing | Without them, jobs wait for a manual/Colab worker |
| `GOOGLE_SERVICE_ACCOUNT_JSON` (or `_PATH`) | for real payments | Service account with Play Console "View financial data" |
| `PLAY_PACKAGE_NAME` | | Default `com.scanforge.app` |
| `EXPORT_PRODUCT_IDS` | | Comma-separated in-app products that unlock export. Default `export_unlock` |
| `ALLOW_DEV_BILLING` | dev only | `1` accepts any purchase when no service account is set. **Never in production** |
| `DATA_DIR` | | Where photos, models and the SQLite DB live. Default `./data` |
| `MAX_PHOTOS`, `MAX_PHOTO_MB`, `MIN_PHOTOS`, `PROCESSING_TIMEOUT` | | Limits (200, 25, 8, 1800 s) |

`render.yaml` deploys it to Render. Photos and models are stored on local disk, so the
service needs a **persistent disk** mounted at `DATA_DIR`. Without one, every redeploy or
restart wipes all scans.

## GPU pipeline

The **build-pipeline-image** Action pushes `ghcr.io/<owner>/scanforge-pipeline:latest`.
Create a RunPod serverless endpoint from that image (GPU with ≥16 GB VRAM recommended),
then set `RUNPOD_ENDPOINT_ID` and `RUNPOD_API_KEY` on the backend.

The image is based on `colmap/colmap` because it is built with CUDA. COLMAP's dense step
requires CUDA, and Ubuntu's `apt install colmap` is built without it.

## Play Console setup

1. Create a one-time in-app product with id `export_unlock` (or whatever you pass as
   `exportProductId` / `EXPORT_PRODUCT_IDS`).
2. Link a Google Cloud service account under *Users and permissions* and give its JSON to the backend.
3. Billing only works for builds installed from a Play testing track, signed with the upload key.
