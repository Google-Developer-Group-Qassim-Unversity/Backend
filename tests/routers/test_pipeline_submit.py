"""The Design and Logistics briefs, and submitting a request to both."""

from datetime import timedelta

import pytest

from tests.pipeline_support import COMPLETE_DESIGN, book_complete, submit


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    pipeline.teams(design, logistics, media)
    ai = pipeline.department("AI")
    leader = pipeline.officer(ai)
    pipeline.sign_in(leader)
    return {"ai": ai, "leader": leader, "design": design, "logistics": logistics}


def test_a_complete_request_submits_to_design_and_logistics(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    before = pipeline.client.get(f"/pipeline/requests/{request_id}").json()
    assert before["missing"] == []

    response = submit(pipeline, request_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "in_review"
    assert body["hold_expires_at"] is None
    assert body["can_edit"] is False
    assert {(t["team"], t["status"]) for t in body["tasks"]} == {("design", "open"), ("logistics", "open")}


def test_dates_stay_taken_after_submit_with_no_hold(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    pipeline.freeze(pipeline.now + timedelta(days=2))

    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-20", "to": "2026-07-21"}).json()["days"]
    assert [d["status"] for d in days] == ["booked", "booked"]


def test_submit_lists_every_missing_field(pipeline, world):
    request_id = pipeline.client.post(
        "/pipeline/requests",
        json={"department_id": world["ai"].id, "start_date": "2026-07-20", "end_date": "2026-07-20"},
    ).json()["id"]
    pipeline.client.put(
        f"/pipeline/requests/{request_id}/details", json={"day_modes": {"2026-07-20": "on_site"}, "audience": "female"}
    )

    response = submit(pipeline, request_id)
    assert response.status_code == 422
    assert response.json()["code"] == "incomplete"
    missing = {".".join(item["loc"]) for item in response.json()["detail"]}
    assert {"details.title", "design.idea", "design.content", "logistics.venue", "logistics.buses_needed"} <= missing
    # No online day, so the Meet link and presenter email are not asked.
    assert "logistics.meet_link_by_logistics" not in missing
    assert "details.presenter_email" not in missing


def test_design_other_needs_its_text(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    pipeline.client.put(
        f"/pipeline/requests/{request_id}/briefs/design", json={"brief": {**COMPLETE_DESIGN, "design_type": "other"}}
    )
    assert "design.design_type_other" in pipeline.client.get(f"/pipeline/requests/{request_id}").json()["missing"]


def test_submit_is_refused_after_the_hold_expired(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    pipeline.freeze(pipeline.now + timedelta(hours=25))
    response = submit(pipeline, request_id)
    assert response.status_code == 409
    assert response.json()["code"] == "hold_expired"


def test_briefs_are_frozen_after_submit(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    response = pipeline.client.put(f"/pipeline/requests/{request_id}/briefs/design", json={"brief": {"idea": "x"}})
    assert response.status_code == 409
    response = pipeline.client.put(f"/pipeline/requests/{request_id}/details", json={"title": "x"})
    assert response.status_code == 409


def test_unknown_brief_fields_are_dropped(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    body = pipeline.client.put(
        f"/pipeline/requests/{request_id}/briefs/design", json={"brief": {**COMPLETE_DESIGN, "junk": 1}}
    ).json()
    design = next(t for t in body["tasks"] if t["team"] == "design")
    assert "junk" not in design["brief"]
    assert design["brief_version"] == 1


def test_media_has_no_brief(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    response = pipeline.client.put(f"/pipeline/requests/{request_id}/briefs/media", json={"brief": {}})
    assert response.status_code == 422


def test_a_draft_keeps_everything_typed_when_reopened(pipeline, world):
    request_id = pipeline.client.post(
        "/pipeline/requests",
        json={"department_id": world["ai"].id, "start_date": "2026-07-20", "end_date": "2026-07-20"},
    ).json()["id"]
    pipeline.client.put(f"/pipeline/requests/{request_id}/details", json={"title": "Intro to ML", "help_needed": "x"})
    pipeline.client.put(f"/pipeline/requests/{request_id}/briefs/design", json={"brief": {"idea": "A robot"}})
    pipeline.client.put(f"/pipeline/requests/{request_id}/briefs/logistics", json={"brief": {"notes": "Projector"}})

    # A fresh read, as when the team comes back to the draft later.
    body = pipeline.client.get(f"/pipeline/requests/{request_id}").json()
    assert body["details"]["title"] == "Intro to ML"
    assert body["details"]["help_needed"] == "x"
    briefs = {t["team"]: t["brief"] for t in body["tasks"]}
    assert briefs["design"] == {"idea": "A robot"}
    assert briefs["logistics"] == {"notes": "Projector"}


def test_a_half_typed_email_saves_but_blocks_submit(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    url = f"/pipeline/requests/{request_id}/details"

    response = pipeline.client.put(url, json={"presenter_email": "ali@", "title": "Kept"})
    assert response.status_code == 200, response.text
    assert response.json()["details"]["title"] == "Kept"
    assert "details.presenter_email" in response.json()["missing"]
    assert submit(pipeline, request_id).status_code == 422

    body = pipeline.client.put(url, json={"presenter_email": "ali@example.com"}).json()
    assert "details.presenter_email" not in body["missing"]


def test_a_blank_title_counts_as_missing(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    body = pipeline.client.put(f"/pipeline/requests/{request_id}/details", json={"title": "   "}).json()
    assert "details.title" in body["missing"]
