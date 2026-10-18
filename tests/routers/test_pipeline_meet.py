"""Logistics has a Google Meet link made on the club's calendar (app/services/google_meet.py)."""

import pytest

from app.config import reload_settings
from app.services import google_meet
from tests.outbound import MEET_CALENDAR_ID, MEET_EVENT_ID, MEET_URL
from tests.pipeline_support import book_complete, submit

CALENDAR_ID = MEET_CALENDAR_ID


@pytest.fixture
def world(pipeline):
    design, logistics, media = (pipeline.department(n) for n in ("Design", "Logistics", "Media"))
    ai = pipeline.department("AI")
    people = {"ai": pipeline.officer(ai), "design": pipeline.officer(design), "logistics": pipeline.officer(logistics)}
    pipeline.sign_in(people["ai"])
    request_id = book_complete(pipeline, ai)
    assert submit(pipeline, request_id).status_code == 200
    pipeline.sign_in(people["logistics"])
    return {"request_id": request_id, "people": people, "ai": ai}


@pytest.fixture
def organizer(monkeypatch):
    """The organizer account is set up; its access token starts cold for every test."""
    monkeypatch.setenv("GOOGLE_MEET_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_MEET_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_MEET_REFRESH_TOKEN", "1//refresh")
    monkeypatch.setenv("GOOGLE_MEET_CALENDAR_ID", CALENDAR_ID)
    reload_settings()
    google_meet.forget_token()
    yield
    google_meet.forget_token()


def url(world, tail=""):
    return f"/pipeline/requests/{world['request_id']}{tail}"


def logistics_task(body):
    return next(t for t in body["tasks"] if t["team"] == "logistics")


def save(pipeline, world, **changes):
    current = logistics_task(pipeline.client.get(url(world)).json())["deliverable"]
    return pipeline.client.put(url(world, "/deliverables/logistics"), json={"deliverable": {**current, **changes}})


def generate(pipeline, world):
    return pipeline.client.post(url(world, "/deliverables/logistics/meet"))


def google_calls(pipeline, method, host):
    return [c for c in pipeline.outbound.calls if c.method == method and c.url.host == host]


def test_nothing_is_offered_until_the_organizer_account_is_set_up(pipeline, world):
    assert pipeline.client.get(url(world)).json()["actions"]["can_generate_meet"] is False
    response = generate(pipeline, world)
    assert response.status_code == 422
    assert response.json()["code"] == "meet_not_configured"
    assert not google_calls(pipeline, "POST", "oauth2.googleapis.com")


def test_logistics_gets_a_link_for_the_confirmed_times(pipeline, world, organizer):
    body = pipeline.client.get(url(world)).json()
    assert body["actions"]["can_generate_meet"] is True
    assert logistics_task(body)["deliverable_missing"] == ["confirm.meet_link"]

    response = generate(pipeline, world)
    assert response.status_code == 200, response.text
    task = logistics_task(response.json())
    assert task["deliverable"]["meet_link"] == MEET_URL
    assert task["deliverable"]["meet_event_id"] == MEET_EVENT_ID
    assert task["deliverable_missing"] == []

    # One token, from the refresh token alone: naming a scope would get the whole refresh refused.
    token = pipeline.outbound.one("/token")
    assert b"grant_type=refresh_token" in token.content and b"scope" not in token.content

    created = pipeline.outbound.one(f"/calendar/v3/calendars/{CALENDAR_ID}/events")
    assert created.method == "POST"
    assert created.headers["Authorization"] == "Bearer ya29.test"
    assert created.params["conferenceDataVersion"] == "1" and created.params["sendUpdates"] == "none"
    event = created.json
    assert event["summary"] == "Intro to ML"
    assert event["description"] == "Hands-on machine learning"
    assert event["start"] == {"dateTime": "2026-07-20T10:00:00+03:00", "timeZone": "Asia/Riyadh"}
    assert event["end"] == {"dateTime": "2026-07-20T12:00:00+03:00", "timeZone": "Asia/Riyadh"}
    assert event["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=2"]  # two days, one link
    assert event["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert "attendees" not in event

    # The room is set up setting by setting, moderation first, each by its leaf field.
    settings = google_calls(pipeline, "PATCH", "meet.googleapis.com")
    assert [c.params["updateMask"] for c in settings] == [mask for mask, _ in google_meet.MEET_SETTINGS]
    assert settings[0].json == {"config": {"moderation": "ON"}}
    assert all(c.path == "/v2/spaces/jQCFfuBOdN5z" for c in settings)

    generated = [h for h in response.json()["history"] if h["action"] == "confirmation_edited"]
    assert generated[-1]["details"] == {"meet_link": MEET_URL}


def test_generating_again_replaces_the_link_and_drops_the_old_event(pipeline, world, organizer):
    assert generate(pipeline, world).status_code == 200
    assert generate(pipeline, world).status_code == 200
    deleted = google_calls(pipeline, "DELETE", "www.googleapis.com")
    assert [c.path for c in deleted] == [f"/calendar/v3/calendars/{CALENDAR_ID}/events/{MEET_EVENT_ID}"]
    assert len(pipeline.outbound.to("/token")) == 1  # the access token is kept for the hour


def test_a_link_needs_an_online_day_with_times(pipeline, world, organizer):
    assert save(pipeline, world, day_modes={"2026-07-20": "on_site", "2026-07-21": "on_site"}).status_code == 200
    response = generate(pipeline, world)
    assert response.status_code == 422 and response.json()["code"] == "meet_not_online"

    assert save(pipeline, world, day_modes={"2026-07-20": "online", "2026-07-21": "online"}, daily_end_time=None)
    response = generate(pipeline, world)
    assert response.status_code == 422 and response.json()["code"] == "meet_needs_times"
    assert not google_calls(pipeline, "POST", "www.googleapis.com")


def test_only_logistics_generates_a_link(pipeline, world, organizer):
    pipeline.sign_in(world["people"]["design"])
    assert generate(pipeline, world).status_code == 403
    pipeline.sign_in(world["people"]["ai"])
    assert generate(pipeline, world).status_code == 403
    assert not google_calls(pipeline, "POST", "www.googleapis.com")


def test_a_refused_room_setting_does_not_lose_the_link(pipeline, world, organizer):
    pipeline.outbound.stub(
        "/v2/spaces/jQCFfuBOdN5z", status_code=400, json={"error": {"code": 400, "message": "prerequisite is not met"}}
    )
    response = generate(pipeline, world)
    assert response.status_code == 200, response.text
    assert logistics_task(response.json())["deliverable"]["meet_link"] == MEET_URL


def test_google_refusing_the_event_is_a_clean_error(pipeline, world, organizer):
    pipeline.outbound.stub(
        f"/calendar/v3/calendars/{CALENDAR_ID}/events",
        status_code=403,
        json={"error": {"code": 403, "message": "Insufficient Permission", "status": "PERMISSION_DENIED"}},
    )
    response = generate(pipeline, world)
    assert response.status_code == 502
    assert response.json()["code"] == "meet_failed"
    assert "Insufficient Permission" in response.json()["detail"]
    assert logistics_task(pipeline.client.get(url(world)).json())["deliverable"]["meet_link"] is None


def test_a_revoked_refresh_token_is_a_clean_error(pipeline, world, organizer):
    pipeline.outbound.stub(
        "/token", status_code=400, json={"error": "invalid_grant", "error_description": "Bad Request"}
    )
    response = generate(pipeline, world)
    assert response.status_code == 502
    assert response.json()["code"] == "meet_failed"


def test_confirming_moves_the_calendar_event_to_the_confirmed_times(pipeline, world, organizer):
    assert generate(pipeline, world).status_code == 200
    assert save(pipeline, world, daily_start_time="11:00", change_reason="Presenter asked for 11").status_code == 200
    response = pipeline.client.post(url(world, "/tasks/logistics/complete"))
    assert response.status_code == 200, response.text
    assert logistics_task(response.json())["deliverable"]["meet_link"] == MEET_URL

    patched = google_calls(pipeline, "PATCH", "www.googleapis.com")
    assert [c.path for c in patched] == [f"/calendar/v3/calendars/{CALENDAR_ID}/events/{MEET_EVENT_ID}"]
    assert patched[0].json["start"] == {"dateTime": "2026-07-20T11:00:00+03:00", "timeZone": "Asia/Riyadh"}


def test_a_typed_link_touches_no_calendar(pipeline, world, organizer):
    assert save(pipeline, world, meet_link="https://meet.google.com/typed-by-hand").status_code == 200
    assert pipeline.client.post(url(world, "/tasks/logistics/complete")).status_code == 200
    assert not google_calls(pipeline, "POST", "oauth2.googleapis.com")
