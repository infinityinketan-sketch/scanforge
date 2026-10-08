# ScanForge

Photogrammetry 3D scanning: walk around an object with an Android phone, and get a mesh
back as **GLB** (Blender) and **STL** (3D printing). Preview is free; export is a one-time
Google Play purchase per scan.

The app offers two ways to build the model (only those whose API key is set on the server):

- **Budget AI model**: open-source TRELLIS (via fal.ai) builds a rough model from 4 photos, ~$0.02.
- **Quick AI model**: Tripo builds a clean, complete textured model from 4 of the photos in 1–3
  minutes, ~$0.30. Shape details are approximated.
- **High-accuracy scan**: KIRI Engine photogrammetry from all photos, with automatic background
  removal. 10–40 minutes, about $1 per scan.

The original self-hosted pipeline (below) is still available as a fallback.

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

Without `-PapiBase` the app talks to the Colab test backend below.

## Testing on Colab (no server or RunPod needed)

Open [`colab/scanforge_colab.ipynb`](https://colab.research.google.com/github/infinityinketan-sketch/scanforge/blob/main/colab/scanforge_colab.ipynb)
in Colab on a T4 GPU and run all cells. It starts the backend, exposes it on the ngrok domain
`chamber-barber-dole.ngrok-free.dev` (the APK's default), and runs COLMAP on Colab's GPU.
Add your ngrok token as a Colab secret named `NGROK_TOKEN`. Everything is lost when the Colab session ends.

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
| `FAL_KEY` | for **Budget AI** | fal.ai TRELLIS multi-image (open source, ~$0.02/model) |
| `TRIPO_API_KEY` | for **Quick AI** | Tripo multiview-to-model (~$0.30/model) |
| `KIRI_API_KEY` | for **High-accuracy** | KIRI Engine Photo Scan with background removal (~$1/scan, needs 20+ photos). `KIRI_SCAN=featureless` for shiny objects |
| `DEFAULT_TIER` | | Used when the app doesn't choose. Default `quick` |
| `TIER_POINTS` | | Points per scan. Default `basic=29,quick=99,hq=249` (1 point = ₹1) |
| `POINT_PACKS`, `PACK_PRICES` | | Play products sold as point packs and their price labels |
| `WELCOME_POINTS` | | Free points for a new account. Default 0 |
| `TIER_PRODUCTS` | | Play product per option, bought **before** processing. Default `basic=scan_basic,quick=scan_quick,hq=scan_hq` |
| `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID` | optional | Self-hosted GPU pipeline. Without them, self-hosted jobs wait for a manual/Colab worker |
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

1. Create consumable in-app products for the point packs: `points_100`, `points_300`, `points_1000`
   (defaults ₹99 / ₹279 / ₹849; set `POINT_PACKS` and `PACK_PRICES` to match what you configure).
   Customers buy points into their wallet; each scan spends points (`TIER_POINTS`, default
   Basic 29, Standard 99, Premium 249) before processing, and a failed scan refunds them
   automatically. A purchase the backend refuses is never acknowledged, so Play refunds it.
   (`scan_*` per-scan products and `export_unlock` remain only for older app versions and the
   self-hosted pipeline.)
2. Link a Google Cloud service account under *Users and permissions* and give its JSON to the backend.
3. Billing only works for builds installed from a Play testing track, signed with the upload key.

## Going live

The Colab notebook is for testing only (it stops when the tab closes and forgets everything on
restart). Production runs on **Render** from `render.yaml`:

1. **Backend.** In Render: *New > Blueprint*, pick this repo. It creates an always-on Starter
   instance with a 10 GB persistent disk at `/var/data` (database, photos, models) and
   `ENVIRONMENT=production`, which refuses to start with test payments on, a default
   `SECRET_KEY`, no Google service account, or a non-https `API_BASE`. Paste the secret values:
   `GOOGLE_SERVICE_ACCOUNT_JSON`, `FAL_KEY` / `TRIPO_API_KEY` / `KIRI_API_KEY`, and the
   `BACKUP_S3_*` settings. If Render names the service differently, update `API_BASE`.
   Pushes to `main` redeploy automatically; `/healthz` is the health check.
2. **Backups.** The database (accounts, points ledger, scans) is snapshotted every 24 h
   (`BACKUP_EVERY_HOURS`), the newest 14 kept in `/var/data/backups`, and each copy uploaded to
   any S3-compatible bucket (Cloudflare R2, Backblaze B2, AWS S3) when `BACKUP_S3_ENDPOINT`,
   `BACKUP_S3_BUCKET`, `BACKUP_S3_KEY_ID` and `BACKUP_S3_SECRET` are set. Restore: stop the
   service, gunzip a copy over `/var/data/app.db`, start it again.
3. **Release app.** Create an upload key once and add four repository secrets
   (*Settings > Secrets and variables > Actions*): `ANDROID_KEYSTORE_BASE64` (the .jks file,
   base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`. Then run
   *Actions > build-release* with the production URL. It produces a signed `.aab` for Play and
   a signed `.apk`, with a higher `versionCode` on every run. Release builds have the
   test-payment path switched off. Keep the key file and passwords safe; with Play App Signing a
   lost upload key can be reset through Play support.
4. **Play Console.** Create the point packs (`points_100`, `points_300`, `points_1000`), link
   the service account with "View financial data", upload the `.aab` to the internal testing
   track, and test real purchases there before production.
5. **Alerts.** Add an uptime check on `https://<your-service>/healthz` and turn on low-credit
   alerts in the Tripo, fal.ai and KIRI dashboards.
