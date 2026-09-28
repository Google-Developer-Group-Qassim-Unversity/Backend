"""The booking calendar: the lockout, and Logistics bans."""

from datetime import date, datetime

import pytest

from tests.pipeline_support import FROZEN_NOW


@pytest.fixture
def teams(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    pipeline.teams(design, logistics, media)
    return {"design": design, "logistics": logistics, "media": media}


def calendar(pipeline, start="2026-07-13", end="2026-07-25"):
    response = pipeline.client.get("/pipeline/calendar", params={"from": start, "to": end})
    assert response.status_code == 200, response.text
    return response.json()


def statuses(body):
    return {d["date"]: d["status"] for d in body["days"]}


def test_today_and_the_next_three_days_are_locked(pipeline, teams):
    pipeline.sign_in(pipeline.officer(teams["design"]))
    body = calendar(pipeline)

    # FROZEN_NOW is 2026-07-15 12:00 in Riyadh.
    assert body["today"] == "2026-07-15"
    assert body["first_bookable_date"] == "2026-07-19"
    days = statuses(body)
    assert [days[f"2026-07-{d}"] for d in range(15, 20)] == ["locked"] * 4 + ["open"]


@pytest.mark.parametrize(
    ("utc_now", "first_bookable"),
    [
        # 23:59:59 on the 15th in Riyadh is 20:59:59 UTC: still the 15th.
        (datetime(2026, 7, 15, 20, 59, 59), "2026-07-19"),
        # One second later it is midnight in Riyadh, and the window moves.
        (datetime(2026, 7, 15, 21, 0, 0), "2026-07-20"),
    ],
)
def test_the_lockout_moves_at_midnight_riyadh_time(pipeline, teams, utc_now, first_bookable):
    pipeline.freeze(utc_now)
    pipeline.sign_in(pipeline.officer(teams["design"]))
    assert calendar(pipeline)["first_bookable_date"] == first_bookable


def test_logistics_bans_and_unbans_days_with_a_reason(pipeline, teams):
    logistics = pipeline.officer(teams["logistics"])
    pipeline.sign_in(logistics)

    response = pipeline.client.put(
        "/pipeline/calendar/bans", json={"dates": ["2026-07-20", "2026-07-21"], "reason": "Midterms"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2

    pipeline.sign_in(pipeline.officer(teams["design"]))
    body = calendar(pipeline)
    banned = {d["date"]: d["reason"] for d in body["days"] if d["status"] == "banned"}
    assert banned == {"2026-07-20": "Midterms", "2026-07-21": "Midterms"}

    pipeline.sign_in(logistics)
    response = pipeline.client.request("DELETE", "/pipeline/calendar/bans", json={"dates": ["2026-07-20"]})
    assert response.json()["count"] == 1
    assert statuses(calendar(pipeline))["2026-07-20"] == "open"


def test_banning_again_updates_the_reason(pipeline, teams):
    pipeline.sign_in(pipeline.officer(teams["logistics"]))
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-20"], "reason": "Exams"})
    pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-20"], "reason": "Holiday"})
    reasons = {d["date"]: d["reason"] for d in calendar(pipeline)["days"]}
    assert reasons["2026-07-20"] == "Holiday"


def test_only_logistics_can_ban(pipeline, teams):
    pipeline.sign_in(pipeline.officer(teams["design"]))
    response = pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-20"]})
    assert response.status_code == 403


def test_a_super_admin_can_ban(pipeline, teams):
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    assert pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-20"]}).status_code == 200


def test_banning_needs_a_logistics_team(pipeline):
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)
    response = pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-20"]})
    assert response.status_code == 409
    assert response.json()["code"] == "team_not_set"


def test_days_in_the_past_cannot_be_banned(pipeline, teams):
    pipeline.sign_in(pipeline.officer(teams["logistics"]))
    response = pipeline.client.put("/pipeline/calendar/bans", json={"dates": ["2026-07-14"]})
    assert response.status_code == 422


def test_calendar_range_is_limited(pipeline, teams):
    pipeline.sign_in(pipeline.officer(teams["design"]))
    response = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-01", "to": "2026-12-31"})
    assert response.status_code == 422
    response = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-10", "to": "2026-07-01"})
    assert response.status_code == 422


def test_calendar_needs_pipeline_access(pipeline, teams):
    pipeline.sign_in(pipeline.person("Nobody"))
    response = pipeline.client.get("/pipeline/calendar", params={"from": "2026-07-13", "to": "2026-07-20"})
    assert response.status_code == 403


def test_frozen_now_is_what_the_suite_expects():
    assert FROZEN_NOW.date() == date(2026, 7, 15)
