import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config

SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]


def verify_purchase(job_id: str, product_id: str, token: str) -> bool:
    """A per-scan purchase (legacy flow), tied to the job via the obfuscated profile id."""
    if product_id not in config.EXPORT_PRODUCT_IDS:
        return False
    return _verify(product_id, token, "obfuscatedExternalProfileId", job_id)


def verify_pack(account_id: str, product_id: str, token: str) -> bool:
    """A point-pack purchase, tied to the buyer's account via the obfuscated account id."""
    if product_id not in config.POINT_PACKS:
        return False
    return _verify(product_id, token, "obfuscatedExternalAccountId", account_id)


def _verify(product_id: str, token: str, owner_field: str, owner_id: str) -> bool:
    """Ask the Play Developer API whether this purchase is real, completed, and owned by owner_id."""
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
    owner = result.get(owner_field)
    return owner is None or owner == owner_id
