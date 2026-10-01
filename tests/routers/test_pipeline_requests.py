"""Booking dates, the 24-hour hold, re-dating, and the event details."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import EventRequests, EventRequestStage


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    cyber = pipeline.department("Cyber")
    return {
        "design": design,
        "logistics": logistics,
        "media": media,
        "ai": ai,
        "cyber": cyber,
        "ai_leader": pipeline.officer(ai),
        "cyber_leader": pipeline.officer(cyber),
        "logistics_leader": pipeline.officer(logistics),
    }


def book(pipeline, department, start="2026-07-20", end="2026-07-21"):
    return pipeline.client.post(
        "/pipeline/requests", json={"department_id": department.id, "start_date": start, "end_date": end}
    )


def test_booking_creates_a_draft_holding_the_days_for_24_hours(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    response = book(pipeline, world["ai"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stage"] == "draft"
    assert (body["start_date"], body["end_date"]) == ("2026-07-20", "2026-07-21")
    assert body["hold_expires_at"].startswith("2026-07-16T09:00:00")
    assert body["can_edit"] is True

    days = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-19", "to": "2026-07-22"}).json()["days"]
    assert [d["status"] for d in days] == ["open", "held", "held", "open"]
    assert days[1]["requests"][0]["department"]["id"] == world["ai"].id


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        ("2026-07-18", "2026-07-18", "day_locked"),  # today + 3
        ("2026-07-20", "2026-07-24", "range_too_long"),  # 5 days
        ("2026-07-21", "2026-07-20", "bad_range"),
    ],
)
def test_booking_refuses_bad_ranges(pipeline, world, start, end, code):
    pipeline.sign_in(world["ai_leader"])
    response = book(pipeline, world["ai"], start, end)
    assert response.status_code in (409, 422)
    assert response.json()["code"] == code


def test_booking_a_banned_day_is_refused(pipeline, world):
    pipeline.sign_in(world["logistics_leader"])
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-21"], "reason": "Exams"})
    pipeline.sign_in(world["ai_leader"])
    response = book(pipeline, world["ai"])
    assert response.status_code == 409
    assert response.json()["code"] == "day_banned"


def test_a_taken_day_cannot_be_booked_but_a_super_admin_can_stack(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    assert book(pipeline, world["ai"]).status_code == 201

    pipeline.sign_in(world["cyber_leader"])
    response = book(pipeline, world["cyber"], "2026-07-21", "2026-07-22")
    assert response.status_code == 409
    assert response.json()["code"] == "day_taken"

    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert book(pipeline, world["cyber"], "2026-07-21", "2026-07-22").status_code == 201


def test_an_expired_hold_is_free_without_any_sweep(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    book(pipeline, world["ai"])

    pipeline.freeze(pipeline.now + timedelta(hours=24, seconds=1))
    pipeline.sign_in(world["cyber_leader"])
    assert book(pipeline, world["cyber"]).status_code == 201


def test_a_hold_one_second_before_expiry_still_blocks(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    book(pipeline, world["ai"])

    pipeline.freeze(pipeline.now + timedelta(hours=24) - timedelta(seconds=1))
    pipeline.sign_in(world["cyber_leader"])
    assert book(pipeline, world["cyber"]).status_code == 409


def test_one_live_hold_per_department(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    book(pipeline, world["ai"])
    response = book(pipeline, world["ai"], "2026-07-25", "2026-07-25")
    assert response.status_code == 409
    assert response.json()["code"] == "hold_exists"


def test_only_someone_acting_for_the_department_can_book(pipeline, world):
    pipeline.sign_in(world["cyber_leader"])
    assert book(pipeline, world["ai"]).status_code == 403


def test_a_ban_undates_every_request_covering_the_day(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]

    pipeline.sign_in(world["logistics_leader"])
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-21"]})

    pipeline.sign_in(world["ai_leader"])
    body = pipeline.client.get(f"/pipeline/requests/{request_id}").json()
    assert body["start_date"] is None
    assert body["hold_expires_at"] is None
    assert body["undated_reason"] == "day_banned"
    assert body["stage"] == "draft"


def test_redating_starts_a_new_hold(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]
    pipeline.client.put(f"/pipeline/requests/{request_id}/details", json={"title": "Intro to ML"})

    pipeline.freeze(pipeline.now + timedelta(hours=30))
    response = pipeline.client.put(
        f"/pipeline/requests/{request_id}/dates", json={"start_date": "2026-07-25", "end_date": "2026-07-26"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start_date"] == "2026-07-25"
    assert body["hold_expires_at"].startswith("2026-07-17T15:00:00")
    assert body["details"]["title"] == "Intro to ML"


def test_a_draft_still_holding_its_dates_cannot_be_redated(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]
    response = pipeline.client.put(
        f"/pipeline/requests/{request_id}/dates", json={"start_date": "2026-07-25", "end_date": "2026-07-26"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "still_held"


def test_details_save_partially_and_validate_day_modes(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]
    url = f"/pipeline/requests/{request_id}/details"

    response = pipeline.client.put(
        url,
        json={
            "title": "Intro to ML",
            "event_type": "workshop",
            "day_modes": {"2026-07-20": "on_site", "2026-07-21": "online"},
            "daily_start_time": "09:00",
            "daily_end_time": "12:00",
            "is_official": True,
            "partner_department_ids": [world["cyber"].id],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["details"]["day_modes"] == {"2026-07-20": "on_site", "2026-07-21": "online"}
    assert body["details"]["is_official"] is True
    assert [p["id"] for p in body["partners"]] == [world["cyber"].id]
    # 20 July 2026 is a Monday: a working day, and 09:00-12:00 is inside working hours.
    assert body["within_official_hours"] is True

    # Only the field sent changes.
    body = pipeline.client.put(url, json={"description": "Hands-on"}).json()
    assert body["details"]["title"] == "Intro to ML"
    assert body["details"]["description"] == "Hands-on"

    bad = pipeline.client.put(url, json={"day_modes": {"2026-07-25": "online"}})
    assert bad.status_code == 422
    assert bad.json()["code"] == "mode_outside_dates"

    assert pipeline.client.put(url, json={"partner_department_ids": [world["ai"].id]}).status_code == 422


def test_list_shows_only_my_departments_requests(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    book(pipeline, world["ai"])
    pipeline.sign_in(world["cyber_leader"])
    book(pipeline, world["cyber"], "2026-07-25", "2026-07-25")

    items = pipeline.client.get("/pipeline/requests").json()["items"]
    assert [i["department"]["id"] for i in items] == [world["cyber"].id]
    assert pipeline.client.get("/pipeline/requests", params={"department_id": world["ai"].id}).status_code == 403

    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert pipeline.client.get("/pipeline/requests").json()["total"] == 2


def test_another_department_cannot_see_the_request(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]
    pipeline.sign_in(world["cyber_leader"])
    assert pipeline.client.get(f"/pipeline/requests/{request_id}").status_code == 403


def test_cancelling_a_draft_frees_its_dates(pipeline, world):
    pipeline.sign_in(world["ai_leader"])
    request_id = book(pipeline, world["ai"]).json()["id"]
    assert pipeline.client.delete(f"/pipeline/requests/{request_id}").status_code == 200
    row = pipeline.session.scalar(select(EventRequests).where(EventRequests.id == request_id))
    pipeline.session.refresh(row)
    assert row.stage == EventRequestStage.CANCELLED

    pipeline.sign_in(world["cyber_leader"])
    assert book(pipeline, world["cyber"]).status_code == 201


def test_a_super_admin_skips_the_lockout_bans_and_the_one_hold_rule(pipeline, world):
    pipeline.sign_in(world["logistics_leader"])
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-17"]})
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert book(pipeline, world["ai"], "2026-07-16", "2026-07-17").status_code == 201
    assert book(pipeline, world["ai"], "2026-07-20", "2026-07-26").status_code == 201
