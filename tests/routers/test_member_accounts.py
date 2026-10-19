"""The member app's account routes: POST /members/onboarding and DELETE /members/me.

Clerk is faked at the HTTP transport (``outbound``): each test stubs the Clerk
user it signs in as, so what the routes read from Clerk, and what they write
back, is exactly what crossed the wire.
"""

from datetime import datetime

import httpx
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
from app.services.clerk_api import parse_user
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


def test_student_at_another_university_is_a_student_card_with_its_university(sign_in, outbound, db_session):
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post(
        "/members/onboarding",
        json=answers(
            "other_university",
            name="فاطمة محمد",
            gender="Female",
            personal_email=None,
            university_name="جامعة الملك سعود",
            uni_college="علوم الحاسب",
            uni_level=3,
        ),
    )

    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert member["uni_id"] is None, "a Google account proves no university ID"
    assert member["email"] == "fatima.m@gmail.com", "Google accounts use the address they signed up with"
    assert (member["uni_level"], member["uni_college"]) == (3, "علوم الحاسب")

    profile = card(db_session, member["id"])
    assert profile is not None
    assert (profile.user_status, profile.education_level) == ("student", "university")
    assert (profile.institution, profile.major) == ("جامعة الملك سعود", "علوم الحاسب")
    assert profile.theme_id == "gdg-red"

    metadata = metadata_written(outbound, GOOGLE_USER)
    assert metadata["memberStatus"] == "other_university"
    assert metadata["universityName"] == "جامعة الملك سعود"
    assert metadata["uni_id"] is None
    assert metadata["personalEmail"] == "fatima.m@gmail.com"


def test_high_school_student(sign_in, outbound, db_session):
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post(
        "/members/onboarding",
        json=answers("high_school", gender="Female", personal_email=None, school_name="ثانوية بريدة الأولى"),
    )

    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert (member["uni_level"], member["uni_college"]) == (None, None)
    profile = card(db_session, member["id"])
    assert profile is not None
    assert (profile.user_status, profile.education_level, profile.institution) == (
        "student",
        "highschool",
        "ثانوية بريدة الأولى",
    )
    assert metadata_written(outbound, GOOGLE_USER)["highSchoolName"] == "ثانوية بريدة الأولى"


def test_high_school_name_is_optional(sign_in, outbound):
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post("/members/onboarding", json=answers("high_school", personal_email=None))

    assert response.status_code == 201, response.text


def test_graduate(sign_in, outbound, db_session):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers("graduate", uni_college="كلية الهندسة"))

    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert (member["uni_level"], member["uni_college"]) == (None, "كلية الهندسة")
    profile = card(db_session, member["id"])
    assert profile is not None
    assert (profile.user_status, profile.education_level) == ("graduate", "university")


@pytest.mark.parametrize(
    "body",
    [
        answers("other_university"),  # no university_name
        answers("other_university", university_name="   "),
        answers("high_school", uni_level=2),
        answers("high_school", uni_college="كلية الحاسب"),
        answers("graduate", uni_level=5),
        answers("graduate", school_name="ثانوية"),
        answers("qu_student", university_name="جامعة الملك سعود"),
        answers("qu_student", school_name="ثانوية"),
        answers("qu_student", uni_level=0),
        answers("qu_student", uni_level=11),
        answers("other_university", university_name="x" * 151),
        answers("qu_student", uni_college="x" * 101),
        answers("alumni"),
    ],
    ids=[
        "other_university without university_name",
        "other_university with a blank university_name",
        "high_school with a level",
        "high_school with a college",
        "graduate with a level",
        "graduate with a school",
        "qu_student with a university_name",
        "qu_student with a school",
        "level 0",
        "level 11",
        "university_name too long",
        "college too long",
        "unknown status",
    ],
)
def test_fields_must_fit_the_status(sign_in, outbound, body):
    stub_qu_account(outbound)

    assert_unprocessable(sign_in(QU_USER).post("/members/onboarding", json=body))
    assert outbound.calls == [], "a body that fails validation never reaches Clerk"


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "x" * 51},
        {"name": "   "},
        {"public_name": "x" * 151},
        {"gender": "male"},
        {"phone_number": "0612345678"},
        {"phone_number": "055123456"},
        {"phone_number": "+966551234567"},
        {"phone_number": "٠٥٥١٢٣٤٥٦٧"},
        {"personal_email": "not-an-email"},
    ],
    ids=lambda fields: next(iter(fields)),
)
def test_personal_fields_are_validated(sign_in, outbound, fields):
    stub_qu_account(outbound)

    assert_unprocessable(sign_in(QU_USER).post("/members/onboarding", json=answers(**fields)))


def test_gender_is_required(sign_in, outbound):
    stub_qu_account(outbound)
    body = answers()
    del body["gender"]

    assert_unprocessable(sign_in(QU_USER).post("/members/onboarding", json=body))


def test_phone_is_optional_and_public_name_is_the_members_own(sign_in, outbound):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers(phone_number="", public_name="  Abdullah  "))

    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert member["phone_number"] is None
    assert member["public_name"] == "Abdullah"
    assert metadata_written(outbound, QU_USER)["saudiPhone"] is None


# ---------- the personal email ----------


def test_a_university_id_account_must_give_a_personal_email(sign_in, outbound):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers(personal_email=None))

    assert_unprocessable(response)
    assert response.json()["code"] == "personal_email_required"


def test_the_personal_email_is_not_the_university_one(sign_in, outbound):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers(personal_email="444111222@QU.edu.sa"))

    assert_unprocessable(response)
    assert response.json()["code"] == "personal_email_university"


def test_a_google_account_cannot_name_an_address_it_does_not_have(sign_in, outbound):
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post("/members/onboarding", json=answers(personal_email="someone@else.com"))

    assert_unprocessable(response)
    assert response.json()["code"] == "personal_email_not_on_account"


def test_a_google_account_may_send_its_own_address(sign_in, outbound):
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post("/members/onboarding", json=answers(personal_email="Fatima.M@gmail.com"))

    assert response.status_code == 201, response.text


# ---------- repeating, linking and conflicts ----------


def test_onboarding_twice_updates_the_same_member(sign_in, outbound, db_session):
    stub_qu_account(outbound)
    client = sign_in(QU_USER)

    first = client.post("/members/onboarding", json=answers())
    second = client.post("/members/onboarding", json=answers(uni_level=5))

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["already_exists"] is True
    assert second.json()["member"]["id"] == first.json()["member"]["id"]
    assert second.json()["member"]["uni_level"] == 5
    count = db_session.scalar(select(func.count()).select_from(Members).where(Members.clerk_user_id == QU_USER))
    assert count == 1
    assert len(outbound.to(f"/v1/users/{QU_USER}/metadata")) == 2


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


def test_links_the_unclaimed_record_with_a_proven_email(sign_in, outbound, db_session):
    existing = Members(name="فاطمة", email="fatima.m@gmail.com", gender=MembersGender.FEMALE)
    db_session.add(existing)
    db_session.commit()
    stub_google_account(outbound)

    response = sign_in(GOOGLE_USER).post(
        "/members/onboarding", json=answers("graduate", gender="Female", personal_email=None)
    )

    assert response.status_code == 200, response.text
    assert response.json()["member"]["id"] == existing.id


def test_a_typed_email_does_not_link_a_record(sign_in, outbound, db_session):
    """The personal email is contact data: records are linked by the addresses the user proved to Clerk."""
    existing = Members(name="Someone", email="someone@example.com", gender=MembersGender.MALE)
    db_session.add(existing)
    db_session.commit()
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers(personal_email="someone@example.com"))

    assert_conflict(response)
    assert response.json()["code"] == "email_in_use"
    db_session.refresh(existing)
    assert existing.clerk_user_id is None
    assert outbound.to(f"/v1/users/{QU_USER}/metadata") == []


def test_an_address_the_user_did_not_prove_does_not_link_a_record(sign_in, outbound, db_session):
    """An address with Clerk's ``admin`` verification was added by a server, not proved by the user."""
    existing = Members(name="Someone", email="someone@gmail.com", gender=MembersGender.MALE)
    db_session.add(existing)
    db_session.commit()
    outbound.stub(
        f"/v1/users/{GOOGLE_USER}",
        json=clerk_user(GOOGLE_USER, ("fatima.m@gmail.com", "from_oauth_google"), ("someone@gmail.com", "admin")),
    )

    response = sign_in(GOOGLE_USER).post("/members/onboarding", json=answers("graduate", personal_email=None))

    assert response.status_code == 201, response.text
    assert response.json()["member"]["id"] != existing.id
    db_session.refresh(existing)
    assert existing.clerk_user_id is None


def test_the_university_id_is_taken_only_from_a_proven_address(sign_in, outbound, db_session):
    """A <uni_id>@qu.edu.sa address with ``admin`` verification proves no university ID."""
    existing = Members(name="Student", email="student@example.com", uni_id="444111222", gender=MembersGender.MALE)
    db_session.add(existing)
    db_session.commit()
    outbound.stub(f"/v1/users/{QU_USER}", json=clerk_user(QU_USER, ("444111222@qu.edu.sa", "admin")))

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 201, response.text
    assert response.json()["member"]["uni_id"] is None
    assert response.json()["member"]["id"] != existing.id
    assert metadata_written(outbound, QU_USER)["uni_id"] is None
    db_session.refresh(existing)
    assert existing.clerk_user_id is None


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


def test_an_email_on_another_members_record_is_a_conflict(sign_in, outbound, seed_refs):
    stub_qu_account(outbound)

    response = sign_in(QU_USER).post("/members/onboarding", json=answers(personal_email=seed_refs.ahmed.email))

    assert_conflict(response)
    assert response.json()["code"] == "email_in_use"


# ---------- when Clerk fails ----------


def test_clerk_down_before_anything_is_written(sign_in, outbound, db_session):
    outbound.stub(f"/v1/users/{QU_USER}", status_code=500, json={"errors": []})

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "clerk_unavailable"
    assert member_row(db_session, QU_USER) is None


def test_clerk_unreachable(sign_in, outbound, db_session):
    outbound.stub(f"/v1/users/{QU_USER}", exc=httpx.ConnectError("no route"))

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 502, response.text
    assert member_row(db_session, QU_USER) is None


def test_a_sign_in_clerk_no_longer_has_is_signed_out(sign_in, outbound):
    outbound.stub(f"/v1/users/{QU_USER}", status_code=404, json={"errors": [{"code": "resource_not_found"}]})

    response = sign_in(QU_USER).post("/members/onboarding", json=answers())

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "not_authenticated"


def test_a_failed_metadata_write_is_finished_by_a_retry(sign_in, outbound, db_session):
    stub_qu_account(outbound)
    outbound.stub(f"/v1/users/{QU_USER}/metadata", status_code=503, json={"errors": []})
    client = sign_in(QU_USER)

    failed = client.post("/members/onboarding", json=answers())

    assert failed.status_code == 502, failed.text
    assert member_row(db_session, QU_USER) is not None, "the record is saved before Clerk is written"

    outbound.stub(f"/v1/users/{QU_USER}/metadata", json={"object": "user", "id": QU_USER})
    retried = client.post("/members/onboarding", json=answers())

    assert retried.status_code == 200, retried.text
    assert outbound.to(f"/v1/users/{QU_USER}/metadata")[-1].json["public_metadata"]["onboardingComplete"] is True


def test_without_a_clerk_secret_nothing_happens(sign_in, outbound, monkeypatch, db_session):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    reload_settings()

    onboarding = sign_in(QU_USER).post("/members/onboarding", json=answers())
    deletion = sign_in(QU_USER).delete("/members/me")

    for response in (onboarding, deletion):
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "clerk_not_configured"
    assert outbound.calls == []
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
    assert response.json() == {"deleted": True, "member_anonymized": True}
    assert [call.method for call in outbound.to(f"/v1/users/{QU_USER}")] == ["GET", "DELETE"]

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


def test_deleting_again_only_asks_clerk_again(sign_in, outbound, db_session):
    _onboarded_member_with_history(sign_in, outbound, db_session)
    client = sign_in(QU_USER)
    assert client.delete("/members/me").status_code == 200

    outbound.stub(f"/v1/users/{QU_USER}", status_code=404, json={"errors": [{"code": "resource_not_found"}]})
    again = client.delete("/members/me")

    assert again.status_code == 200, again.text
    assert again.json() == {"deleted": True, "member_anonymized": False}


def test_a_clerk_failure_leaves_a_retry_path(sign_in, outbound, db_session):
    member, _ = _onboarded_member_with_history(sign_in, outbound, db_session)
    outbound.stub(f"/v1/users/{QU_USER}", status_code=500, json={"errors": []})
    client = sign_in(QU_USER)

    failed = client.delete("/members/me")

    assert failed.status_code == 502, failed.text
    assert failed.json()["code"] == "clerk_unavailable"
    db_session.expire_all()
    row = db_session.get(Members, member.id)
    assert row is not None and row.deleted_at is not None, "the data is gone before Clerk is asked"

    outbound.stub(f"/v1/users/{QU_USER}", json={"object": "user", "id": QU_USER, "deleted": True})
    retried = client.delete("/members/me")

    assert retried.status_code == 200, retried.text
    assert retried.json() == {"deleted": True, "member_anonymized": False}
    assert [call.method for call in outbound.to(f"/v1/users/{QU_USER}")].count("DELETE") == 2


def test_a_sign_in_without_a_member_record_is_deleted_too(sign_in, outbound):
    response = sign_in("user_never_onboarded").delete("/members/me")

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "member_anonymized": False}
    assert outbound.one("/v1/users/user_never_onboarded").method == "DELETE"


def test_deleting_touches_only_the_record_linked_to_the_sign_in(sign_in, outbound, db_session, seed_refs):
    """Ahmed's record has a university ID and an email, but no sign-in links to it."""
    response = sign_in("user_someone_else").delete("/members/me")

    assert response.status_code == 200, response.text
    assert response.json()["member_anonymized"] is False
    db_session.refresh(seed_refs.ahmed)
    assert seed_refs.ahmed.deleted_at is None and seed_refs.ahmed.email == "ahmed@example.com"


def test_staff_must_leave_their_role_first(sign_in, outbound, db_session):
    member, _ = _onboarded_member_with_history(sign_in, outbound, db_session)
    db_session.add(SuperAdmins(member_id=member.id))
    db_session.commit()
    calls_before = len(outbound.calls)

    response = sign_in(QU_USER).delete("/members/me")

    assert_conflict(response)
    assert response.json()["code"] == "member_is_staff"
    db_session.expire_all()
    row = db_session.get(Members, member.id)
    assert row is not None and row.deleted_at is None and row.clerk_user_id == QU_USER
    assert len(outbound.calls) == calls_before, "Clerk is not asked to delete a staff sign-in"


def test_a_member_who_deleted_their_account_leaves_past_rosters(sign_in, outbound, db_session, club):
    """Only this semester's roster blocks a deletion; an earlier semester's keeps the seat, under no name."""
    member, _ = _onboarded_member_with_history(sign_in, outbound, db_session)
    department = club.department("Media")
    club.join(member, department, semester=club.previous)
    db_session.commit()
    client = sign_in(QU_USER)

    def roster_names() -> list[str]:
        response = client.get("/club-structure/public", params={"semester": club.previous.hijri_code})
        assert_2xx(response)
        listed = next(d for d in response.json()["departments"] if d["id"] == department.id)
        return listed["members"]

    assert roster_names() == ["عبدالله الأحمد"]
    assert client.delete("/members/me").status_code == 200
    assert roster_names() == []


def test_a_deleted_record_is_never_linked_again(sign_in, outbound, db_session):
    member, _ = _onboarded_member_with_history(sign_in, outbound, db_session)
    assert sign_in(QU_USER).delete("/members/me").status_code == 200

    # The same student signs up again: a new Clerk user, the same university mailbox and personal email.
    stub_qu_account(outbound, user_id="user_qu_again")
    response = sign_in("user_qu_again").post("/members/onboarding", json=answers())

    assert response.status_code == 201, response.text
    assert response.json()["member"]["id"] != member.id


# ---------- reading Clerk's user ----------


def test_only_addresses_the_user_proved_count():
    user = parse_user(
        clerk_user(
            "user_x",
            ("444111222@qu.edu.sa", "email_code"),
            ("me@gmail.com", "from_oauth_google"),
            ("relay@privaterelay.appleid.com", "from_oauth_apple"),
            ("link@example.com", "email_link"),
            ("reset@example.com", "reset_password_email_code"),
            ("added@example.com", "admin"),
            ("ticket@example.com", "ticket"),
            ("scim@example.com", "scim"),
            ("saml@example.com", "saml"),
        )
    )

    assert user.uni_id == "444111222"
    assert user.proven_emails == (
        "444111222@qu.edu.sa",
        "me@gmail.com",
        "relay@privaterelay.appleid.com",
        "link@example.com",
        "reset@example.com",
    )


def test_the_university_id_comes_only_from_a_proven_primary_address():
    secondary = parse_user(
        clerk_user("u", ("me@gmail.com", "from_oauth_google"), ("444111222@qu.edu.sa", "email_code"))
    )
    unproven = parse_user(clerk_user("u", ("444111222@qu.edu.sa", "admin")))
    unverified_data = clerk_user("u", ("444111222@qu.edu.sa", "email_code"))
    unverified_data["email_addresses"][0]["verification"]["status"] = "unverified"

    assert secondary.uni_id is None
    assert unproven.uni_id is None and unproven.primary_email == "444111222@qu.edu.sa"
    assert parse_user(unverified_data).uni_id is None
    assert parse_user(unverified_data).primary_email is None
    assert parse_user(clerk_user("u", ("44411122@qu.edu.sa", "email_code"))).uni_id is None
