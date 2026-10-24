"""The Apple Wallet pass update push client: the certificate, the request, and what stays out of logs.

No database and no Apple: the certificate is self-signed, the transport a mock.
The delivery rules (retries, dead tokens, the welcome) are in
tests/routers/test_wallet_passes.py.
"""

import asyncio
import json
import os
import ssl
import stat
import tempfile
from pathlib import Path

import httpx
import pytest

from app.config import reload_settings
from app.services import wallet_push
from app.services.push import clock, redact
from tests.push_support import FakeApns

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
