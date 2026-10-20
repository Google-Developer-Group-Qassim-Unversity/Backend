"""The member card QR's signature (app/services/card_qr.py)."""

import base64
import hashlib
import hmac

import pytest

from app.services.card_qr import card_qr_value, card_signature, profile_url, verify_card_signature

UUID = "0f8c2a9d4b6e4c1a9e3f5d7b2c4a6e8f"
SECRET = "test-card-qr-secret-0123456789abcdef"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("CARD_QR_SECRET", SECRET)


def test_without_a_secret_the_card_keeps_todays_unsigned_link(monkeypatch):
    monkeypatch.delenv("CARD_QR_SECRET", raising=False)

    assert card_signature(UUID) is None
    assert card_qr_value(UUID) == f"https://gdg-q.com/p/{UUID}"
    assert verify_card_signature(UUID, "anything") == "unknown"


def test_the_signature_is_16_characters_of_base64url_hmac_sha256(secret):
    digest = hmac.new(SECRET.encode(), f"card:v1:{UUID}".encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")[:16]

    assert card_signature(UUID) == expected
    assert card_qr_value(UUID) == f"https://gdg-q.com/p/{UUID}?s={expected}"
    # 20 + 32 + 3 + 16: QR version 5 at level M, which the app, the website and both wallets draw.
    assert len(card_qr_value(UUID)) == 71


def test_the_signature_only_uses_url_safe_characters(secret):
    for index in range(200):
        signature = card_signature(f"{index:032x}")
        assert signature is not None
        assert set(signature) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_a_cards_own_signature_verifies_and_anything_else_does_not(secret):
    signature = card_signature(UUID)
    assert signature is not None

    assert verify_card_signature(UUID, signature) == "valid"
    assert verify_card_signature("1" * 32, signature) == "invalid", "a signature is bound to its uuid"
    assert verify_card_signature(UUID, signature[:-1]) == "invalid"
    assert verify_card_signature(UUID, "") == "invalid"
    assert verify_card_signature(UUID, "ثثثثثثثثثثثثثثثث") == "invalid", "non-ASCII input is compared, not crashed on"


def test_another_secret_makes_another_signature(secret, monkeypatch):
    first = card_signature(UUID)
    monkeypatch.setenv("CARD_QR_SECRET", SECRET + "-rotated")
    from app.config import reload_settings

    reload_settings()
    assert card_signature(UUID) != first
    assert first is not None and verify_card_signature(UUID, first) == "invalid"


def test_sharing_a_profile_stays_unsigned(secret):
    assert profile_url(UUID) == f"https://gdg-q.com/p/{UUID}"


@pytest.mark.parametrize("signature", ["\ud800" * 16, "é" * 16])
def test_a_non_ascii_signature_is_invalid_rather_than_an_error(secret, signature):
    assert verify_card_signature(UUID, signature) == "invalid"
