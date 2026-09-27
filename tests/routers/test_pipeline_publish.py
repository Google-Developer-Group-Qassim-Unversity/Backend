"""Publishing a ready request as a real event."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import Events, Forms, Logs, Modifications, PipelinePenalties
from tests.pipeline_support import book_complete, submit


@pytest.fixture
def world(pipeline, seed_refs):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    pipeline.teams(design, logistics, media)
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


def publish(pipeline, world):
    return pipeline.client.post(f"/pipeline/requests/{world['request_id']}/publish", json=world["actions"])


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
    department_log = pipeline.session.scalar(
        select(Logs).where(Logs.event_id == event_id, Logs.action_id == world["actions"]["department_action_id"])
    )
    mods = pipeline.session.scalars(select(Modifications).where(Modifications.log_id == department_log.id)).all()
    assert [(m.type.value, m.value) for m in mods] == [("discount", 2)]
    penalty = pipeline.session.scalar(
        select(PipelinePenalties).where(PipelinePenalties.request_id == world["request_id"])
    )
    pipeline.session.refresh(penalty)
    assert penalty.applied_log_id == department_log.id

    assert publish(pipeline, world).status_code == 409  # already published: nothing twice


def test_another_department_cannot_publish(pipeline, world):
    finish_all(pipeline, world)
    other = pipeline.department("Cyber")
    pipeline.sign_in(pipeline.officer(other))
    assert publish(pipeline, world).status_code == 403
