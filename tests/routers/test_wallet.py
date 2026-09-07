"""The wallet routes, and the identity resolution they used to own.

`/wallet` had no route-level tests. It also had its own copy of "who is
calling?" - a three-step fallback wrapped in three `except Exception` blocks,
plus a card-data builder that read the Authorization header and called the
guard by hand. Both are gone; the routes take `CurrentMember` / `MemberOrGuest`
like everything else, which is what makes them reachable from here at all.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.DB.schema import Members, MembersGender, Role, RoleType
from app.exceptions import MemberNotFound
from app.helpers import resolve_member
from fastapi_clerk_auth import HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials

from tests.conftest import FAKE_CLERK_CREDENTIALS
from tests.utils import assert_2xx, assert_forbidden, assert_not_found

CLERK_SUB = "clerk_test_member_sub"
CLERK_UNI_ID = "123456789"
CLERK_EMAIL = "test@example.com"


def make_member(session, *, clerk_user_id=None, uni_id=None, email="wallet@example.com", admin=False) -> Members:
    member = Members(
        name="Wallet Member",
        email=email,
        phone_number="0500000000",
        uni_id=uni_id,
        gender=MembersGender.MALE,
        uni_level=4,
        uni_college="Engineering",
        clerk_user_id=clerk_user_id,
    )
    session.add(member)
    session.flush()
    if admin:
        session.add(Role(member_id=member.id, role=RoleType.ADMIN))
    session.commit()
    return member


# ====================== resolving the caller ======================
#
# One chain now, in `app/helpers.py`: clerk_user_id, then uni_id, then email.
# The wallet router's copy had the same three steps but never wrote the
# clerk_user_id back, so every request it served paid for the fallback again.


def test_resolves_by_clerk_user_id(db_session):
    member = make_member(db_session, clerk_user_id=CLERK_SUB)
    assert resolve_member(db_session, FAKE_CLERK_CREDENTIALS).id == member.id


def test_falls_back_to_uni_id_and_remembers_the_clerk_id(db_session):
    member = make_member(db_session, uni_id=CLERK_UNI_ID)
    assert member.clerk_user_id is None

    resolved = resolve_member(db_session, FAKE_CLERK_CREDENTIALS)

    assert resolved.id == member.id
    # Self-healed, so the next request takes the first branch.
    assert resolved.clerk_user_id == CLERK_SUB


def credentials_with_email(email: str):
    """Clerk credentials carrying a top-level `email` claim.

    `FAKE_CLERK_CREDENTIALS` deliberately does not have one - see
    `test_the_email_fallback_ignores_app_written_metadata` below.
    """
    decoded = dict(FAKE_CLERK_CREDENTIALS.decoded or {})
    decoded["email"] = email
    return ClerkHTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token", decoded=decoded)


def test_falls_back_to_email_and_remembers_the_clerk_id(db_session):
    """A row an admin created before the member ever signed in carries neither
    a clerk_user_id nor a uni_id, so email is the only thing left to match."""
    member = make_member(db_session, email=CLERK_EMAIL, uni_id=None)

    resolved = resolve_member(db_session, credentials_with_email(CLERK_EMAIL))

    assert resolved.id == member.id
    assert resolved.clerk_user_id == CLERK_SUB


def test_email_match_ignores_case(db_session):
    member = make_member(db_session, email="TEST@Example.COM", uni_id=None)
    assert resolve_member(db_session, credentials_with_email(CLERK_EMAIL)).id == member.id


def test_the_email_fallback_ignores_app_written_metadata(db_session):
    """Pins a gap rather than a feature.

    The chain reads `email` / `primary_email_address` - claims Clerk issues and
    verifies - and `metadata.email`. It does *not* read
    `metadata.personalEmail`, which is what this app's auth frontend actually
    writes, so for a uni_id/password signup the email step never fires; those
    members are found by uni_id one step earlier.

    Adding `personalEmail` would make the step live, and would also mean a
    self-typed signup field could match somebody else's already-claimed member
    row. That is a decision to take deliberately, not a side effect of moving
    this code, so the behaviour is preserved exactly as the wallet router had
    it. See `credentials_to_member_model` for the metadata contract.
    """
    make_member(db_session, email=CLERK_EMAIL, uni_id=None)

    with pytest.raises(MemberNotFound):
        resolve_member(db_session, FAKE_CLERK_CREDENTIALS)


def test_raises_when_nothing_matches(db_session):
    make_member(db_session, uni_id="999999999", email="somebody-else@example.com")
    with pytest.raises(MemberNotFound):
        resolve_member(db_session, FAKE_CLERK_CREDENTIALS)


def test_a_lookup_failure_is_not_reported_as_an_unknown_caller(db_session, monkeypatch):
    """The wallet router's version caught every exception at each step, so a
    database that was down produced the same answer as a caller who is not a
    member. `MemberOrGuest` narrows that to `MemberNotFound`."""
    from app.DB import members as member_queries
    from app.helpers import get_member_or_none

    def explode(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("server has gone away"))

    monkeypatch.setattr(member_queries, "get_member_by_clerk_user_id_or_none", explode)

    with pytest.raises(OperationalError):
        get_member_or_none(db_session, FAKE_CLERK_CREDENTIALS)


def test_no_credentials_resolves_to_a_guest(db_session):
    from app.helpers import get_member_or_none

    assert get_member_or_none(db_session, None) is None


def test_signed_in_but_unregistered_resolves_to_a_guest(db_session):
    from app.helpers import get_member_or_none

    assert get_member_or_none(db_session, FAKE_CLERK_CREDENTIALS) is None


# ====================== GET /wallet/me ======================


def test_wallet_me_returns_the_members_card(clerk_client: TestClient, db_session):
    member = make_member(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.get("/wallet/me")

    assert_2xx(response)
    body = response.json()
    assert body["member_id"] == member.id
    assert body["official_name"] == "Wallet Member"
    assert body["is_admin"] is False
    assert body["profile"]["uuid"]


def test_wallet_me_returns_a_skeleton_for_a_caller_with_no_member_row(clerk_client: TestClient):
    """Signed in with Clerk, not registered yet. A 200 with empty fields, not a
    404 - the member app renders this as an unregistered card."""
    response = clerk_client.get("/wallet/me")

    assert_2xx(response)
    body = response.json()
    assert body["member_id"] is None
    assert body["uni_id"] == CLERK_UNI_ID
    assert body["is_admin"] is False
    assert body["profile"]["theme_id"] == "gdg-blue"


def test_wallet_me_still_needs_a_token(client: TestClient):
    assert client.get("/wallet/me").status_code == 403


def test_wallet_me_reports_admin_roles(clerk_client: TestClient, db_session):
    make_member(db_session, clerk_user_id=CLERK_SUB, admin=True)

    body = clerk_client.get("/wallet/me").json()

    assert body["is_admin"] is True
    assert body["roles"] == ["admin"]


# ====================== PUT /wallet/me ======================


def test_update_wallet_me_writes_the_profile(clerk_client: TestClient, db_session):
    make_member(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.put("/wallet/me", json={"custom_name": "اسم مختار", "bio": "مطور"})

    assert_2xx(response)
    body = response.json()
    assert body["success"] is True
    assert body["name"] == "اسم مختار"
    assert body["profile"]["bio"] == "مطور"


def test_update_wallet_me_404s_for_a_caller_with_no_member_row(clerk_client: TestClient):
    """The one wallet endpoint where being unregistered is an error - there is
    nothing to write to. The Arabic message the member app shows is preserved."""
    response = clerk_client.put("/wallet/me", json={"bio": "x"})

    assert_not_found(response)
    assert "عضو غير مسجل" in response.json()["detail"]


def test_update_wallet_me_refuses_the_gold_theme_for_a_non_admin(clerk_client: TestClient, db_session):
    make_member(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.put("/wallet/me", json={"theme_id": "gdg-gold-admin"})

    assert_forbidden(response)


def test_update_wallet_me_allows_the_gold_theme_for_an_admin(clerk_client: TestClient, db_session):
    make_member(db_session, clerk_user_id=CLERK_SUB, admin=True)

    response = clerk_client.put("/wallet/me", json={"theme_id": "gdg-gold-admin"})

    assert_2xx(response)
    assert response.json()["profile"]["theme_id"] == "gdg-gold-admin"


# ====================== the pass endpoints ======================
#
# Signing is covered by tests/test_google_wallet_signer.py. What matters here is
# which card data reaches the signer, which is what `_pass_card_data` decides.


@pytest.fixture
def signed_card(monkeypatch):
    """Capture the card data the route hands to the signer."""
    captured: dict = {}

    def fake_google(card_data):
        captured.update(card_data)
        return "https://pay.google.com/gp/v/save/fake"

    from app.routers import wallet

    monkeypatch.setattr(wallet, "generate_google_wallet_pass_url", fake_google)
    return captured


def test_google_pass_for_a_member_takes_identity_from_the_database(clerk_client: TestClient, db_session, signed_card):
    """The uni_id and the admin flag come from the member row, never the body.

    The displayed name does not: the order is the profile's custom_name, then
    the payload, then the official name. It is the member's own card, and they
    can set custom_name through PUT /wallet/me anyway.
    """
    member = make_member(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.post("/wallet/google-pass", json={"fullName": "اسم على البطاقة", "uniId": "000"})

    assert_2xx(response)
    assert signed_card["uniId"] == member.uni_id
    assert signed_card["fullName"] == "اسم على البطاقة"
    assert signed_card["isAdmin"] is False


def test_google_pass_for_a_guest_uses_the_payload(client: TestClient, signed_card):
    """No token at all. Still a 200 and still a card - that is the point of the
    endpoint - built from whatever the caller sent."""
    response = client.post("/wallet/google-pass", json={"fullName": "زائر", "uniId": "451000000"})

    assert_2xx(response)
    assert signed_card["fullName"] == "زائر"
    assert signed_card["isAdmin"] is False
    assert signed_card["uuid"]


def test_a_guest_cannot_ask_for_the_admin_card(client: TestClient, signed_card):
    response = client.post("/wallet/google-pass", json={"themeId": "gdg-gold-admin"})

    assert_2xx(response)
    assert signed_card["themeId"] == "gdg-blue"


def test_a_non_admin_member_cannot_ask_for_the_admin_card(clerk_client: TestClient, db_session, signed_card):
    make_member(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.post("/wallet/google-pass", json={"themeId": "gdg-gold-admin"})

    assert_2xx(response)
    assert signed_card["themeId"] == "gdg-blue"


def test_an_admin_member_gets_the_admin_card_from_their_profile(clerk_client: TestClient, db_session, signed_card):
    """A profile is created with a theme already set, so the payload never gets
    a say for a registered member - the profile is authoritative. An admin gets
    the gold card by choosing it through PUT /wallet/me."""
    make_member(db_session, clerk_user_id=CLERK_SUB, admin=True)
    assert_2xx(clerk_client.put("/wallet/me", json={"theme_id": "gdg-gold-admin"}))

    response = clerk_client.post("/wallet/google-pass", json={})

    assert_2xx(response)
    assert signed_card["themeId"] == "gdg-gold-admin"
    assert signed_card["isAdmin"] is True


def test_a_members_profile_theme_wins_over_the_payload(clerk_client: TestClient, db_session, signed_card):
    make_member(db_session, clerk_user_id=CLERK_SUB, admin=True)
    assert_2xx(clerk_client.put("/wallet/me", json={"theme_id": "gdg-gold-admin"}))

    clerk_client.post("/wallet/google-pass", json={"themeId": "gdg-blue"})

    assert signed_card["themeId"] == "gdg-gold-admin"


def test_a_members_pass_reuses_their_profile_uuid(clerk_client: TestClient, db_session, signed_card):
    """A guest card gets a fresh uuid each time; a member's points at their
    profile, which is what the public /wallet/{uuid} page resolves."""
    make_member(db_session, clerk_user_id=CLERK_SUB)

    clerk_client.post("/wallet/google-pass", json={})
    first = signed_card["uuid"]
    clerk_client.post("/wallet/google-pass", json={})

    assert signed_card["uuid"] == first
    assert clerk_client.get(f"/wallet/{first}").status_code == 200
