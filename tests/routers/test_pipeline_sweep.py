"""The sweep: expiring holds, with a frozen clock."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, EventRequestUndatedReason, PipelineNotifications
from app.services.pipeline_sweep import run_sweep
from tests.pipeline_support import book_complete, submit


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    leader = pipeline.officer(ai)
    pipeline.sign_in(leader)
    return {"ai": ai, "leader": leader}


def sweep(pipeline, now):
    result = run_sweep(pipeline.session, now)
    pipeline.session.commit()
    return result


def request_row(pipeline, request_id):
    pipeline.session.expire_all()
    return pipeline.session.scalar(select(EventRequests).where(EventRequests.id == request_id))


def test_a_hold_is_expired_one_second_after_not_before(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    expires = pipeline.now + timedelta(hours=24)

    assert sweep(pipeline, expires - timedelta(seconds=1)).expired_holds == 0
    assert request_row(pipeline, request_id).start_date is not None

    assert sweep(pipeline, expires + timedelta(seconds=1)).expired_holds == 1
    row = request_row(pipeline, request_id)
    assert row.start_date is None
    assert row.undated_reason == EventRequestUndatedReason.HOLD_EXPIRED
    assert row.title == "Intro to ML"  # the draft keeps everything else


def test_running_twice_notifies_once(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    later = pipeline.now + timedelta(hours=25)
    sweep(pipeline, later)
    assert sweep(pipeline, later).expired_holds == 0
    notes = pipeline.session.scalars(
        select(PipelineNotifications).where(PipelineNotifications.request_id == request_id)
    ).all()
    assert [n.kind.value for n in notes] == ["hold_expired"]


def test_submitted_requests_are_never_touched(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    assert sweep(pipeline, pipeline.now + timedelta(days=10)).expired_holds == 0
    assert request_row(pipeline, request_id).start_date is not None


def test_trial_email_goes_to_whoever_booked(pipeline, world):
    book_complete(pipeline, world["ai"])
    result = sweep(pipeline, pipeline.now + timedelta(hours=25))
    assert [e.emails for e in result.emails] == [[world["leader"].email]]


def test_only_a_super_admin_can_run_the_sweep_by_hand(pipeline, world):
    assert pipeline.client.post("/pipeline/sweep").status_code == 403
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    response = pipeline.client.post("/pipeline/sweep")
    assert response.status_code == 200, response.text
    assert response.json()["ran"] is True
