"""Which member row a Clerk login is allowed to become.

Members can write their own Clerk publicMetadata (the website's onboarding and
profile forms call server actions that store whatever they are sent), so in
production a ``uni_id`` or email found there is not proof of anything. These
tests pin the rule that stops one member's login from taking over another
member's row - points, profile, roster roles and super-admin rights included:

- Production links a login to an existing row only if no login owns that row
  yet, and only through claims Clerk verified (the university email, or the
  email claim).
- Staging and local dev keep the metadata path, because the development
  instance's token has no email claim (docs/STAGING.md).
"""

from datetime import datetime

import pytest
from fastapi_clerk_auth import HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials

from app.config import reload_settings
from app.DB.schema import Members
from app.exceptions import MemberNotFound
from app.helpers import authenticated_guard, resolve_member
from app.main import app

VICTIM_UNI_ID = "431900017"
VICTIM_EMAIL = "victim@example.com"


def add_member(session, *, uni_id=None, email=None, clerk_user_id=None) -> Members:
    member = Members(
        name="Existing Member",
        email=email,
        phone_number="0501112233",
        uni_id=uni_id,
        clerk_user_id=clerk_user_id,
        gender="Male",
        uni_level=4,
        uni_college="Engineering",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        is_authenticated=clerk_user_id is not None,
    )
    session.add(member)
    session.flush()
    return member


def credentials(sub: str, *, email: str | None = None, metadata: dict | None = None):
    decoded: dict = {"sub": sub, "metadata": metadata or {}}
    if email is not None:
        decoded["email"] = email
    return ClerkHTTPAuthorizationCredentials(scheme="Bearer", credentials="fake-token", decoded=decoded)


def signup_metadata(**overrides) -> dict:
    metadata = {
        "fullArabicName": "Attacker",
        "saudiPhone": "0509998877",
        "gender": "Male",
        "uniLevel": 3,
        "uniCollege": "Engineering",
        "personalEmail": "attacker@example.com",
    }
    metadata.update(overrides)
    return metadata


@pytest.fixture
def signed_in_as(client):
    """POST as whoever the given credentials say, through the real route."""

    def use(creds):
        app.dependency_overrides[authenticated_guard] = lambda: creds
        return client

    yield use
    app.dependency_overrides.pop(authenticated_guard, None)


# ---------- resolve_member (every authenticated request) ----------


def test_a_uni_id_written_into_metadata_does_not_take_over_a_claimed_row(db_session):
    victim = add_member(db_session, uni_id=VICTIM_UNI_ID, email=VICTIM_EMAIL, clerk_user_id="victim_sub")
    attacker = credentials("attacker_sub", email="attacker@gmail.com", metadata={"uni_id": VICTIM_UNI_ID})

    with pytest.raises(MemberNotFound):
        resolve_member(db_session, attacker)

    db_session.refresh(victim)
    assert victim.clerk_user_id == "victim_sub"


def test_a_uni_id_written_into_metadata_does_not_claim_an_unclaimed_row(db_session):
    """Production ignores metadata.uni_id entirely: only the verified university email counts."""
    shadow = add_member(db_session, uni_id=VICTIM_UNI_ID)
    attacker = credentials("attacker_sub", email="attacker@gmail.com", metadata={"uni_id": VICTIM_UNI_ID})

    with pytest.raises(MemberNotFound):
        resolve_member(db_session, attacker)

    db_session.refresh(shadow)
    assert shadow.clerk_user_id is None


def test_the_verified_university_email_links_a_row_no_login_owns(db_session):
    shadow = add_member(db_session, uni_id=VICTIM_UNI_ID)
    owner = credentials("owner_sub", email=f"{VICTIM_UNI_ID}@qu.edu.sa")

    assert resolve_member(db_session, owner).id == shadow.id
    db_session.refresh(shadow)
    assert shadow.clerk_user_id == "owner_sub"


def test_the_university_email_does_not_move_a_row_another_login_owns(db_session):
    """Moving an owned row to a new login is an admin's call, not something a token can trigger."""
    owned = add_member(db_session, uni_id=VICTIM_UNI_ID, clerk_user_id="first_login")
    second_login = credentials("second_login", email=f"{VICTIM_UNI_ID}@qu.edu.sa")

    with pytest.raises(MemberNotFound):
        resolve_member(db_session, second_login)

    db_session.refresh(owned)
    assert owned.clerk_user_id == "first_login"


def test_the_email_claim_links_only_rows_without_a_login(db_session):
    owned = add_member(db_session, email=VICTIM_EMAIL, clerk_user_id="victim_sub")
    with pytest.raises(MemberNotFound):
        resolve_member(db_session, credentials("attacker_sub", email=VICTIM_EMAIL))
    db_session.refresh(owned)
    assert owned.clerk_user_id == "victim_sub"

    shadow = add_member(db_session, email="walk.in@example.com")
    assert resolve_member(db_session, credentials("walk_in_sub", email="walk.in@example.com")).id == shadow.id


def test_an_email_written_into_metadata_is_ignored(db_session):
    add_member(db_session, email=VICTIM_EMAIL)
    with pytest.raises(MemberNotFound):
        resolve_member(db_session, credentials("attacker_sub", metadata={"email": VICTIM_EMAIL}))


def test_staging_still_resolves_through_metadata(db_session, monkeypatch):
    """The development instance's token has no email claim, so staging keeps the metadata path."""
    monkeypatch.setenv("ENV", "staging")
    reload_settings()
    row = add_member(db_session, uni_id=VICTIM_UNI_ID, clerk_user_id="prod_login")

    assert resolve_member(db_session, credentials("staging_login", metadata={"uni_id": VICTIM_UNI_ID})).id == row.id


# ---------- POST /members/ (onboarding) ----------


def test_signup_with_someone_elses_uni_id_in_metadata_leaves_their_row_alone(db_session, signed_in_as):
    victim = add_member(db_session, uni_id=VICTIM_UNI_ID, email=VICTIM_EMAIL, clerk_user_id="victim_sub")
    attacker = credentials("attacker_sub", email="attacker@gmail.com", metadata=signup_metadata(uni_id=VICTIM_UNI_ID))

    response = signed_in_as(attacker).post("/members/")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["member"]["id"] != victim.id
    assert body["member"]["uni_id"] is None
    db_session.refresh(victim)
    assert (victim.clerk_user_id, victim.name, victim.email) == ("victim_sub", "Existing Member", VICTIM_EMAIL)


def test_signup_with_a_university_email_whose_row_another_login_owns_is_refused(db_session, signed_in_as):
    owned = add_member(db_session, uni_id=VICTIM_UNI_ID, email=VICTIM_EMAIL, clerk_user_id="first_login")
    second_login = credentials("second_login", email=f"{VICTIM_UNI_ID}@qu.edu.sa", metadata=signup_metadata())

    response = signed_in_as(second_login).post("/members/")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "member_claimed"
    db_session.refresh(owned)
    assert (owned.clerk_user_id, owned.name) == ("first_login", "Existing Member")


def test_signup_with_the_verified_university_email_takes_the_uni_id_from_it(db_session, signed_in_as):
    owner = credentials(
        "owner_sub", email="431900023@qu.edu.sa", metadata=signup_metadata(uni_id="431900031", personalEmail="o@x.com")
    )

    response = signed_in_as(owner).post("/members/")

    assert response.status_code == 201, response.text
    assert response.json()["member"]["uni_id"] == "431900023"
