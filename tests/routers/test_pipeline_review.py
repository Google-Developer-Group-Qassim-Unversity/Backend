"""Design and Logistics work their requests: done, return, resubmit, late penalty, Media, ready."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import PipelinePenalties
from app.services.pipeline_sweep import run_sweep
from tests.pipeline_support import book_complete, submit


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    pipeline.teams(design, logistics, media)
    ai = pipeline.department("AI")
    people = {
        "ai": pipeline.officer(ai),
        "design": pipeline.officer(design, name="Designer"),
        "logistics": pipeline.officer(logistics, name="Logistics"),
        "media": pipeline.officer(media, name="Media"),
    }
    pipeline.sign_in(people["ai"])
    request_id = book_complete(pipeline, ai)
    assert submit(pipeline, request_id).status_code == 200
    return {"request_id": request_id, "people": people, "ai": ai}


def url(world, tail=""):
    return f"/pipeline/requests/{world['request_id']}{tail}"


def as_(pipeline, world, who):
    pipeline.sign_in(world["people"][who])


def test_inbox_shows_requests_waiting_on_my_team(pipeline, world):
    as_(pipeline, world, "design")
    items = pipeline.client.get("/pipeline/inbox").json()
    assert [(i["request"]["id"], i["team"]) for i in items] == [(world["request_id"], "design")]
    as_(pipeline, world, "media")
    assert pipeline.client.get("/pipeline/inbox").json() == []


def test_only_design_can_return(pipeline, world):
    as_(pipeline, world, "logistics")
    assert pipeline.client.post(url(world, "/return"), json={"notes": "x"}).status_code == 403


def test_return_once_within_two_days(pipeline, world):
    as_(pipeline, world, "design")
    detail = pipeline.client.get(url(world)).json()
    assert detail["actions"]["can_return"] is True
    response = pipeline.client.post(url(world, "/return"), json={"notes": "The content is unclear"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "returned"
    assert body["return_notes"] == "The content is unclear"
    assert body["return_due_at"].startswith("2026-07-15T21:00:00")

    as_(pipeline, world, "ai")
    body = pipeline.client.get(url(world)).json()
    assert body["can_edit"] is True
    assert pipeline.client.post(url(world, "/resubmit")).status_code == 200

    as_(pipeline, world, "design")
    response = pipeline.client.post(url(world, "/return"), json={"notes": "again"})
    assert response.status_code == 409


def test_return_after_two_days_is_refused(pipeline, world):
    pipeline.freeze(pipeline.now + timedelta(days=2, seconds=1))
    as_(pipeline, world, "design")
    assert pipeline.client.post(url(world, "/return"), json={"notes": "late"}).status_code == 409


def test_resubmitting_on_time_costs_nothing(pipeline, world):
    as_(pipeline, world, "design")
    pipeline.client.post(url(world, "/return"), json={"notes": "fix"})
    pipeline.freeze(pipeline.now + timedelta(hours=11))
    as_(pipeline, world, "ai")
    body = pipeline.client.post(url(world, "/resubmit")).json()
    assert body["stage"] == "in_review"
    assert body["penalty"] is None


def test_resubmitting_30_hours_late_is_two_late_days(pipeline, world):
    as_(pipeline, world, "design")
    pipeline.client.post(url(world, "/return"), json={"notes": "fix"})
    pipeline.freeze(pipeline.now + timedelta(hours=12 + 30))
    as_(pipeline, world, "ai")
    body = pipeline.client.post(url(world, "/resubmit")).json()
    assert body["penalty"] == {"late_days": 2, "points": 2, "applied": False}


def test_the_sweep_grows_the_penalty_without_duplicating_it(pipeline, world):
    as_(pipeline, world, "design")
    pipeline.client.post(url(world, "/return"), json={"notes": "fix"})
    due = pipeline.now + timedelta(hours=12)

    assert run_sweep(pipeline.session, due + timedelta(hours=1)).penalties_grown == 1
    assert run_sweep(pipeline.session, due + timedelta(hours=2)).penalties_grown == 0
    assert run_sweep(pipeline.session, due + timedelta(hours=25)).penalties_grown == 1
    rows = pipeline.session.scalars(
        select(PipelinePenalties).where(PipelinePenalties.request_id == world["request_id"])
    ).all()
    assert [(r.late_days, r.points) for r in rows] == [(2, 2)]


def test_logistics_keeps_working_while_returned(pipeline, world):
    as_(pipeline, world, "design")
    pipeline.client.post(url(world, "/return"), json={"notes": "fix"})
    as_(pipeline, world, "logistics")
    assert pipeline.client.post(url(world, "/tasks/logistics/complete")).status_code == 200


def test_design_done_sends_to_media_and_all_three_make_it_ready(pipeline, world):
    as_(pipeline, world, "logistics")
    assert pipeline.client.post(url(world, "/tasks/logistics/complete")).json()["stage"] == "in_review"

    as_(pipeline, world, "design")
    body = pipeline.client.post(url(world, "/tasks/design/complete")).json()
    assert body["stage"] == "media"
    assert {t["team"]: t["status"] for t in body["tasks"]} == {"design": "done", "logistics": "done", "media": "open"}

    as_(pipeline, world, "media")
    assert [i["team"] for i in pipeline.client.get("/pipeline/inbox").json()] == ["media"]
    body = pipeline.client.post(url(world, "/tasks/media/complete")).json()
    assert body["stage"] == "ready"

    as_(pipeline, world, "ai")
    kinds = [n["kind"] for n in pipeline.client.get("/pipeline/notifications").json()["items"]]
    assert kinds[0] == "ready_to_publish"
    assert kinds.count("task_done") == 3


def test_a_team_cannot_complete_another_teams_part(pipeline, world):
    as_(pipeline, world, "logistics")
    assert pipeline.client.post(url(world, "/tasks/design/complete")).status_code == 403


def test_a_super_admin_can_do_every_step(pipeline, world):
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    pipeline.freeze(pipeline.now + timedelta(days=5))
    assert pipeline.client.post(url(world, "/return"), json={"notes": "late but allowed"}).status_code == 200
    assert pipeline.client.post(url(world, "/resubmit")).status_code == 200
    for team in ("design", "logistics", "media"):
        assert pipeline.client.post(url(world, f"/tasks/{team}/complete")).status_code == 200
    assert pipeline.client.get(url(world)).json()["stage"] == "ready"
