"""A member cancelling their own registration: DELETE /submissions/{form_id}."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.DB.schema import Events, Forms, Members, MembersGender, Submissions
from tests.utils import assert_2xx, assert_conflict, assert_not_found, semester_id_on


@pytest.fixture
def member(db_session) -> Members:
    """The member behind FAKE_CLERK_CREDENTIALS (resolved by its uni_id)."""
    member = Members(
        name="Test Member",
        email="test@example.com",
        phone_number="0501234567",
        uni_id="123456789",
        gender=MembersGender.MALE,
        uni_level=4,
        uni_college="Engineering",
    )
    db_session.add(member)
    db_session.commit()
    return member


def make_form(db_session, status: str = "open") -> Forms:
    event = Events(
        name="test event",
        description="test description",
        start_datetime=datetime(2026, 3, 1, 0, 0, 0),
        end_datetime=datetime(2026, 3, 2, 0, 0),
        semester_id=semester_id_on(db_session, "2026-03-02"),
        status=status,
        location_type="on-site",
        location="the moon",
    )
    db_session.add(event)
    db_session.flush()
    form = Forms(event_id=event.id, form_type="registration")
    db_session.add(form)
    db_session.commit()
    return form


def register(db_session, form: Forms, member_id: int, is_accepted: int = 0) -> Submissions:
    submission = Submissions(
        form_id=form.id, member_id=member_id, submission_type="registration", is_accepted=is_accepted, is_invited=0
    )
    db_session.add(submission)
    db_session.commit()
    return submission


def submission_ids(db_session, form: Forms) -> set[int]:
    db_session.expire_all()
    return set(db_session.scalars(select(Submissions.member_id).where(Submissions.form_id == form.id)).all())


def test_cancel_removes_only_the_callers_registration(clerk_client: TestClient, db_session, member, seed_refs):
    form = make_form(db_session)
    register(db_session, form, member.id)
    register(db_session, form, seed_refs.sara.id)

    response = clerk_client.delete(f"/submissions/{form.id}")

    assert_2xx(response)
    assert response.json() == {"status": "success"}
    assert submission_ids(db_session, form) == {seed_refs.sara.id}
    assert clerk_client.get(f"/submissions/{form.id}").json()["submission_status"] is False


def test_cancelled_member_can_register_again(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id)

    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    assert_2xx(clerk_client.post(f"/submissions/{form.id}", params={"submission_type": "none"}))
    assert submission_ids(db_session, form) == {member.id}


def test_cancel_accepted_registration(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id, is_accepted=1)

    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    assert submission_ids(db_session, form) == set()


def test_cancel_without_registration_is_not_found(clerk_client: TestClient, db_session, member, seed_refs):
    form = make_form(db_session)
    register(db_session, form, seed_refs.sara.id)

    assert_not_found(clerk_client.delete(f"/submissions/{form.id}"))
    assert submission_ids(db_session, form) == {seed_refs.sara.id}


@pytest.mark.parametrize("status", ["active", "closed", "draft"])
def test_cancel_after_registration_closes_is_refused(clerk_client: TestClient, db_session, member, status):
    form = make_form(db_session, status=status)
    register(db_session, form, member.id)

    response = clerk_client.delete(f"/submissions/{form.id}")

    assert_conflict(response)
    assert response.json()["code"] == "registration_closed"
    assert submission_ids(db_session, form) == {member.id}
