"""A super admin booking over other requests: the bumped one is rescheduled or cancelled, and its department told."""

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, EventRequestStage, EventRequestTasks, PipelineHistory
from tests.pipeline_support import book_complete, finish, submit


@pytest.fixture
def world(pipeline):
    for name in ("Design", "Logistics", "Media"):
        pipeline.department(name)
    ai = pipeline.department("AI")
    cyber = pipeline.department("Cyber")
    ai_leader = pipeline.officer(ai)
    ai_member = pipeline.join(pipeline.person("Member"), ai)
    pipeline.sign_in(ai_leader)
    request_id = book_complete(pipeline, ai)
    return {
        "ai": ai,
        "cyber": cyber,
        "ai_leader": ai_leader,
        "ai_member": ai_member,
        "admin": pipeline.person("Admin", email="admin@example.com"),
        "request_id": request_id,
    }


def book(pipeline, department, bumps=None, start="2026-07-21", end="2026-07-22"):
    body = {"department_id": department.id, "start_date": start, "end_date": end}
    if bumps is not None:
        body["bumps"] = bumps
    return pipeline.client.post("/pipeline/requests", json=body)


def row(pipeline, request_id) -> EventRequests:
    pipeline.session.expire_all()
    return pipeline.session.get(EventRequests, request_id)


def test_the_preview_lists_the_requests_and_who_gets_the_email(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    body = pipeline.client.get("/pipeline/overlaps", params={"from": "2026-07-21", "to": "2026-07-22"}).json()
    assert body["emails_live"] is False
    [entry] = body["requests"]
    assert entry["request"]["id"] == world["request_id"]
    assert entry["request"]["department"]["id"] == world["ai"].id
    assert {r["name"] for r in entry["recipients"]} == {world["ai_leader"].name, world["ai_member"].name}

    # The request being re-dated never bumps itself.
    params = {"from": "2026-07-21", "to": "2026-07-22", "ignore": world["request_id"]}
    assert pipeline.client.get("/pipeline/overlaps", params=params).json()["requests"] == []


def test_only_a_super_admin_sees_the_preview(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    response = pipeline.client.get("/pipeline/overlaps", params={"from": "2026-07-21", "to": "2026-07-22"})
    assert response.status_code == 403


def test_booking_over_a_request_needs_a_decision_for_it(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    response = book(pipeline, world["cyber"])
    assert response.status_code == 409
    assert response.json()["code"] == "overlap_undecided"
    assert book(pipeline, world["cyber"], {"someone-else": "cancel"}).json()["code"] == "overlap_undecided"
    assert row(pipeline, world["request_id"]).stage == EventRequestStage.DRAFT


def test_a_department_leader_cannot_bump(pipeline, world):
    leader = pipeline.officer(world["cyber"])
    pipeline.sign_in(leader)
    response = book(pipeline, world["cyber"], {world["request_id"]: "cancel"})
    assert response.status_code == 409
    assert response.json()["code"] == "day_taken"


def test_reschedule_sends_a_submitted_request_back_to_a_draft_with_no_dates(pipeline, world):
    assert submit(pipeline, world["request_id"]).status_code == 200
    pipeline.sign_in(world["admin"], super_admin=True)
    for team in ("design", "logistics"):
        assert finish(pipeline, world["request_id"], team).status_code == 200
    pipeline.outbound.calls.clear()

    response = book(pipeline, world["cyber"], {world["request_id"]: "reschedule"})
    assert response.status_code == 201, response.text

    bumped = row(pipeline, world["request_id"])
    assert bumped.stage == EventRequestStage.DRAFT
    assert (bumped.start_date, bumped.end_date, bumped.hold_expires_at) == (None, None, None)
    assert bumped.undated_reason.value == "overridden"
    assert bumped.title == "Intro to ML"  # everything in it is kept
    tasks = pipeline.session.scalars(select(EventRequestTasks).where(EventRequestTasks.request_id == bumped.id)).all()
    assert {t.status.value for t in tasks} == {"brief"}
    logistics = next(t for t in tasks if t.team.value == "logistics")
    assert logistics.deliverable is None  # confirmed for the old dates
    assert logistics.brief is not None

    history = pipeline.session.scalars(
        select(PipelineHistory).where(PipelineHistory.request_id == bumped.id, PipelineHistory.action == "overridden")
    ).one()
    assert history.actor_id == world["admin"].id
    assert history.details["outcome"] == "reschedule"
    assert history.details["by_request_id"] == response.json()["id"]

    # Trial mode: the copy goes to the super admin, listing the department's members.
    [blast] = pipeline.outbound.to("/blasts")
    assert blast.params.get_list("emails") == ["admin@example.com"]
    assert world["ai_member"].email in blast.text

    # The department sees it, books new dates, and submits again.
    pipeline.sign_in(world["ai_leader"])
    kinds = [n["kind"] for n in pipeline.client.get("/pipeline/notifications").json()["items"]]
    assert kinds[0] == "overridden"
    redated = pipeline.client.put(
        f"/pipeline/requests/{bumped.id}/dates", json={"start_date": "2026-07-25", "end_date": "2026-07-26"}
    )
    assert redated.status_code == 200, redated.text
    modes = {"day_modes": {"2026-07-25": "on_site", "2026-07-26": "online"}}
    assert pipeline.client.put(f"/pipeline/requests/{bumped.id}/details", json=modes).status_code == 200
    assert submit(pipeline, bumped.id).status_code == 200


def test_cancel_drops_the_request(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    assert book(pipeline, world["cyber"], {world["request_id"]: "cancel"}).status_code == 201
    bumped = row(pipeline, world["request_id"])
    assert bumped.stage == EventRequestStage.CANCELLED
    assert bumped.cancelled_by == world["admin"].id
    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-20", "to": "2026-07-22"}).json()["days"]
    assert [d["status"] for d in days] == ["open", "held", "held"]
    assert {r["department"]["id"] for d in days for r in d["requests"]} == {world["cyber"].id}


def test_a_published_event_is_never_bumped(pipeline, world):
    assert submit(pipeline, world["request_id"]).status_code == 200
    pipeline.sign_in(world["admin"], super_admin=True)
    for team in ("design", "logistics", "media"):
        assert finish(pipeline, world["request_id"], team).status_code == 200
    assert pipeline.client.post(f"/pipeline/requests/{world['request_id']}/publish").status_code == 200

    response = book(pipeline, world["cyber"], {world["request_id"]: "cancel"})
    assert response.status_code == 409
    assert response.json()["code"] == "day_published"


def test_re_dating_onto_a_request_bumps_it(pipeline, world):
    pipeline.sign_in(world["admin"], super_admin=True)
    other = book(pipeline, world["cyber"], start="2026-07-25", end="2026-07-25").json()["id"]
    response = pipeline.client.put(
        f"/pipeline/requests/{other}/dates",
        json={"start_date": "2026-07-21", "end_date": "2026-07-21", "bumps": {world["request_id"]: "reschedule"}},
    )
    assert response.status_code == 200, response.text
    assert row(pipeline, world["request_id"]).undated_reason.value == "overridden"
