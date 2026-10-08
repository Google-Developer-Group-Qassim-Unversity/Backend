"""Publishing a ready request as a real event."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import (
    DepartmentsLogs,
    EventRequests,
    EventRequestTasks,
    Events,
    Forms,
    Logs,
    Modifications,
    PipelinePenalties,
)
from tests.pipeline_support import book_complete, submit


@pytest.fixture
def world(pipeline, seed_refs):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    leader = pipeline.officer(ai)
    admin = pipeline.person("Admin")
    pipeline.sign_in(leader)
    request_id = book_complete(pipeline, ai)
    submit(pipeline, request_id)
    return {
        "ai": ai,
        "leader": leader,
        "admin": admin,
        "request_id": request_id,
        "actions": {"department_action_id": seed_refs.dept_action.id, "member_action_id": seed_refs.member_action.id},
    }


def finish_all(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    for team in ("design", "logistics", "media"):
        assert (
            pipeline.client.post(f"/pipeline/requests/{world['request_id']}/tasks/{team}/complete").status_code == 200
        )
    pipeline.sign_in(world["leader"])


def publish(pipeline, world, level="intermediate"):
    return pipeline.client.post(
        f"/pipeline/requests/{world['request_id']}/publish", json={**world["actions"], "level": level}
    )


def test_publish_requires_a_level(pipeline, world):
    finish_all(pipeline, world)
    response = pipeline.client.post(f"/pipeline/requests/{world['request_id']}/publish", json=world["actions"])
    assert response.status_code == 422


def test_publish_is_refused_until_every_team_is_done(pipeline, world):
    response = publish(pipeline, world)
    assert response.status_code == 409
    assert response.json()["code"] == "not_ready"


def test_publish_creates_the_event_like_post_events(pipeline, world):
    finish_all(pipeline, world)
    response = publish(pipeline, world)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "published"

    event = pipeline.session.get(Events, body["event_id"])
    assert event.name == "Intro to ML"
    assert event.status.value == "draft"
    # One on-site day, so on-site at the venue from the Logistics brief.
    assert event.location_type.value == "on-site"
    assert event.location == "التيك فالي (60)"
    assert event.start_datetime == datetime(2026, 7, 20, 10, 0)
    assert event.end_datetime == datetime(2026, 7, 21, 12, 0)
    assert event.is_official == 1
    assert event.level.value == "intermediate"
    form = pipeline.session.scalar(select(Forms).where(Forms.event_id == event.id))
    assert form.form_type.value == "registration"
    logs = pipeline.session.scalars(select(Logs).where(Logs.event_id == event.id)).all()
    assert {log.action_id for log in logs} == set(world["actions"].values())

    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-20", "to": "2026-07-21"}).json()["days"]
    assert [d["status"] for d in days] == ["published", "published"]


def test_the_published_event_shows_up_in_events(pipeline, world):
    finish_all(pipeline, world)
    event_id = publish(pipeline, world).json()["event_id"]
    pipeline.sign_in(world["admin"], super_admin=True)
    items = pipeline.client.get("/events/paginated", params={"page_size": 100}).json()["items"]
    assert event_id in [i["id"] for i in items]


def test_a_late_penalty_is_taken_off_once(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    url = f"/pipeline/requests/{world['request_id']}"
    pipeline.client.post(f"{url}/return", json={"notes": "fix"})
    pipeline.freeze(pipeline.now + timedelta(hours=12 + 30))
    pipeline.client.post(f"{url}/resubmit")
    finish_all(pipeline, world)

    event_id = publish(pipeline, world).json()["event_id"]
    penalty = pipeline.session.scalar(
        select(PipelinePenalties).where(PipelinePenalties.request_id == world["request_id"])
    )
    pipeline.session.refresh(penalty)
    mods = pipeline.session.scalars(select(Modifications).where(Modifications.log_id == penalty.applied_log_id)).all()
    assert [(m.type.value, m.value) for m in mods] == [("discount", 2)]
    assert pipeline.session.get(Logs, penalty.applied_log_id).event_id == event_id
    # Bug #4: the event lasts two days, so its department log has a row per day.
    # The penalty sits on its own log with one row, so it counts once, not twice.
    penalty_rows = pipeline.session.scalars(
        select(DepartmentsLogs).where(DepartmentsLogs.log_id == penalty.applied_log_id)
    ).all()
    assert [row.department_id for row in penalty_rows] == [world["ai"].id]
    department_log = pipeline.session.scalar(
        select(Logs).where(Logs.event_id == event_id, Logs.action_id == world["actions"]["department_action_id"])
    )
    assert pipeline.session.scalars(select(Modifications).where(Modifications.log_id == department_log.id)).all() == []

    assert publish(pipeline, world).status_code == 409  # already published: nothing twice


def test_the_requester_is_responsible_and_the_publisher_is_recorded(pipeline, world):
    finish_all(pipeline, world)
    pipeline.sign_in(world["admin"], super_admin=True)
    event_id = publish(pipeline, world).json()["event_id"]

    event = pipeline.session.get(Events, event_id)
    assert event.responsible_member_id == world["leader"].id
    assert event.created_by == world["admin"].id
    details = pipeline.client.get(f"/events/{event_id}/details").json()
    assert details["responsible"] == {"member_id": world["leader"].id, "name": world["leader"].name}
    assert details["created_by"] == {"member_id": world["admin"].id, "name": world["admin"].name}


def test_another_department_cannot_publish(pipeline, world):
    finish_all(pipeline, world)
    other = pipeline.department("Cyber")
    pipeline.sign_in(pipeline.officer(other))
    assert publish(pipeline, world).status_code == 403


def test_a_team_finishing_after_an_early_publish_does_not_reopen_it(pipeline, world):
    """Bug #5: a super admin publishes before Media is done; Media's "Mark done" must not make a second event."""
    pipeline.sign_in(world["admin"], super_admin=True)
    url = f"/pipeline/requests/{world['request_id']}"
    for team in ("design", "logistics"):
        assert pipeline.client.post(f"{url}/tasks/{team}/complete").status_code == 200
    published = publish(pipeline, world)
    assert published.status_code == 200, published.text
    assert published.json()["stage"] == "published"

    late = pipeline.client.post(f"{url}/tasks/media/complete")
    assert late.status_code == 409
    assert late.json()["code"] == "cannot_complete"
    body = pipeline.client.get(url).json()
    assert body["stage"] == "published"
    assert body["actions"]["complete"] == []
    assert publish(pipeline, world).status_code == 409
    events = pipeline.session.scalars(select(Events).where(Events.name == "Intro to ML")).all()
    assert len(events) == 1


def test_deleting_a_published_event_takes_everything_with_it(pipeline, world):
    """Bug #6: the request, its tasks and penalty, the event's points and the penalty's discount all go,
    and the days are free again."""
    booked_at = pipeline.now
    pipeline.sign_in(world["admin"], super_admin=True)
    url = f"/pipeline/requests/{world['request_id']}"
    pipeline.client.post(f"{url}/return", json={"notes": "fix"})
    pipeline.freeze(pipeline.now + timedelta(hours=12 + 30))
    pipeline.client.post(f"{url}/resubmit")
    finish_all(pipeline, world)
    event_id = publish(pipeline, world).json()["event_id"]
    penalty = pipeline.session.scalar(
        select(PipelinePenalties).where(PipelinePenalties.request_id == world["request_id"])
    )
    pipeline.session.refresh(penalty)
    log_ids = pipeline.session.scalars(select(Logs.id).where(Logs.event_id == event_id)).all()
    assert penalty.applied_log_id in log_ids

    pipeline.sign_in(world["admin"], super_admin=True)
    response = pipeline.client.delete(f"/events/{event_id}")
    assert response.status_code == 200, response.text

    pipeline.session.expire_all()
    assert pipeline.session.get(Events, event_id) is None
    assert pipeline.session.get(EventRequests, world["request_id"]) is None
    assert (
        pipeline.session.scalars(
            select(EventRequestTasks).where(EventRequestTasks.request_id == world["request_id"])
        ).all()
        == []
    )
    assert (
        pipeline.session.scalars(
            select(PipelinePenalties).where(PipelinePenalties.request_id == world["request_id"])
        ).all()
        == []
    )
    # Points: the event's department and member rows, and the penalty's discount.
    assert pipeline.session.scalars(select(DepartmentsLogs).where(DepartmentsLogs.log_id.in_(log_ids))).all() == []
    assert pipeline.session.scalars(select(Modifications).where(Modifications.log_id.in_(log_ids))).all() == []
    assert pipeline.client.get(url).status_code == 404

    # Back to before the late resubmit, when those days were past the lockout.
    pipeline.freeze(booked_at)
    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-20", "to": "2026-07-21"}).json()["days"]
    assert [d["status"] for d in days] == ["open", "open"]
    other = pipeline.department("Cyber")
    pipeline.sign_in(pipeline.officer(other))
    booked = pipeline.client.post(
        "/pipeline/requests", json={"department_id": other.id, "start_date": "2026-07-20", "end_date": "2026-07-21"}
    )
    assert booked.status_code == 201, booked.text
