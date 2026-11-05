"""The Apple Wallet pass update push client: the certificate, the request, and what stays out of logs.

No database and no Apple: the certificate is self-signed, the transport a mock.
The delivery rules (retries, dead tokens, the welcome) are in
tests/routers/test_wallet_passes.py.
"""

import asyncio
import base64
import json
import os
import ssl
import stat
import tempfile
from pathlib import Path

import httpx
import pytest

from app import wallet_signer
from app.config import config, reload_settings
from app.services import wallet_push
from app.services.push import clock, redact
from app.wallet_signer import generate_apple_pkpass
from tests.push_support import FakeApns
from tests.wallet_support import sentry_event

PUSH = "ab" * 32


@pytest.fixture
def spy_on_load(monkeypatch):
    """What the TLS context was loaded from: the file's mode and whether the key in it was encrypted."""
    seen: dict = {}
    original = ssl.SSLContext.load_cert_chain

    def spy(self, certfile, keyfile=None, password=None):
        seen["path"] = str(certfile)
        seen["mode"] = stat.S_IMODE(os.stat(certfile).st_mode)
        text = Path(certfile).read_text()
        seen["encrypted"] = "BEGIN ENCRYPTED PRIVATE KEY" in text and "BEGIN PRIVATE KEY" not in text
        seen["certificate"] = "BEGIN CERTIFICATE" in text
        return original(self, certfile, keyfile, password)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", spy)
    return seen


def test_without_memfd_the_identity_lives_in_a_0600_file_only_while_it_loads(
    signing_certs, spy_on_load, tmp_path, monkeypatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delattr(os, "memfd_create", raising=False)

    context = wallet_push.ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert spy_on_load["path"].startswith(str(tmp_path))
    assert spy_on_load["mode"] == 0o600
    assert spy_on_load["encrypted"] and spy_on_load["certificate"]
    assert list(tmp_path.iterdir()) == [], "deleted as soon as it was loaded"


@pytest.mark.skipif(not hasattr(os, "memfd_create"), reason="memfd is Linux only")
def test_with_memfd_the_identity_never_touches_a_disk(signing_certs, spy_on_load, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    wallet_push.ssl_context()

    assert spy_on_load["path"].startswith("/proc/self/fd/")
    assert spy_on_load["encrypted"]
    assert list(tmp_path.iterdir()) == []


def test_without_a_certificate_the_client_says_so_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("APPLE_P12_BASE64", raising=False)
    monkeypatch.setenv("APPLE_P12_PASSWORD", "test-password")
    monkeypatch.setenv("APPLE_P12_PATH", str(tmp_path / "missing.p12"))
    reload_settings()

    with pytest.raises(wallet_push.PassPushNotConfigured, match="missing"):
        wallet_push.ssl_context()
    assert list(tmp_path.iterdir()) == []


def test_the_client_is_built_once_per_worker(signing_certs, monkeypatch):
    built = []
    real = wallet_push.ssl_context
    monkeypatch.setattr(wallet_push, "ssl_context", lambda: built.append(1) or real())
    wallet_push.set_client(None)
    try:
        client = wallet_push.get_client()
        assert wallet_push.get_client() is client
        assert built == [1]
    finally:
        asyncio.run(wallet_push.close_client())


def test_a_pass_push_is_an_empty_dictionary_on_the_pass_type_topic(frozen):
    fake = FakeApns()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))

    answer = asyncio.run(wallet_push.send(client, PUSH))

    assert answer.status == 200
    [request] = fake.requests
    assert request.url == httpx.URL(f"https://api.push.apple.com/3/device/{PUSH}"), "production: no sandbox for passes"
    assert request.method == "POST"
    assert request.headers["apns-topic"] == "pass.pass.com.gdg-q.wallet"
    assert int(request.headers["apns-expiration"]) == clock.epoch(frozen.now) + 24 * 3600
    assert json.loads(request.content) == {} and request.content == b"{}"
    assert "apns-push-type" not in request.headers and "apns-priority" not in request.headers


def test_apples_refusal_is_read_with_its_reason():
    fake = FakeApns()
    fake.answer(PUSH, (410, {"reason": "Unregistered", "timestamp": 1784538000000}))
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))

    answer = asyncio.run(wallet_push.send(client, PUSH))

    assert (answer.status, answer.reason, answer.timestamp_ms) == (410, "Unregistered", 1784538000000)
    assert len(fake.requests) == 1, "only a dropped connection is tried again at once"


def test_a_registration_owed_a_push_never_shows_its_token():
    due = wallet_push.Due("registration-id", 7, PUSH, 1)

    assert PUSH not in repr(due)


# ---------------------------------------------------------------- redaction


def test_the_device_library_id_is_hidden_in_paths():
    path = "/wallet/apple/v1/devices/6d1c1a6e7f3b4b0f9c1e2d3a4b5c6d7e/registrations/pass.x/GDGQ-ABCD1234"

    assert redact.scrub(path) == "/wallet/apple/v1/devices/[device]/registrations/pass.x/GDGQ-ABCD1234"
    assert redact.scrub(f"https://api.gdg-q.com{path}?x=1").endswith(
        "/v1/devices/[device]/registrations/pass.x/GDGQ-ABCD1234?x=1"
    )


def test_sentry_events_lose_the_push_token_and_the_authorization():
    event = {
        "request": {"url": "https://api.gdg-q.com/wallet/apple/v1/devices/abc123/x", "data": {"pushToken": PUSH}},
        "exception": {"values": [{"stacktrace": {"frames": [{"vars": {"authorization": "ApplePass secret-token"}}]}}]},
    }

    cleaned = redact.before_send(event)

    assert PUSH not in json.dumps(cleaned) and "secret-token" not in json.dumps(cleaned)
    assert "abc123" not in json.dumps(cleaned)
    assert cleaned["request"]["data"]["pushToken"] == "[Filtered]"


def test_sentry_events_lose_the_wallet_device_id_and_the_push_token_in_every_shape():
    """As Sentry hands them to ``before_send``: frame locals already serialized, strings in their reprs."""
    device = "6d1c1a6e7f3b4b0f9c1e2d3a4b5c6d7e"
    frames = [
        {
            "module": "app.routers.wallet",
            "vars": {
                "device_library_id": f"'{device}'",
                "body": f"WalletPassRegistrationRequest(pushToken='{PUSH}')",
                "serial_number": "'GDGQ-ABCD1234'",
            },
        },
        {"module": "app.DB.wallet_passes", "vars": {"values": {"push_token": f"'{PUSH}'", "member_id": "7"}}},
        {"module": "sqlalchemy.engine.base", "vars": {"parameters": f"('{device}', '{PUSH}')"}},
        {"module": "pymysql.cursors", "vars": {"query": f"\"INSERT ... VALUES ('{device}', '{PUSH}')\""}},
    ]
    event = {
        "exception": {"values": [{"value": f"{{'device_library_id': '{device}'}}", "stacktrace": {"frames": frames}}]}
    }

    cleaned = redact.before_send(event)

    assert device not in json.dumps(cleaned) and PUSH not in json.dumps(cleaned)
    route, queries, *library = cleaned["exception"]["values"][0]["stacktrace"]["frames"]
    assert route["vars"]["serial_number"] == "'GDGQ-ABCD1234'", "the rest of our frames' locals stay"
    assert route["vars"]["body"] == "WalletPassRegistrationRequest(pushToken='[Filtered]')"
    assert queries["vars"]["values"] == {"push_token": "[Filtered]", "member_id": "7"}
    assert [frame.get("vars") for frame in library] == [None, None], "the database library's frames lose their locals"


def test_sentry_events_lose_the_pass_certificate_and_its_passwords():
    """A failed certificate load (a wrong APPLE_P12_PASSWORD) must not ship the key and its password."""
    frames = [
        {
            "module": "app.wallet_signer",
            "vars": {"p12_password": "'hunter2'", "p12_password_str": "'hunter2'", "p12_bytes": "b'0\\x82'"},
        },
        {"module": "app.services.wallet_push", "vars": {"pem": "b'-----BEGIN'", "password": "b'one-time'"}},
        {"module": "app.services.wallet_push", "vars": {"private_key": "<RSAPrivateKey>", "context": "<SSLContext>"}},
    ]
    event = {"exception": {"values": [{"stacktrace": {"frames": frames}}]}}

    cleaned = json.dumps(redact.before_send(event))

    for secret in ("hunter2", "0\\\\x82", "BEGIN", "one-time", "RSAPrivateKey"):
        assert secret not in cleaned, secret
    assert "<SSLContext>" in cleaned, "other locals stay"


@pytest.mark.parametrize("broken", ["cut short", "not ASCII"])
def test_a_broken_p12_base64_never_sends_the_certificate_to_sentry(signing_certs, monkeypatch, broken):
    """APPLE_P12_BASE64 cut short, or pasted with typographic quotes (not ASCII, so the decoder refuses it):
    a clean ValueError, and the event Sentry would send holds no part of the p12 or its key."""
    p12_base64 = config.APPLE_P12_BASE64
    assert p12_base64
    value = p12_base64[:-3] if broken == "cut short" else "\u201c" + p12_base64 + "\u201d"
    monkeypatch.setenv("APPLE_P12_BASE64", value)
    reload_settings()

    raised, event = sentry_event(lambda: generate_apple_pkpass({"uuid": "2b53403d-2248-47d7-90f6-fc1b2ad3429a"}))

    cleaned = json.dumps(event, default=str)
    for start in range(0, len(value) - 32, 256):
        assert value[start : start + 32] not in cleaned, f"the p12's characters {start}-{start + 32}"
    assert [error["type"] for error in event["exception"]["values"]] == ["ValueError"], "not the decoder's error"
    assert str(raised).startswith("APPLE_P12_BASE64 is not valid base64"), "says which setting, not what it holds"


@pytest.mark.parametrize(
    "pasted", ['"{}"', "{}\n", "wrapped"], ids=["in double quotes", "with a trailing newline", "wrapped at 76 columns"]
)
def test_a_p12_base64_pasted_with_quotes_or_line_breaks_still_loads_the_same_p12(signing_certs, monkeypatch, pasted):
    """Decoded as it always was: characters that are not base64 are skipped, so a value that worked keeps working."""
    p12_base64 = config.APPLE_P12_BASE64
    assert p12_base64
    expected = base64.b64decode(p12_base64)
    if pasted == "wrapped":
        value = "\n".join(p12_base64[i : i + 76] for i in range(0, len(p12_base64), 76))
    else:
        value = pasted.format(p12_base64)
    loaded: list[bytes] = []
    load = wallet_signer.pkcs12.load_key_and_certificates

    def spy(data, password):
        loaded.append(data)
        return load(data, password)

    monkeypatch.setattr(wallet_signer.pkcs12, "load_key_and_certificates", spy)
    monkeypatch.setenv("APPLE_P12_BASE64", value)
    reload_settings()

    _, certificate, _ = wallet_signer.load_pass_certificate()

    assert loaded == [expected], "the same bytes as the value without them"
    assert certificate.subject.rfc4514_string() == "CN=Pass Type ID: test"
