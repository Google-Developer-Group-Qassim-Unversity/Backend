import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.wallet_signer import generate_google_wallet_pass_url
from tests.wallet_support import sentry_event


def _private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _configure_google_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer_id = "3388000000022212345"
    monkeypatch.setenv("GOOGLE_WALLET_ISSUER_ID", issuer_id)
    monkeypatch.setenv("GOOGLE_WALLET_CLASS_ID", f"{issuer_id}.gdgq-card")
    monkeypatch.setenv("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL", "wallet@example.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_WALLET_PRIVATE_KEY", _private_key_pem())


def test_google_wallet_jwt_matches_current_google_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google_wallet(monkeypatch)

    save_url = generate_google_wallet_pass_url(
        {
            "uuid": "2b53403d-2248-47d7-90f6-fc1b2ad3429a",
            "fullName": "عضو تجريبي",
            "themeId": "gdg-blue",
            "uniId": "451000000",
        }
    )

    token = save_url.rsplit("/", 1)[-1]
    claims = jwt.decode(token, options={"verify_signature": False})
    google_object = claims["payload"]["genericObjects"][0]

    assert claims["aud"] == "google"
    assert claims["typ"] == "savetowallet"
    assert claims["origins"] == ["https://gdg-q.com"]
    assert claims["payload"]["genericClasses"][0]["id"] == "3388000000022212345.gdgq-card"
    assert google_object["state"] == "ACTIVE"
    assert google_object["classId"] == "3388000000022212345.gdgq-card"
    assert google_object["barcode"]["type"] == "QR_CODE"
    assert google_object["hexBackgroundColor"] == "#BFF2FF"
    assert google_object["subheader"]["defaultValue"]["value"] == "عضو نادي قوقل للطلبة المطورين"
    # 361ac26 intentionally moved the hero art from the strip to the full-card render
    assert google_object["heroImage"]["sourceUri"]["uri"].endswith("/wallet-v2/strip-gdg-blue@3x.png")
    assert "classTemplateInfo" not in claims["payload"]["genericClasses"][0]


def test_google_wallet_rejects_google_pay_merchant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_WALLET_ISSUER_ID", "BCR2DN6DTK643EAC")
    monkeypatch.setenv("GOOGLE_WALLET_CLASS_ID", "BCR2DN6DTK643EAC.gdgq-card")
    monkeypatch.setenv("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL", "wallet@example.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_WALLET_PRIVATE_KEY", _private_key_pem())

    with pytest.raises(ValueError, match="numeric Google Wallet Issuer ID"):
        generate_google_wallet_pass_url({"uuid": "test-card"})


def test_google_wallet_rejects_missing_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer_id = "3388000000022212345"
    monkeypatch.setenv("GOOGLE_WALLET_ISSUER_ID", issuer_id)
    monkeypatch.setenv("GOOGLE_WALLET_CLASS_ID", f"{issuer_id}.gdgq-card")
    monkeypatch.setenv("GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL", "wallet@example.iam.gserviceaccount.com")
    monkeypatch.delenv("GOOGLE_WALLET_PRIVATE_KEY", raising=False)

    with pytest.raises(ValueError, match="PRIVATE_KEY is not configured"):
        generate_google_wallet_pass_url({"uuid": "test-card"})


def _ec_private_key_pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("utf-8")
    )


@pytest.mark.parametrize(
    ("broken", "message"),
    [("cut short", "is not a readable PEM private key"), ("an EC key", "must be an RSA private key")],
)
def test_a_google_wallet_key_that_does_not_load_never_reaches_sentry(
    monkeypatch: pytest.MonkeyPatch, broken: str, message: str
) -> None:
    """A ValueError naming the setting (the route's 503), and no line of the key in the event Sentry would send."""
    _configure_google_wallet(monkeypatch)
    lines = (_private_key_pem() if broken == "cut short" else _ec_private_key_pem()).splitlines()
    if broken == "cut short":
        lines = lines[:10]
    # Written with "\\n" for its line breaks, as it is in the settings.
    monkeypatch.setenv("GOOGLE_WALLET_PRIVATE_KEY", "\\n".join(lines))

    raised, event = sentry_event(lambda: generate_google_wallet_pass_url({"uuid": "test-card"}))

    cleaned = json.dumps(event, default=str)
    for line in lines[1:-1]:
        assert line not in cleaned
    assert [error["type"] for error in event["exception"]["values"]] == ["ValueError"], "not PyJWT's or cryptography's"
    assert str(raised).startswith(f"GOOGLE_WALLET_PRIVATE_KEY {message}")


def _barcode(card: dict) -> dict:
    token = generate_google_wallet_pass_url(card).rsplit("/", 1)[-1]
    claims = jwt.decode(token, options={"verify_signature": False})
    return claims["payload"]["genericObjects"][0]["barcode"]


def test_google_wallet_qr_is_the_cards_qr_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google_wallet(monkeypatch)
    uuid = "0f8c2a9d4b6e4c1a9e3f5d7b2c4a6e8f"
    qr_value = f"https://gdg-q.com/p/{uuid}?s=AbCdEfGhIjKlMnOp"

    assert _barcode({"uuid": uuid, "qrValue": qr_value})["value"] == qr_value
    assert _barcode({"uuid": uuid})["value"] == "https://gdg-q.com", "a guest pass points at the home page"
