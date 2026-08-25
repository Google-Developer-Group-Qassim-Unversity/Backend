import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.wallet_signer import generate_google_wallet_pass_url


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
    assert google_object["heroImage"]["sourceUri"]["uri"].endswith("/wallet-v2/card-gdg-blue@2x.png")
    assert "classTemplateInfo" not in claims["payload"]["genericClasses"][0]


@pytest.mark.xfail(
    reason=(
        "Issuer-ID format validation from 85901ae was dropped by 361ac26 and is deliberately not "
        "restored yet: production's GOOGLE_WALLET_ISSUER_ID is still a Google Pay merchant ID, so "
        "enforcing this would turn the Wallet endpoint into a hard error. Set a numeric Issuer ID "
        "from the Google Wallet console in Infisical, then restore the check and drop this xfail."
    ),
    strict=True,
)
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
