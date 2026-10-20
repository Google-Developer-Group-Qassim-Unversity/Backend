"""DELETE /members/me revokes the app's Sign in with Apple tokens (app/services/apple_signin.py).

Clerk and Apple are both faked at the HTTP transport (``outbound``), so the
form posts to appleid.apple.com are asserted as they would leave the process.
The Sign in with Apple key is generated for each run; no test reaches Apple.
"""

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi_clerk_auth import HTTPAuthorizationCredentials

from app.config import config
from app.DB.schema import Members, MembersGender, SuperAdmins
from app.main import app
from app.services.clerk_api import parse_user
from tests.utils import assert_conflict, assert_unprocessable

APPLE_USER = "user_apple_noura"
APPLE_SUB = "001234.0a1b2c3d4e5f60718293a4b5c6d7e8f9.1234"
RELAY_EMAIL = "x7k2m9q4pz@privaterelay.appleid.com"
KEY_ID = "TESTKEY123"
TEAM_ID = "TESTTEAM12"
CODE = "c0de.0.abcd.EFGH-ijkl"
REFRESH_TOKEN = "r.refresh.token.value"
ACCESS_TOKEN = "a.access.token.value"


# ---------- fakes ----------


@pytest.fixture
def apple_key(monkeypatch) -> ec.EllipticCurvePrivateKey:
    """A fresh P-256 key in the settings, as the Sign in with Apple .p8 would be."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_member_accounts")
    monkeypatch.setenv("APPLE_TEAM_ID", TEAM_ID)
    monkeypatch.setenv("APPLE_SIGNIN_KEY_ID", KEY_ID)
    # One-line secret stores hold the key with literal \n; that is how it is read.
    monkeypatch.setenv("APPLE_SIGNIN_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.delenv("APPLE_SIGNIN_CLIENT_ID", raising=False)
    return key


@pytest.fixture
def signed_in(client):
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="test-token", decoded={"sub": APPLE_USER, "metadata": {}}
    )
    app.dependency_overrides[config.CLERK_GUARD] = lambda: credentials
    app.dependency_overrides[config.CLERK_GUARD_optional] = lambda: credentials
    yield client
    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    app.dependency_overrides.pop(config.CLERK_GUARD_optional, None)


def clerk_apple_user(apple_sub: str | None = APPLE_SUB) -> dict:
    """A Clerk Backend API user who signed up with Apple (Hide My Email)."""
    return {
        "id": APPLE_USER,
        "object": "user",
        "primary_email_address_id": "idn_0",
        "email_addresses": [
            {
                "id": "idn_0",
                "object": "email_address",
                "email_address": RELAY_EMAIL,
                "verification": {"status": "verified", "strategy": "from_oauth_apple"},
                "linked_to": [{"type": "oauth_apple", "id": "eac_1"}],
            }
        ],
        "external_accounts": (
            [
                {
                    "object": "external_account",
                    "id": "eac_1",
                    "provider": "oauth_apple",
                    "provider_user_id": apple_sub,
                    "email_address": RELAY_EMAIL,
                    "verification": {"status": "verified", "strategy": "oauth_apple"},
                }
            ]
            if apple_sub
            else []
        ),
        "public_metadata": {},
    }


def apple_id_token(sub: str = APPLE_SUB, aud: str = "com.gdgq.app", iss: str = "https://appleid.apple.com") -> str:
    # Apple signs it with its own key; the backend reads it straight off Apple's TLS answer.
    return jwt.encode({"iss": iss, "aud": aud, "sub": sub, "iat": 1, "exp": 2}, "apple-side-key", algorithm="HS256")


def stub_apple(outbound, *, token_status=200, tokens=None, revoke_status=200, token_exc=None) -> None:
    if token_exc is not None:
        outbound.stub("/auth/token", exc=token_exc)
    else:
        body = (
            tokens
            if tokens is not None
            else {
                "access_token": ACCESS_TOKEN,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": REFRESH_TOKEN,
                "id_token": apple_id_token(),
            }
        )
        outbound.stub("/auth/token", status_code=token_status, json=body)
    outbound.stub("/auth/revoke", status_code=revoke_status, content=b"")


def a_member(db_session) -> Members:
    member = Members(
        name="نورة عبدالله السالم",
        email=RELAY_EMAIL,
        phone_number="0559876543",
        gender=MembersGender.FEMALE,
        clerk_user_id=APPLE_USER,
        is_authenticated=True,
    )
    db_session.add(member)
    db_session.commit()
    return member


def delete(client, body=None):
    return client.request("DELETE", "/members/me", json=body)


def form(request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.text))


def apple_paths(outbound) -> list[str]:
    return [call.path for call in outbound.calls if call.url.host == "appleid.apple.com"]


# ---------- revoking ----------


def test_deleting_revokes_apple_tokens_after_the_account_is_gone(signed_in, outbound, db_session, apple_key):
    member_id = a_member(db_session).id
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound)

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "member_anonymized": True, "apple_token_revoked": True}
    db_session.expire_all()
    assert db_session.get(Members, member_id).deleted_at is not None
    # Clerk read, Clerk delete, then Apple: exchange the code, revoke the refresh token.
    hosts = {"api.clerk.com", "appleid.apple.com"}
    assert [(call.method, call.url.host, call.path) for call in outbound.calls if call.url.host in hosts] == [
        ("GET", "api.clerk.com", f"/v1/users/{APPLE_USER}"),
        ("DELETE", "api.clerk.com", f"/v1/users/{APPLE_USER}"),
        ("POST", "appleid.apple.com", "/auth/token"),
        ("POST", "appleid.apple.com", "/auth/revoke"),
    ]

    exchange, revoke = outbound.one("/auth/token"), outbound.one("/auth/revoke")
    assert exchange.headers["content-type"] == "application/x-www-form-urlencoded"
    exchange_form = form(exchange)
    assert {k: v for k, v in exchange_form.items() if k != "client_secret"} == {
        "client_id": "com.gdgq.app",
        "code": CODE,
        "grant_type": "authorization_code",
    }
    revoke_form = form(revoke)
    assert {k: v for k, v in revoke_form.items() if k != "client_secret"} == {
        "client_id": "com.gdgq.app",
        "token": REFRESH_TOKEN,
        "token_type_hint": "refresh_token",
    }

    # The client secret Apple authenticates the app with, on both calls.
    for secret in (exchange_form["client_secret"], revoke_form["client_secret"]):
        assert jwt.get_unverified_header(secret) == {"alg": "ES256", "kid": KEY_ID, "typ": "JWT"}
        claims = jwt.decode(secret, apple_key.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com")
        assert (claims["iss"], claims["sub"]) == (TEAM_ID, "com.gdgq.app")
        assert 0 < claims["exp"] - claims["iat"] <= 15777000


def test_without_a_refresh_token_the_access_token_is_revoked(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, tokens={"access_token": ACCESS_TOKEN, "id_token": apple_id_token()})

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json()["apple_token_revoked"] is True
    revoke_form = form(outbound.one("/auth/revoke"))
    assert (revoke_form["token"], revoke_form["token_type_hint"]) == (ACCESS_TOKEN, "access_token")


def test_a_sign_in_without_a_member_record_is_revoked_too(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound)

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json() == {"deleted": True, "member_anonymized": False, "apple_token_revoked": True}


# ---------- never blocks the deletion ----------


def test_a_code_apple_refuses_still_deletes(signed_in, outbound, db_session, apple_key):
    member_id = a_member(db_session).id
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, token_status=400, tokens={"error": "invalid_grant"})

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "member_anonymized": True, "apple_token_revoked": False}
    db_session.expire_all()
    assert db_session.get(Members, member_id).deleted_at is not None
    assert apple_paths(outbound) == ["/auth/token"], "nothing to revoke without tokens"


def test_a_code_for_another_apple_account_revokes_nothing(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, tokens={"refresh_token": REFRESH_TOKEN, "id_token": apple_id_token(sub="009999.other.0001")})

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json()["apple_token_revoked"] is False
    assert apple_paths(outbound) == ["/auth/token"]


@pytest.mark.parametrize(
    "id_token",
    [apple_id_token(aud="com.example.other"), apple_id_token(iss="https://evil.example"), "not-a-jwt", None],
    ids=["another-app", "another-issuer", "garbage", "missing"],
)
def test_an_id_token_not_issued_to_this_app_revokes_nothing(signed_in, outbound, apple_key, id_token):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, tokens={"refresh_token": REFRESH_TOKEN, "id_token": id_token})

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json()["apple_token_revoked"] is False
    assert apple_paths(outbound) == ["/auth/token"]


def test_a_sign_in_without_an_apple_account_never_calls_apple(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user(apple_sub=None))

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json() == {"deleted": True, "member_anonymized": False, "apple_token_revoked": False}
    assert apple_paths(outbound) == []


def test_apple_unreachable_still_deletes(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, token_exc=httpx.ConnectTimeout("timed out"))

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.status_code == 200, response.text
    assert response.json()["apple_token_revoked"] is False
    assert [call.method for call in outbound.to(f"/v1/users/{APPLE_USER}")] == ["GET", "DELETE"]


def test_a_failed_revoke_is_reported(signed_in, outbound, apple_key):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound, revoke_status=503)

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json()["apple_token_revoked"] is False


def test_a_key_that_does_not_load_still_deletes(signed_in, outbound, apple_key, monkeypatch):
    monkeypatch.setenv(
        "APPLE_SIGNIN_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\\nbm90IGEga2V5\\n-----END PRIVATE KEY-----"
    )
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json() == {"deleted": True, "member_anonymized": False, "apple_token_revoked": False}
    assert apple_paths(outbound) == []


def test_not_configured_skips_revocation(signed_in, outbound, apple_key, monkeypatch):
    monkeypatch.delenv("APPLE_SIGNIN_KEY_ID")
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.json() == {"deleted": True, "member_anonymized": False, "apple_token_revoked": False}
    assert apple_paths(outbound) == []


# ---------- when Apple is not called at all ----------


@pytest.mark.parametrize("body", [None, {}, {"apple_authorization_code": None}])
def test_no_code_deletes_as_before(signed_in, outbound, apple_key, body):
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())

    response = delete(signed_in, body)

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "member_anonymized": False, "apple_token_revoked": None}
    assert apple_paths(outbound) == []


def test_a_refused_deletion_revokes_nothing(signed_in, outbound, db_session, apple_key):
    member = a_member(db_session)
    db_session.add(SuperAdmins(member_id=member.id))
    db_session.commit()
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    stub_apple(outbound)

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert_conflict(response)
    assert response.json()["code"] == "member_is_staff"
    assert apple_paths(outbound) == []


def test_clerk_failing_to_delete_revokes_nothing(signed_in, outbound, apple_key):
    """The app retries a 502 with a new code; this one is never spent."""
    outbound.stub(f"/v1/users/{APPLE_USER}", method="GET", json=clerk_apple_user())
    outbound.stub(f"/v1/users/{APPLE_USER}", method="DELETE", status_code=500, json={})
    stub_apple(outbound)

    response = delete(signed_in, {"apple_authorization_code": CODE})

    assert response.status_code == 502
    assert response.json()["code"] == "clerk_unavailable"
    assert apple_paths(outbound) == []


@pytest.mark.parametrize(
    "body",
    [{"apple_authorization_code": ""}, {"apple_authorization_code": "x" * 1001}, {"uuid": "anything"}],
    ids=["empty", "too-long", "unknown-key"],
)
def test_a_bad_body_is_a_422_before_anything_happens(signed_in, outbound, apple_key, body):
    response = delete(signed_in, body)

    assert_unprocessable(response)
    assert outbound.calls == []


# ---------- reading the Apple account off Clerk's user ----------


def test_the_clerk_user_carries_the_apple_user_id():
    assert parse_user(clerk_apple_user()).apple_user_id == APPLE_SUB
    assert parse_user(clerk_apple_user(apple_sub=None)).apple_user_id is None
    google = {
        **clerk_apple_user(),
        "external_accounts": [{"provider": "oauth_google", "provider_user_id": "10987654321"}],
    }
    assert parse_user(google).apple_user_id is None
