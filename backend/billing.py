import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def verify_purchase(job_id: str, product_id: str, token: str) -> bool:
    """Verify a one-time Play Billing purchase via Play Developer API.

    Checks that the product is an export unlock, the purchase is completed, and (when the
    app attached one) that the purchase was made for this job.
    """
    if product_id not in config.EXPORT_PRODUCT_IDS:
        return False
    if not (config.SERVICE_ACCOUNT_JSON or config.SERVICE_ACCOUNT_PATH):
        # Dev mode: only allowed when explicitly enabled.
        return config.DEV_BILLING == "1"
    if config.SERVICE_ACCOUNT_JSON:
        info = json.loads(config.SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            config.SERVICE_ACCOUNT_PATH, scopes=SCOPES
        )
    svc = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
    result = (
        svc.purchases()
        .products()
        .get(packageName=config.PLAY_PACKAGE, productId=product_id, token=token)
        .execute()
    )
    # purchaseState 0 = purchased (1 = cancelled, 2 = pending)
    if result.get("purchaseState") != 0:
        return False
    owner = result.get("obfuscatedExternalProfileId")
    return owner is None or owner == job_id
