"""Shared set-up for the Apple Wallet tests: a throwaway Pass Type ID certificate, pass updates switched on.

No test talks to Apple: the certificate is self-signed per run, and pass update
pushes go to an ``httpx.MockTransport`` client put in ``app.services.wallet_push``.
"""

import base64
import io
import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, pkcs12
from cryptography.x509.oid import NameOID
from sentry_sdk.utils import event_from_exception

from app.config import reload_settings
from app.services.push import redact
from tests.push_support import FakeApns

P12_PASSWORD = "test-password"
WEB_SERVICE_URL = "https://api.example.test/wallet/apple"
AUTH_SECRET = "test-pass-auth-secret-0123456789abcdef"


def self_signed(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, certificate


@pytest.fixture
def signing_certs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Pass Type ID p12 and a WWDR certificate, both self-signed, in the settings the signer reads."""
    key, certificate = self_signed("Pass Type ID: test")
    _, wwdr = self_signed("Test WWDR")
    p12 = pkcs12.serialize_key_and_certificates(
        b"pass", key, certificate, None, BestAvailableEncryption(P12_PASSWORD.encode())
    )
    monkeypatch.setenv("APPLE_P12_BASE64", base64.b64encode(p12).decode())
    monkeypatch.setenv("APPLE_P12_PASSWORD", P12_PASSWORD)
    monkeypatch.setenv("APPLE_WWDR_BASE64", base64.b64encode(wwdr.public_bytes(Encoding.DER)).decode())
    reload_settings()


def sentry_event(call: Callable[[], object]) -> tuple[Exception, dict]:
    """What ``call`` raises, and the event Sentry would send for it (``before_send`` applied).

    Caught here, as the app's error handler would catch it, so the event's frames start here: the
    test's own locals, which hold the secret, are not among them.
    """
    try:
        call()
    except Exception as exc:
        event, _ = event_from_exception(exc)
        return exc, redact.before_send(event)
    raise AssertionError("nothing was raised")


def unpack(pkpass: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(pkpass)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def pass_json(pkpass: bytes) -> dict:
    return json.loads(unpack(pkpass)["pass.json"])


@pytest.fixture
def pass_updates_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web service configured: its URL and the token secret."""
    monkeypatch.setenv("APPLE_PASS_WEB_SERVICE_URL", WEB_SERVICE_URL + "/")
    monkeypatch.setenv("APPLE_PASS_AUTH_SECRET", AUTH_SECRET)
    reload_settings()


@pytest.fixture
def pass_apns():
    """A fake APNs for pass update pushes. ``answer(token, (status, json), ...)`` as in tests/push_support.py."""
    from app.services import wallet_push

    fake = FakeApns()
    wallet_push.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    yield fake
    wallet_push.set_client(None)
