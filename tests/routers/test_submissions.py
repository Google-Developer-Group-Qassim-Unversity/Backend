"""A member cancelling their own registration: DELETE /submissions/{form_id}.

Cancelling is a soft delete: the row stays, stamped with ``cancelled_at``, and
every reader of "who is registered" skips it.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.DB import submissions as submission_queries
from app.DB.schema import Events, Forms, Members, MembersGender, Submissions
from tests.utils import assert_2xx, assert_bad_request, assert_conflict, assert_not_found, semester_id_on


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


def active_member_ids(db_session, form: Forms) -> set[int]:
    """Members registered for the form, cancelled ones left out."""
    db_session.expire_all()
    return set(
        db_session.scalars(
            select(Submissions.member_id).where(Submissions.form_id == form.id, Submissions.cancelled_at.is_(None))
        ).all()
    )


def row_of(db_session, form: Forms, member_id: int) -> Submissions:
    """The member's row for the form, cancelled or not."""
    db_session.expire_all()
    return db_session.scalars(
        select(Submissions).where(Submissions.form_id == form.id, Submissions.member_id == member_id)
    ).one()


def test_cancel_keeps_the_row_and_stamps_it(clerk_client: TestClient, db_session, member, seed_refs):
    form = make_form(db_session)
    kept = register(db_session, form, member.id)
    register(db_session, form, seed_refs.sara.id)

    response = clerk_client.delete(f"/submissions/{form.id}")

    assert_2xx(response)
    assert response.json() == {"status": "success"}
    row = row_of(db_session, form, member.id)
    assert row.id == kept.id
    assert row.cancelled_at is not None
    assert active_member_ids(db_session, form) == {seed_refs.sara.id}
    assert clerk_client.get(f"/submissions/{form.id}").json()["submission_status"] is False


def test_registering_again_brings_back_the_same_row_as_a_fresh_registration(
    clerk_client: TestClient, db_session, member
):
    form = make_form(db_session)
    original = register(db_session, form, member.id, is_accepted=1)
    original.is_invited = 1
    db_session.commit()

    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    assert_2xx(clerk_client.post(f"/submissions/{form.id}", params={"submission_type": "none"}))

    row = row_of(db_session, form, member.id)
    assert row.id == original.id
    assert row.cancelled_at is None
    assert row.is_accepted == 0
    assert row.is_invited == 0
    assert row.submission_type.value == "none"
    assert clerk_client.get(f"/submissions/{form.id}").json()["submission_status"] is True


def test_registering_twice_without_cancelling_is_still_refused(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id)

    assert_bad_request(clerk_client.post(f"/submissions/{form.id}", params={"submission_type": "none"}))


def test_cancel_accepted_registration(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id, is_accepted=1)

    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    assert active_member_ids(db_session, form) == set()


def test_cancelling_twice_is_not_found(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id)

    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    assert_not_found(clerk_client.delete(f"/submissions/{form.id}"))


def test_cancel_without_registration_is_not_found(clerk_client: TestClient, db_session, member, seed_refs):
    form = make_form(db_session)
    register(db_session, form, seed_refs.sara.id)

    assert_not_found(clerk_client.delete(f"/submissions/{form.id}"))
    assert active_member_ids(db_session, form) == {seed_refs.sara.id}


@pytest.mark.parametrize("status", ["active", "closed", "draft"])
def test_cancel_after_registration_closes_is_refused(clerk_client: TestClient, db_session, member, status):
    form = make_form(db_session, status=status)
    register(db_session, form, member.id)

    response = clerk_client.delete(f"/submissions/{form.id}")

    assert_conflict(response)
    assert response.json()["code"] == "registration_closed"
    assert row_of(db_session, form, member.id).cancelled_at is None


def test_admins_no_longer_see_or_accept_a_cancelled_registration(
    admin_client: TestClient, db_session, member, seed_refs
):
    """`admin_client` still calls member routes as the FAKE_CLERK member, so it can cancel too."""
    form = make_form(db_session)
    cancelled = register(db_session, form, member.id)
    register(db_session, form, seed_refs.sara.id)
    assert_2xx(admin_client.delete(f"/submissions/{form.id}"))

    listed = admin_client.get(f"/events/submissions/{form.event_id}").json()
    assert {s["member"]["id"] for s in listed} == {seed_refs.sara.id}

    accept = admin_client.put("/submissions/accept", json=[{"submission_id": cancelled.id, "is_accepted": True}])
    assert_bad_request(accept)
    assert row_of(db_session, form, member.id).is_accepted == 0


def test_lookups_skip_a_cancelled_registration_unless_asked(clerk_client: TestClient, db_session, member):
    form = make_form(db_session)
    register(db_session, form, member.id)
    assert_2xx(clerk_client.delete(f"/submissions/{form.id}"))
    db_session.expire_all()

    assert submission_queries.get_submission_by_form_and_member(db_session, form.id, member.id) is None
    found = submission_queries.get_submission_by_form_and_member(db_session, form.id, member.id, include_cancelled=True)
    assert found is not None and found.cancelled_at is not None
