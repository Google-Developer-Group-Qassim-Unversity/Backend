"""The member app's account routes: POST /members/onboarding and DELETE /members/me.

Clerk is faked at the HTTP transport (``outbound``): each test stubs the Clerk
user it signs in as, so what the routes read from Clerk, and what they write
back, is exactly what crossed the wire.
"""

from datetime import datetime

import pytest
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from app.config import config, reload_settings
from app.DB import logs as log_queries
from app.DB import members as member_queries
from app.DB.schema import (
    Actions,
    ActionsActionType,
    EmailLogs,
    EmailLogsEmailType,
    Events,
    EventsLocationType,
    EventsStatus,
    Forms,
    FormType,
    Logs,
    MemberProfiles,
    Members,
    MembersGender,
    MembersLogs,
    Submissions,
    SubmissionsSubmissionType,
    SuperAdmins,
)
from app.exceptions import MemberNotFound
from app.main import app
from tests.utils import assert_2xx, assert_conflict, assert_unprocessable, semester_id_on

QU_USER = "user_qu_444111222"
GOOGLE_USER = "user_google_fatima"


# ---------- signing in and faking Clerk ----------


@pytest.fixture(autouse=True)
def clerk_secret(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_member_accounts")
    reload_settings()


@pytest.fixture
def sign_in(client):
    """Sign in as a Clerk user id. Only the JWT check is replaced; the guards run as they do in production."""

    def _sign_in(user_id: str):
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="test-token", decoded={"sub": user_id, "metadata": {}}
        )
        app.dependency_overrides[config.CLERK_GUARD] = lambda: credentials
        app.dependency_overrides[config.CLERK_GUARD_optional] = lambda: credentials
        return client

    yield _sign_in
    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    app.dependency_overrides.pop(config.CLERK_GUARD_optional, None)


def clerk_user(user_id: str, *addresses: tuple[str, str], primary: int = 0) -> dict:
    """A Clerk Backend API user object. ``addresses`` are (email, verification strategy)."""
    return {
        "id": user_id,
        "object": "user",
        "primary_email_address_id": f"idn_{primary}" if addresses else None,
        "email_addresses": [
            {
                "id": f"idn_{index}",
                "object": "email_address",
                "email_address": email,
                "verification": {"status": "verified", "strategy": strategy, "attempts": None, "expire_at": None},
                "linked_to": [],
            }
            for index, (email, strategy) in enumerate(addresses)
        ],
        "public_metadata": {},
    }


def stub_qu_account(outbound, user_id: str = QU_USER, uni_id: str = "444111222") -> None:
    """A university-ID account: signed up as <uni_id>@qu.edu.sa, proved with a code sent there."""
    outbound.stub(f"/v1/users/{user_id}", json=clerk_user(user_id, (f"{uni_id}@qu.edu.sa", "email_code")))


def stub_google_account(outbound, user_id: str = GOOGLE_USER, email: str = "fatima.m@gmail.com") -> None:
    outbound.stub(f"/v1/users/{user_id}", json=clerk_user(user_id, (email, "from_oauth_google")))


def answers(status: str = "qu_student", **fields) -> dict:
    """An onboarding body. Pass a field as None to leave it out."""
    body: dict = {
        "status": status,
        "name": "عبدالله محمد الأحمد",
        "gender": "Male",
        "phone_number": "0551234567",
        "personal_email": "abdullah.m@gmail.com",
    }
    if status == "qu_student":
        body |= {"uni_level": 4, "uni_college": "كلية الحاسب"}
    body |= fields
    return {key: value for key, value in body.items() if value is not None}


def metadata_written(outbound, user_id: str) -> dict:
    return outbound.one(f"/v1/users/{user_id}/metadata").json["public_metadata"]


def member_row(db_session, clerk_user_id: str) -> Members | None:
    db_session.expire_all()
    return db_session.scalar(select(Members).where(Members.clerk_user_id == clerk_user_id))


def card(db_session, member_id: int) -> MemberProfiles | None:
    db_session.expire_all()
    return db_session.scalar(select(MemberProfiles).where(MemberProfiles.member_id == member_id))


# ---------- who may call ----------


@pytest.mark.parametrize("method, path", [("POST", "/members/onboarding"), ("DELETE", "/members/me")])
def test_account_routes_answer_401_without_a_session(client, outbound, method, path):
    for headers in ({}, {"Authorization": "Bearer not-a-jwt"}):
        response = client.request(method, path, json=answers() if method == "POST" else None, headers=headers)

        assert response.status_code == 401, response.text
        assert response.json()["code"] == "not_authenticated"
        assert response.headers["www-authenticate"] == "Bearer"
    assert outbound.calls == [], "nothing reaches Clerk for a caller who is not signed in"


def test_onboarding_body_cannot_carry_identity(sign_in, outbound):
    stub_qu_account(outbound)
    for field, value in (("uni_id", "999999999"), ("clerk_user_id", "user_other"), ("email", "x@example.com")):
        response = sign_in(QU_USER).post("/members/onboarding", json=answers(**{field: value}))

        assert_unprocessable(response)
    assert outbound.calls == []


# ---------- the four statuses ----------


def test_qu_student_creates_a_member_and_marks_onboarding_complete(sign_in, outbound, db_session):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["already_exists"] is False
    member = body["member"]
    assert member["uni_id"] == "444111222", "the university ID comes from the address the account proved"
    assert member["clerk_user_id"] == QU_USER
    assert member["name"] == "عبدالله محمد الأحمد"
    assert member["public_name"] == "عبدالله الأحمد"
    assert member["email"] == "abdullah.m@gmail.com"
    assert member["phone_number"] == "0551234567"
    assert member["gender"] == "Male"
    assert (member["uni_level"], member["uni_college"]) == (4, "كلية الحاسب")
    assert member["is_authenticated"] is True

    assert metadata_written(outbound, QU_USER) == {
        "uni_id": "444111222",
        "fullArabicName": "عبدالله محمد الأحمد",
        "saudiPhone": "0551234567",
        "gender": "Male",
        "memberStatus": "qu_student",
        "uniLevel": 4,
        "uniCollege": "كلية الحاسب",
        "highSchoolName": None,
        "universityName": None,
        "personalEmail": "abdullah.m@gmail.com",
        "onboardingComplete": True,
    }
    request = outbound.one(f"/v1/users/{QU_USER}/metadata")
    assert request.method == "PATCH"
    assert request.headers["authorization"] == "Bearer sk_test_member_accounts"

    profile = card(db_session, member["id"])
    assert profile is not None
    assert (profile.user_status, profile.education_level, profile.institution) == (
        "student",
        "university",
        "كلية الحاسب",
    )
    assert profile.theme_id == "gdg-blue"


# ---------- the personal email ----------


# ---------- repeating, linking and conflicts ----------


def test_links_the_unclaimed_record_with_the_proven_university_id(sign_in, outbound, db_session):
    """An admin added this student (from an attendance sheet) before they ever signed in."""
    existing = Members(
        name="عبدالله", email="old@example.com", uni_id="444111222", gender=MembersGender.MALE, is_authenticated=0
    )
    db_session.add(existing)
    db_session.commit()
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 200, response.text
    assert response.json()["already_exists"] is True
    assert response.json()["member"]["id"] == existing.id
    row = member_row(db_session, QU_USER)
    assert row is not None and row.id == existing.id
    assert row.email == "abdullah.m@gmail.com"


def test_a_record_another_sign_in_uses_is_a_conflict(sign_in, outbound, db_session):
    owned = Members(
        name="Owner",
        email="owner@example.com",
        uni_id="444111222",
        clerk_user_id="user_owner",
        gender=MembersGender.MALE,
    )
    db_session.add(owned)
    db_session.commit()
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert_conflict(response)
    assert response.json()["code"] == "member_claimed"
    db_session.refresh(owned)
    assert (owned.clerk_user_id, owned.name, owned.email) == ("user_owner", "Owner", "owner@example.com")


# ---------- when Clerk fails ----------


def test_clerk_down_before_anything_is_written(sign_in, outbound, db_session):
    outbound.stub(f"/v1/users/{QU_USER}", status_code=500, json={"errors": []})

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "clerk_unavailable"
    assert member_row(db_session, QU_USER) is None


# ---------- deleting an account ----------


def _onboarded_member_with_history(sign_in, outbound, db_session) -> tuple[Members, int]:
    """A member with points this semester, a registration with form answers, a card and an email sent to
    them. Returns the member and the event."""
    stub_qu_account(outbound)
    response = sign_in(QU_USER).post("/members/onboarding", json=answers())
    assert response.status_code == 201, response.text
    member = member_row(db_session, QU_USER)
    assert member is not None

    event = Events(
        name="ورشة الذكاء الاصطناعي",
        location_type=EventsLocationType.ON_SITE,
        location="Hall",
        start_datetime=datetime(2026, 7, 1, 10),
        end_datetime=datetime(2026, 7, 1, 12),
        status=EventsStatus.CLOSED,
        semester_id=semester_id_on(db_session, "2026-07-01"),
    )
    db_session.add(event)
    db_session.flush()
    action_id = db_session.scalar(select(Actions.id).where(Actions.action_type == ActionsActionType.MEMBER))
    log = Logs(action_id=action_id, event_id=event.id)
    db_session.add(log)
    db_session.flush()
    db_session.add(MembersLogs(member_id=member.id, log_id=log.id, date=datetime(2026, 7, 1, 11)))
    form = Forms(event_id=event.id, form_type=FormType.GOOGLE)
    db_session.add(form)
    db_session.flush()
    db_session.add(
        Submissions(
            form_id=form.id,
            member_id=member.id,
            submission_type=SubmissionsSubmissionType.GOOGLE,
            google_submission_value={"phone": "0551234567"},
        )
    )
    db_session.add(
        EmailLogs(
            member_id=member.id,
            event_id=event.id,
            from_address="gdg.qu1@gmail.com",
            sent_by=member.id,
            recipient_count=1,
            email_type=EmailLogsEmailType.EVENT_CERTIFICATE,
            data={"member": {"name": member.name, "email": member.email}},
        )
    )
    db_session.commit()
    return member, event.id


def _leaderboard_ids(client) -> set[int]:
    response = client.get("/points/members/total")
    assert_2xx(response)
    return {row["member_id"] for row in response.json()}


def test_deleting_an_account_anonymizes_the_member_and_deletes_the_sign_in(sign_in, outbound, db_session):
    member, event_id = _onboarded_member_with_history(sign_in, outbound, db_session)
    member_id = member.id
    profile = card(db_session, member_id)
    assert profile is not None
    profile_uuid = profile.uuid
    client = sign_in(QU_USER)
    assert member_id in _leaderboard_ids(client)

    response = client.delete("/members/me")

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "member_anonymized": True, "apple_token_revoked": None}
    # onboarding read the user; deleting reads it again, then deletes it
    assert [call.method for call in outbound.to(f"/v1/users/{QU_USER}")] == ["GET", "GET", "DELETE"]

    db_session.expire_all()
    row = db_session.get(Members, member_id)
    assert row is not None, "the row stays for the records that point at it"
    assert row.deleted_at is not None
    assert (row.name, row.public_name) == ("Deleted member", "Deleted member")
    assert (row.email, row.phone_number, row.uni_id, row.clerk_user_id) == (None, None, None, None)
    assert (row.uni_level, row.uni_college) == (None, None)
    assert card(db_session, member_id) is None
    assert (
        db_session.scalar(select(func.count()).select_from(Submissions).where(Submissions.member_id == member_id)) == 0
    )
    log = db_session.scalar(select(EmailLogs).where(EmailLogs.event_id == event_id))
    assert log is not None and (log.member_id, log.data) == (None, None)
    points = db_session.scalar(select(func.count()).select_from(MembersLogs).where(MembersLogs.member_id == member_id))
    assert points == 1, "attendance stays in the event's counts, under no name"

    # Nowhere to be found: leaderboard, history, public card, admin lists and search, attendance lists.
    assert member_id not in _leaderboard_ids(client)
    assert client.get(f"/points/members/{member_id}").status_code == 404
    assert client.get(f"/wallet/{profile_uuid}").status_code == 404
    assert member_id not in {m.id for m in member_queries.get_members(db_session)}
    assert member_queries.count_members(db_session, "Deleted member") == 0
    assert member_queries.get_members_paginated(db_session, 50, 0, "Deleted") == []
    assert member_id not in {r.Member.id for r in log_queries.get_event_attendance(db_session, event_id)}
    with pytest.raises(MemberNotFound):
        member_queries.get_member_by_id(db_session, member_id)
    assert client.get("/members/me").status_code == 404


def test_staff_must_leave_their_role_first(sign_in, outbound, db_session):
    member, _ = _onboarded_member_with_history(sign_in, outbound, db_session)
    db_session.add(SuperAdmins(member_id=member.id))
    db_session.commit()

    response = sign_in(QU_USER).delete("/members/me")

    assert_conflict(response)
    assert response.json()["code"] == "member_is_staff"
    db_session.expire_all()
    row = db_session.get(Members, member.id)
    assert row is not None and row.deleted_at is None and row.clerk_user_id == QU_USER
    assert "DELETE" not in [call.method for call in outbound.to(f"/v1/users/{QU_USER}")], (
        "Clerk is not asked to delete a staff sign-in"
    )


# ---------- reading Clerk's user ----------
