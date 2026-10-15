"""The team's Google Calendar: an entry from booking, following the request, off when it no longer holds dates.

A route queues the sync as a background task, which clears the entry's mark in
a session of its own. Under this suite every session shares one connection
inside one transaction, and the route's session, closed after the background
task, rolls its trailing savepoint back - taking that update with it (not the
Google calls, which the transport recorder keeps). So a test that needs the
mark cleared runs the sync again itself (``persist``). Production sessions
have their own connections and no such problem.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import reload_settings
from app.DB.schema import PipelineCalendarEntries
from app.services import google_calendar, pipeline_calendar
from app.services.pipeline_sweep import sweep_once
from tests.pipeline_support import MEET_LINK, book_complete, finish, submit

CALENDAR_ID = "team-calendar"
EVENTS = f"/calendar/v3/calendars/{CALENDAR_ID}/events"


@pytest.fixture
def team_calendar(monkeypatch):
    """The calendar is configured; Google itself is the transport recorder's canned answers."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", CALENDAR_ID)
    monkeypatch.setenv("GOOGLE_CALENDAR_REFRESH_TOKEN", "1//calendar-test")
    reload_settings()
    monkeypatch.setattr(google_calendar, "access_token", lambda: "ya29.test")


@pytest.fixture
def world(pipeline):
    for name in ("Design", "Logistics", "Media"):
        pipeline.department(name)
    ai = pipeline.department("AI")
    leader = pipeline.officer(ai)
    pipeline.sign_in(leader)
    return {"ai": ai, "leader": leader, "admin": pipeline.person("Admin")}


def entry_path(request_id: str) -> str:
    return f"{EVENTS}/{pipeline_calendar.event_id(request_id)}"


def writes(pipeline, request_id: str) -> list[dict]:
    return [c.json for c in pipeline.outbound.to(entry_path(request_id)) if c.method == "PUT"]


def deletes(pipeline, request_id: str) -> list:
    return [c for c in pipeline.outbound.to(entry_path(request_id)) if c.method == "DELETE"]


def entry(pipeline, request_id: str) -> PipelineCalendarEntries | None:
    pipeline.session.expire_all()
    return pipeline.session.scalar(
        select(PipelineCalendarEntries).where(PipelineCalendarEntries.request_id == request_id)
    )


def marked(pipeline, request_id: str) -> PipelineCalendarEntries:
    row = entry(pipeline, request_id)
    assert row is not None
    return row


def publish_ready(pipeline, world, request_id: str) -> int:
    pipeline.sign_in(world["admin"], super_admin=True)
    for team in ("design", "logistics", "media"):
        assert finish(pipeline, request_id, team).status_code == 200
    response = pipeline.client.post(f"/pipeline/requests/{request_id}/publish")
    assert response.status_code == 200, response.text
    return response.json()["event_id"]


def test_booking_puts_a_tentative_entry_on_the_calendar(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])

    first, *_, last = writes(pipeline, request_id)
    # Just booked: no times yet, so all day over the held dates.
    assert first["status"] == "tentative"
    assert first["summary"] == "(testing) [Held] بدون عنوان · Untitled · AI"
    assert first["start"] == {"date": "2026-07-20"} and first["end"] == {"date": "2026-07-22"}
    # Filled in: the daily times, each day, at the venue asked for.
    assert last["summary"] == "(testing) [Held] Intro to ML · AI"
    assert last["colorId"] == "8"
    assert last["start"] == {"dateTime": "2026-07-20T10:00:00", "timeZone": "Asia/Riyadh"}
    assert last["end"] == {"dateTime": "2026-07-20T12:00:00", "timeZone": "Asia/Riyadh"}
    assert last["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=2"]
    assert last["location"] == "التيك فالي (60)"
    assert "Hold expires" in last["description"]
    assert last["source"]["url"].endswith(f"/pipeline/requests/{request_id}")
    assert last["extendedProperties"] == {"private": {"request_id": request_id}}


def test_the_entry_follows_the_request_to_publish(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200
    submitted = writes(pipeline, request_id)[-1]
    assert submitted["status"] == "confirmed"
    assert submitted["summary"] == "(testing) [In review] Intro to ML · AI"
    assert "Design … · Logistics … · Media —" in submitted["description"]

    publish_ready(pipeline, world, request_id)
    published = writes(pipeline, request_id)[-1]
    assert published["summary"] == "(testing) Intro to ML · AI"
    assert published["colorId"] == "3"
    assert published["start"]["dateTime"] == "2026-07-20T10:00:00"
    assert published["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=2"]
    assert f"Meet: {MEET_LINK}" in published["description"]
    assert "Design ✓ · Logistics ✓ · Media ✓" in published["description"]
    assert "Poster: https://cdn.example.com/event-images/" in published["description"]


def test_editing_the_published_event_changes_the_entry(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    online = {"day_modes": {"2026-07-20": "online", "2026-07-21": "online"}}
    assert pipeline.client.put(f"/pipeline/requests/{request_id}/details", json=online).status_code == 200
    submit(pipeline, request_id)
    event_id = publish_ready(pipeline, world, request_id)
    before = len(writes(pipeline, request_id))

    url = MEET_LINK.replace("abc", "xyz")
    response = pipeline.client.put(f"/events/{event_id}/meeting-url", json={"meeting_url": url})
    assert response.status_code == 200, response.text
    assert len(writes(pipeline, request_id)) == before + 1
    assert f"Meet: {url}" in writes(pipeline, request_id)[-1]["description"]


def test_deleting_the_published_event_takes_the_entry_off(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    submit(pipeline, request_id)
    event_id = publish_ready(pipeline, world, request_id)
    pipeline.client.put(f"/events/{event_id}/status", json={"status": "draft"})
    assert pipeline.client.delete(f"/events/{event_id}").status_code == 200

    # The request went with its event; its row outlived it long enough for the entry to come off.
    assert len(deletes(pipeline, request_id)) == 1
    assert entry(pipeline, request_id) is None


def test_a_cancelled_draft_comes_off(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    assert pipeline.client.post(f"/pipeline/requests/{request_id}/cancel").status_code == 200
    assert len(deletes(pipeline, request_id)) == 1


def test_an_expired_hold_comes_off_with_the_sweep(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    pipeline.freeze(pipeline.now + timedelta(hours=25))

    result = sweep_once()
    assert result is not None and result.expired_holds == 1 and result.calendar_synced == 1
    assert len(deletes(pipeline, request_id)) == 1
    assert marked(pipeline, request_id).sync_pending == 0


def test_an_entry_google_never_saw_is_inserted_under_our_id(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    pipeline.outbound.stub(entry_path(request_id), status_code=404, json={"error": {"message": "Not Found"}})
    assert pipeline_calendar.sync_request(request_id) is True

    inserted = [c.json for c in pipeline.outbound.to(EVENTS) if c.method == "POST"][-1]
    assert inserted["id"] == pipeline_calendar.event_id(request_id)
    assert inserted["summary"] == "(testing) [Held] Intro to ML · AI"


def test_google_down_never_blocks_the_step_and_the_sweep_retries(pipeline, team_calendar, world):
    request_id = book_complete(pipeline, world["ai"])
    pipeline.outbound.stub(entry_path(request_id), status_code=503, json={"error": {"message": "Backend Error"}})
    assert submit(pipeline, request_id).status_code == 200
    assert pipeline_calendar.sync_request(request_id) is False
    row = marked(pipeline, request_id)
    assert row.sync_pending == 1 and "Backend Error" in (row.error or "")

    pipeline.outbound._stubs.pop(entry_path(request_id))
    result = sweep_once()
    assert result is not None and result.calendar_synced == 1
    row = marked(pipeline, request_id)
    assert row.sync_pending == 0 and row.error is None
    again = sweep_once()
    assert again is not None and again.calendar_synced == 0  # nothing left to retry


def test_nothing_is_sent_without_the_setting(pipeline, world):
    request_id = book_complete(pipeline, world["ai"])
    assert submit(pipeline, request_id).status_code == 200
    assert not [c for c in pipeline.outbound.calls if c.url.host == "www.googleapis.com"]
    assert pipeline_calendar.sync_request(request_id) is False
    # Still marked: setting the calendar later writes it.
    assert marked(pipeline, request_id).sync_pending == 1


def test_entry_ids_are_valid_and_differ_per_environment():
    request_id = "019a2b3c-4d5e-7f80-9a1b-2c3d4e5f6a7b"
    eid = pipeline_calendar.event_id(request_id)
    assert set(eid) <= pipeline_calendar.ID_ALPHABET and 5 <= len(eid) <= 1024
    assert eid == "gdgtesting019a2b3c4d5e7f809a1b2c3d4e5f6a7b"
