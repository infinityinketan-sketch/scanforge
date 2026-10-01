import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def verify_purchase(product_id: str, token: str) -> bool:
    """Verify a one-time Play Billing purchase via Play Developer API."""
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
    # purchaseState 0 = purchased
    return result.get("purchaseState") == 0
