"""Tests for the Admin Settings Google Forms OAuth reconnect flow and encrypted DB token storage."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.DB.google_oauth import get_oauth_credential
from app.DB.schema import Members, MembersGender
from app.config import reload_settings
from app.services import google_client, google_health, google_oauth
from tests.conftest import STAFF_ACTOR_CLERK_ID

TEST_ENCRYPTION_KEY = "test-google-oauth-encryption-key-with-plenty-of-entropy-1234567890"


@pytest.fixture
def committed_staff_actor(db_session):
    """Persist the test staff actor row so `POST /start` (read-only) and `POST /exchange` see the same member id."""
    member = Members(
        name="Test Staff",
        email="staff-actor@example.com",
        clerk_user_id=STAFF_ACTOR_CLERK_ID,
        gender=MembersGender.MALE,
    )
    db_session.add(member)
    db_session.commit()
    return member


@pytest.fixture(autouse=True)
def _reset_google_oauth_state(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "env-refresh-token")
    monkeypatch.setenv("TEMPLATE_FORM_FILE_ID", "template-form-123")
    monkeypatch.setenv("ADMIN_APP_URL", "https://admin.gdg-q.com")
    monkeypatch.delenv("GOOGLE_FORMS_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setenv("GOOGLE_TOKEN_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    reload_settings()
    google_health._health = google_health._CredentialHealth()
    yield
    reload_settings()
    google_health._health = google_health._CredentialHealth()


def _install_mock_httpx(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(google_oauth.httpx, "Client", _client_factory)


def test_google_auth_endpoints_require_forms_admin_permission(clerk_client: TestClient):
    assert clerk_client.get("/forms/google-auth/status").status_code == 403
    assert clerk_client.post("/forms/google-auth/start").status_code == 403
    assert clerk_client.post("/forms/google-auth/exchange", json={"code": "abc", "state": "xyz"}).status_code == 403


def test_status_reports_env_fallback_when_no_db_token(admin_client: TestClient, monkeypatch):
    monkeypatch.setattr(google_health, "get_google_credentials", lambda: object())

    resp = admin_client.get("/forms/google-auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["source"] == "env"
    assert body["account_email"] is None
    assert body["connected_at"] is None
    assert body["can_reconnect"] is True
    assert body["reconnect_unavailable_reason"] is None


def test_status_reports_reconnect_unavailable_without_encryption_key(admin_client: TestClient, monkeypatch):
    monkeypatch.delenv("GOOGLE_TOKEN_ENCRYPTION_KEY", raising=False)
    reload_settings()
    monkeypatch.setattr(google_health, "get_google_credentials", lambda: object())

    resp = admin_client.get("/forms/google-auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["can_reconnect"] is False
    assert body["reconnect_unavailable_reason"] == "missing_encryption_key"

    start = admin_client.post("/forms/google-auth/start")
    assert start.status_code == 503
    assert start.json()["code"] == "google_oauth_not_configured"


def test_start_builds_google_consent_url_with_signed_state(admin_client: TestClient):
    resp = admin_client.post("/forms/google-auth/start")
    assert resp.status_code == 200
    auth_url = resp.json()["authorization_url"]

    parsed = urlparse(auth_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"

    query = parse_qs(parsed.query)
    assert query["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == ["https://admin.gdg-q.com/settings/google-forms/callback"]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert set(query["scope"][0].split()) == set(google_oauth.FORMS_SCOPES)
    assert len(query["state"][0]) > 20


def test_exchange_encrypts_token_in_db_and_overrides_env_fallback(
    admin_client: TestClient, db_session, committed_staff_actor, monkeypatch
):
    # Start OAuth flow to get a real signed state for this admin.
    start_resp = admin_client.post("/forms/google-auth/start")
    assert start_resp.status_code == 200
    state = parse_qs(urlparse(start_resp.json()["authorization_url"]).query)["state"][0]

    seen_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, str(request.url)))
        if request.url.host == "oauth2.googleapis.com":
            assert b"code=auth-code-xyz" in request.content
            assert b"grant_type=authorization_code" in request.content
            return httpx.Response(
                200, json={"access_token": "fresh-access-token", "refresh_token": "super-secret-db-refresh-token"}
            )
        if request.url.path == "/drive/v3/about":
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            return httpx.Response(200, json={"user": {"emailAddress": "gdg.qu1@gmail.com"}})
        if request.url.path == "/drive/v3/files/template-form-123":
            assert request.headers["Authorization"] == "Bearer fresh-access-token"
            return httpx.Response(200, json={"id": "template-form-123", "ownedByMe": True})
        raise AssertionError(f"Unexpected outbound request: {request.method} {request.url}")

    _install_mock_httpx(monkeypatch, handler)

    # Capture what refresh_token google_client actually sends to Google when probing health.
    captured_refresh_tokens: list[str] = []

    class _FakeCredentials:
        def __init__(self, token, *, refresh_token, token_uri, client_id, client_secret):
            self.token = token
            self.refresh_token = refresh_token
            self.valid = False

        def refresh(self, _request):
            captured_refresh_tokens.append(self.refresh_token)
            self.valid = True

    monkeypatch.setattr(google_client, "Credentials", _FakeCredentials)

    exchange_resp = admin_client.post("/forms/google-auth/exchange", json={"code": "auth-code-xyz", "state": state})
    assert exchange_resp.status_code == 200
    body = exchange_resp.json()
    assert body["valid"] is True
    assert body["source"] == "database"
    assert body["account_email"] == "gdg.qu1@gmail.com"
    assert body["connected_at"] is not None
    assert body["connected_by_name"] is not None

    # Verify the DB stores ciphertext, never the plaintext refresh token.
    row = get_oauth_credential(db_session, google_oauth.FORMS_SERVICE)
    assert row is not None
    assert row.account_email == "gdg.qu1@gmail.com"
    assert "super-secret-db-refresh-token" not in row.encrypted_refresh_token
    assert google_oauth.decrypt_refresh_token(row.encrypted_refresh_token) == "super-secret-db-refresh-token"

    # And verify get_google_credentials() now uses the DB token instead of the env token.
    assert captured_refresh_tokens == ["super-secret-db-refresh-token"]


def test_exchange_rejects_wrong_google_account(
    admin_client: TestClient, db_session, committed_staff_actor, monkeypatch
):
    start_resp = admin_client.post("/forms/google-auth/start")
    state = parse_qs(urlparse(start_resp.json()["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok", "refresh_token": "personal-refresh"})
        if request.url.path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"emailAddress": "personal@gmail.com"}})
        if request.url.path == "/drive/v3/files/template-form-123":
            return httpx.Response(200, json={"id": "template-form-123", "ownedByMe": False})
        raise AssertionError(f"Unexpected request: {request.url}")

    _install_mock_httpx(monkeypatch, handler)

    resp = admin_client.post("/forms/google-auth/exchange", json={"code": "code-1", "state": state})
    assert resp.status_code == 422
    assert resp.json()["code"] == "wrong_google_account"
    assert get_oauth_credential(db_session, google_oauth.FORMS_SERVICE) is None


def test_exchange_rejects_missing_refresh_token(admin_client: TestClient, committed_staff_actor, monkeypatch):
    start_resp = admin_client.post("/forms/google-auth/start")
    state = parse_qs(urlparse(start_resp.json()["authorization_url"]).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "tok"})
        raise AssertionError(f"Unexpected request: {request.url}")

    _install_mock_httpx(monkeypatch, handler)

    resp = admin_client.post("/forms/google-auth/exchange", json={"code": "code-1", "state": state})
    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_refresh_token"


def test_exchange_rejects_tampered_expired_or_wrong_actor_state(admin_client: TestClient):
    # Tampered state
    resp = admin_client.post("/forms/google-auth/exchange", json={"code": "code-1", "state": "not-a-jwt"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_oauth_state"

    # Expired state
    expired_state = jwt.encode(
        {
            "purpose": google_oauth.STATE_PURPOSE,
            "sub": "999999",
            "redirect_uri": "https://admin.gdg-q.com/settings/google-forms/callback",
            "iat": int((datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp()),
            "exp": int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()),
        },
        google_oauth._state_signing_key(TEST_ENCRYPTION_KEY),
        algorithm="HS256",
    )
    resp = admin_client.post("/forms/google-auth/exchange", json={"code": "code-1", "state": expired_state})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_oauth_state"

    # Valid signature & unexpired, but bound to a different member id
    wrong_actor_state = jwt.encode(
        {
            "purpose": google_oauth.STATE_PURPOSE,
            "sub": "999999999",
            "redirect_uri": "https://admin.gdg-q.com/settings/google-forms/callback",
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        },
        google_oauth._state_signing_key(TEST_ENCRYPTION_KEY),
        algorithm="HS256",
    )
    resp = admin_client.post("/forms/google-auth/exchange", json={"code": "code-1", "state": wrong_actor_state})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_oauth_state"
