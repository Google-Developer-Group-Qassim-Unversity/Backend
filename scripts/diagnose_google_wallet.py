"""One-off diagnostic: ask the Google Wallet REST API directly what's wrong
with the issuer/class/object, instead of guessing from the vague
"something went wrong" page pay.google.com shows end users.

Run from Backend/:
    infisical run --env=dev --path=/admin-backend -- uv run python scripts/diagnose_google_wallet.py
"""

import json
import sys

import google.auth.transport.requests
from google.oauth2 import service_account

from app.config import config

SCOPES = ["https://www.googleapis.com/auth/wallet_object.issuer"]
API_BASE = "https://walletobjects.googleapis.com/walletobjects/v1"


def get_session() -> google.auth.transport.requests.AuthorizedSession:
    private_key = config.GOOGLE_WALLET_PRIVATE_KEY.strip()
    if private_key.startswith('"') and private_key.endswith('"'):
        private_key = private_key[1:-1]
    private_key = private_key.replace("\\n", "\n")

    info = {
        "type": "service_account",
        "client_email": config.GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL.strip(),
        "private_key": private_key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return google.auth.transport.requests.AuthorizedSession(credentials)


def show(label: str, resp) -> None:
    print(f"\n=== {label} ===")
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)


def main() -> None:
    issuer_id = config.GOOGLE_WALLET_ISSUER_ID.strip()
    class_id = config.GOOGLE_WALLET_CLASS_ID.strip() or f"{issuer_id}.gdgq-card"
    print(f"issuer_id = {issuer_id}")
    print(f"class_id  = {class_id}")
    print(f"service_account = {config.GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL.strip()}")

    session = get_session()

    # 1. Can this service account see the issuer at all?
    show("GET issuer", session.get(f"{API_BASE}/issuer/{issuer_id}"))

    # 2. Does the class exist, and what does Google say its real state is?
    show("GET genericClass", session.get(f"{API_BASE}/genericClass/{class_id}"))

    # 3. List every class this service account can see under this issuer -
    #    confirms whether "gdgq-card" is even the class we think it is.
    show("LIST genericClass", session.get(f"{API_BASE}/genericClass", params={"issuerId": issuer_id}))

    if len(sys.argv) > 1:
        object_id = sys.argv[1]
        show("GET genericObject", session.get(f"{API_BASE}/genericObject/{object_id}"))
    else:
        print("\n(pass a generic object id as argv[1] to also fetch a specific object)")


if __name__ == "__main__":
    main()
