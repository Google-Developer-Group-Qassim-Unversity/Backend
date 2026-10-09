"""Who did what: the who-and-when on every request, and its history."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, PipelineHistory
from app.services.pipeline_sweep import run_sweep
from tests.pipeline_support import MEET_LINK, book_complete, finish, submit, upload_poster


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    people = {
        "ai": pipeline.officer(ai),
        "vp": pipeline.officer(ai, "vp", name="VP"),
        "design": pipeline.officer(design, name="Designer"),
        "logistics": pipeline.officer(logistics, name="Logistics"),
        "media": pipeline.officer(media, name="Media"),
    }
    pipeline.sign_in(people["ai"])
    request_id = book_complete(pipeline, ai)
    return {"request_id": request_id, "people": people, "ai": ai}


def url(world, tail=""):
    return f"/pipeline/requests/{world['request_id']}{tail}"


def ref(member):
    return {"member_id": member.id, "name": member.name}


def steps(body):
    """The history as (action, who) pairs, oldest first."""
    return [(h["action"], h["actor"]["member_id"] if h["actor"] else None) for h in body["history"]]


def test_every_step_says_who_did_it_and_when(pipeline, world):
    people = world["people"]
    pipeline.sign_in(people["vp"])  # a VP submits what the leader booked
    assert submit(pipeline, world["request_id"]).status_code == 200

    pipeline.sign_in(people["design"])
    pipeline.client.post(url(world, "/return"), json={"notes": "fix the content"})
    pipeline.sign_in(people["ai"])
    pipeline.client.post(url(world, "/resubmit"))
    pipeline.sign_in(people["logistics"])
    assert finish(pipeline, world["request_id"], "logistics").status_code == 200
    pipeline.sign_in(people["design"])
    assert upload_poster(pipeline, world["request_id"]).status_code == 200
    assert finish(pipeline, world["request_id"], "design").status_code == 200  # uploads a second poster
    pipeline.sign_in(people["media"])
    assert finish(pipeline, world["request_id"], "media").status_code == 200
    pipeline.sign_in(people["ai"])
    body = pipeline.client.post(url(world, "/publish")).json()

    assert body["requested_by"] == ref(people["ai"])
    assert body["requested_at"]
    assert body["submitted_by"] == ref(people["vp"])
    assert body["returned_by"] == ref(people["design"])
    assert body["published_by"] == ref(people["ai"])
    assert body["published_at"]
    done = {t["team"]: t["done_by"] for t in body["tasks"]}
    assert done == {
        "design": ref(people["design"]),
        "logistics": ref(people["logistics"]),
        "media": ref(people["media"]),
    }
    assert all(t["done_at"] for t in body["tasks"])

    ai, vp, design, logistics, media = (people[k].id for k in ("ai", "vp", "design", "logistics", "media"))
    assert steps(body) == [
        ("booked", ai),
        ("details_edited", ai),
        ("brief_edited", ai),
        ("brief_edited", ai),
        ("submitted", vp),
        ("returned", design),
        ("resubmitted", ai),
        ("confirmation_edited", logistics),
        ("task_done", logistics),
        ("poster_uploaded", design),
        ("poster_uploaded", design),
        ("task_done", design),
        ("task_done", media),
        ("published", ai),
    ]
    history = body["history"]
    assert history[5]["details"] == {"notes": "fix the content"}
    first, second = history[9]["details"], history[10]["details"]
    assert second["replaced"] == first["poster_url"]  # who swapped the poster, and from what
    assert history[-1]["details"] == {"event_id": body["event_id"]}


def test_typing_in_a_form_is_one_row_per_person(pipeline, world):
    for title in ("Intro", "Intro to", "Intro to ML!"):
        pipeline.client.put(url(world, "/details"), json={"title": title})
    pipeline.sign_in(world["people"]["vp"])
    pipeline.client.put(url(world, "/details"), json={"title": "Intro to ML"})
    body = pipeline.client.get(url(world)).json()
    ai, vp = world["people"]["ai"].id, world["people"]["vp"].id
    assert steps(body)[-2:] == [("details_edited", ai), ("details_edited", vp)]
    # The design and logistics briefs are separate forms.
    assert [h["details"] for h in body["history"] if h["action"] == "brief_edited"] == [
        {"team": "design"},
        {"team": "logistics"},
    ]


def test_dates_logistics_moved_say_from_what_to_what(pipeline, world):
    submit(pipeline, world["request_id"])
    pipeline.sign_in(world["people"]["logistics"])
    tasks = pipeline.client.get(url(world)).json()["tasks"]
    confirmation = next(t for t in tasks if t["team"] == "logistics")["deliverable"]
    confirmation.update(start_date="2026-07-22", end_date="2026-07-22", day_modes={"2026-07-22": "online"})
    confirmation["meet_link"] = MEET_LINK
    pipeline.client.put(url(world, "/deliverables/logistics"), json={"deliverable": confirmation})
    body = pipeline.client.post(url(world, "/tasks/logistics/complete")).json()
    moved = next(h for h in body["history"] if h["action"] == "dates_moved")
    assert moved["actor"] == ref(world["people"]["logistics"])
    assert moved["details"] == {"from": ["2026-07-20", "2026-07-21"], "to": ["2026-07-22", "2026-07-22"]}


def test_what_the_sweep_did_has_no_person(pipeline, world):
    run_sweep(pipeline.session, pipeline.now + timedelta(hours=25))
    body = pipeline.client.get(url(world)).json()
    expired = body["history"][-1]
    assert (expired["action"], expired["actor"]) == ("hold_expired", None)


def test_a_ban_is_recorded_on_the_calendar_and_on_each_request_it_undated(pipeline, world):
    submit(pipeline, world["request_id"])
    logistics = world["people"]["logistics"]
    pipeline.sign_in(logistics)
    response = pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-21"], "reason": "Exams"})
    assert response.status_code == 200, response.text

    calendar_rows = pipeline.session.scalars(
        select(PipelineHistory).where(PipelineHistory.request_id.is_(None), PipelineHistory.action == "days_banned")
    ).all()
    assert [(r.actor_id, r.details) for r in calendar_rows] == [
        (logistics.id, {"dates": ["2026-07-21"], "reason": "Exams"})
    ]
    pipeline.sign_in(world["people"]["ai"])
    banned = pipeline.client.get(url(world)).json()["history"][-1]
    assert (banned["action"], banned["actor"]) == ("dates_banned", ref(logistics))


def test_the_history_outlives_a_deleted_event(pipeline, world):
    admin = pipeline.person("Admin")
    pipeline.sign_in(admin, super_admin=True)
    submit(pipeline, world["request_id"])
    for team in ("design", "logistics", "media"):
        finish(pipeline, world["request_id"], team)
    event_id = pipeline.client.post(url(world, "/publish")).json()["event_id"]
    pipeline.client.put(f"/events/{event_id}/status", json={"status": "draft"})
    assert pipeline.client.delete(f"/events/{event_id}").status_code == 200

    pipeline.session.expire_all()
    assert pipeline.session.get(EventRequests, world["request_id"]) is None
    rows = pipeline.session.scalars(
        select(PipelineHistory).where(PipelineHistory.request_id == world["request_id"]).order_by(PipelineHistory.at)
    ).all()
    assert rows[-1].action.value == "event_deleted"
    assert rows[-1].actor_id == admin.id
    assert rows[-1].request_title == "Intro to ML"
    assert rows[-1].details == {"event_id": event_id, "event_name": "Intro to ML"}


def test_an_edit_while_the_teams_work_on_it_is_flagged(pipeline, world):
    submit(pipeline, world["request_id"])
    admin = pipeline.person("Admin")
    pipeline.sign_in(admin, super_admin=True)
    pipeline.client.put(url(world, "/details"), json={"title": "Renamed by an admin"})
    pipeline.client.put(url(world, "/briefs/design"), json={"brief": {"idea": "Changed"}})
    history = pipeline.client.get(url(world)).json()["history"]
    assert [(h["action"], h["details"]) for h in history[-2:]] == [
        ("details_edited", {"after_submit": True}),
        ("brief_edited", {"team": "design", "after_submit": True}),
    ]
