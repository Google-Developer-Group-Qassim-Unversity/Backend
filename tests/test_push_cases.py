"""The member app 1.0.3's pushes (app/services/push/cases.py): who gets each, its key, its switch, its timing.

The clock is naive UTC 2026-07-20 09:00 (a Monday, 12:00 in Riyadh); event
times are Riyadh wall clock, so an event "at 16:00" starts at 13:00 UTC.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB import logs as log_queries
from app.DB.schema import (
    EventContentLinks,
    EventHistory,
    EventHistoryAction,
    Events,
    EventsContentLinkKind,
    EventsLocationType,
    EventsStatus,
    Forms,
    FormType,
    MembersLogs,
    PushKind,
    PushNotifications,
    PushStatus,
    SubmissionsSubmissionType,
)
from app.services import email_jobs, event_history
from app.services.pipeline_sweep import SweepResult, run_sweep
from app.services.push import cases, delivery, links, messages, outbox
from app.services.push.outbox import Push
from tests.factories import make_create_event_payload
from tests.factories import make_event as event_body
from tests.push_support import add_device, attendance_log, deliver, make_event, make_member, register, set_prefs
from tests.utils import assert_2xx

NAME = "\u2068Intro to ML\u2069"
EIGHT_AM_TOMORROW = datetime(2026, 7, 21, 5, 0)  # 08:00 Riyadh on 2026-07-21
ELEVEN_PM = datetime(2026, 7, 20, 20, 0)  # 23:00 Riyadh, quiet hours


def rows(session, kind: PushKind | None = None) -> list[PushNotifications]:
    session.expire_all()
    statement = select(PushNotifications).order_by(PushNotifications.created_at, PushNotifications.id)
    if kind is not None:
        statement = statement.where(PushNotifications.kind == kind)
    return list(session.scalars(statement).all())


def app_user(session, **switches):
    member = make_member(session)
    add_device(session, member)
    if switches:
        set_prefs(session, member, **switches)
    return member


def sweep(session, now) -> SweepResult:
    result = run_sweep(session, now)
    session.commit()
    return result


def change(session, event: Events, **values) -> int:
    """Change the event as a route would, record it in its history, then run the trigger the routes run."""
    before = cases.event_state(event)
    for key, value in values.items():
        setattr(event, key, value)
    session.flush()
    recorded = event_history.record(session, event, EventHistoryAction.EDITED, None, {"fields": sorted(values)})
    return cases.event_changed(session, event, before, recorded.id)


@pytest.fixture
def kicks(monkeypatch):
    """The deliveries a route schedules after its commit, recorded instead of run; the test then delivers.

    Here a route's session and the delivery's share one connection through
    savepoints, and a route that reads after its commit (``session.refresh``)
    holds a savepoint that closing its session rolls back, the delivery's
    writes with it. In production each session has its own connection.
    """
    calls: list[int] = []

    async def kick() -> None:
        calls.append(1)

    monkeypatch.setattr(delivery, "kick", kick)
    return calls


def attended(session, event: Events, *members, day: datetime | None = None):
    log = attendance_log(session, event)
    for member in members:
        session.add(MembersLogs(member_id=member.id, log_id=log.id, date=day or event.start_datetime))
    session.flush()
    return log


# ---------------------------------------------------------------- event_updated


def test_a_new_time_tells_the_accepted_registrants(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    accepted, pending, cancelled = app_user(db_session), app_user(db_session), app_user(db_session)
    app_user(db_session)  # not registered
    register(db_session, event, accepted)
    register(db_session, event, pending, accepted=False)
    register(db_session, event, cancelled).cancelled_at = datetime(2026, 7, 10)

    assert (
        change(
            db_session, event, start_datetime=datetime(2026, 7, 29, 17, 30), end_datetime=datetime(2026, 7, 29, 19, 30)
        )
        == 1
    )

    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert row.member_id == accepted.id
    recorded = db_session.scalars(select(EventHistory).where(EventHistory.event_id == event.id)).one()
    assert row.dedupe_key == f"event_updated:{event.id}:{accepted.id}:{recorded.id}"
    assert row.payload["en"] == {"title": f"Updated: {NAME}", "body": "New time: Jul 29 · 5:30 PM"}
    assert row.payload["ar"] == {"title": f"تحديث: {NAME}", "body": "الموعد الجديد: 29 يوليو · 5:30 م"}
    assert row.payload["url"] == f"gdgq://events/{event.id}"
    assert row.not_before == frozen.now
    assert row.expires_at == datetime(2026, 7, 29, 16, 30)  # the end, 19:30 Riyadh


def test_the_same_change_twice_is_one_push_and_another_change_is_another(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    before = cases.event_state(event)
    event.location = "Hall 61"
    recorded = event_history.record(db_session, event, EventHistoryAction.EDITED, None, {"location": ["", ""]})
    assert cases.event_changed(db_session, event, before, recorded.id) == 1
    assert cases.event_changed(db_session, event, before, recorded.id) == 0  # the same change run twice
    assert cases.event_changed(db_session, event, before, None) == 0  # a route that recorded no change
    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert row.status == PushStatus.PENDING
    assert row.dedupe_key == f"event_updated:{event.id}:{row.member_id}:{recorded.id}"


def test_a_double_submitted_status_change_pushes_once(admin_client, db_session, outbound, push_on, frozen, kicks):
    """The status route locks the event: the second request finds it closed already and records nothing."""
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    db_session.commit()
    for _ in range(2):
        assert_2xx(admin_client.put(f"/events/{event.id}/status", json={"status": "closed"}))
    assert len(rows(db_session, PushKind.EVENT_CANCELLED)) == 1


def test_the_status_route_locks_the_event_only_after_google_answers(
    admin_client, db_session, outbound, push_on, frozen, kicks, monkeypatch
):
    from app.DB import events as events_queries
    from app.routers import events as events_router

    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), form_type=FormType.GOOGLE)
    form = db_session.scalars(select(Forms).where(Forms.event_id == event.id)).one()
    form.google_form_id = "google-form-1"
    db_session.commit()
    calls: list[str] = []
    lock = events_queries.lock_event
    monkeypatch.setattr(events_router, "set_form_publish_state", lambda *a, **k: calls.append("google"))
    monkeypatch.setattr(events_queries, "lock_event", lambda *a: calls.append("lock") or lock(*a))
    assert_2xx(admin_client.put(f"/events/{event.id}/status", json={"status": "closed"}))
    assert calls == ["google", "lock"]


def test_the_edit_form_takes_no_event_lock(admin_client, db_session, seed_refs, outbound, push_on, kicks, monkeypatch):
    """Its log writes would take locks in the opposite order to attendance's (deadlock), so it does not lock."""
    from app.DB import events as events_queries

    created = admin_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs))
    assert_2xx(created)
    event_id = created.json()["id"]
    monkeypatch.setattr(events_queries, "lock_event", lambda *a: pytest.fail("the edit form locked the event"))
    actions = admin_client.get(f"/events/{event_id}/details").json()["actions"]
    assert_2xx(admin_client.put(f"/events/{event_id}", json={"event": event_body(name="Renamed"), "actions": actions}))


def test_waiting_updates_are_found_with_one_locking_read(db_session, push_on, frozen):
    """A plain read would answer from the transaction's first snapshot and miss what a concurrent edit queued."""
    from sqlalchemy import event as sa_event

    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    member = app_user(db_session)
    register(db_session, event, member)
    assert change(db_session, event, location="Hall 61") == 1
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind().engine
    sa_event.listen(engine, "before_cursor_execute", record)
    try:
        found = cases._waiting(db_session, PushKind.EVENT_UPDATED, event.id, [member.id], unsent_only=True)
    finally:
        sa_event.remove(engine, "before_cursor_execute", record)
    assert [row.member_id for row in found[member.id]] == [member.id]
    reads = [s for s in statements if "push_notifications" in s]
    assert len(reads) == 1 and "FOR UPDATE SKIP LOCKED" in reads[0]


def test_a_double_submitted_edit_whose_twin_went_out_is_skipped(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    stale = cases.event_state(event)
    assert change(db_session, event, location="Hall 61") == 1
    db_session.commit()
    assert deliver().sent == 1
    # The second request read the event before the first committed, and saves the same change.
    recorded = event_history.record(db_session, event, EventHistoryAction.EDITED, None, {"location": []})
    assert cases.event_changed(db_session, event, stale, recorded.id) == 1
    db_session.commit()
    deliver()
    first, second = rows(db_session, PushKind.EVENT_UPDATED)
    assert first.status == PushStatus.SENT
    assert (second.status, second.last_error) == (PushStatus.SKIPPED, "already_told")
    assert len(apns_mock.requests) == 1


def test_a_waiting_update_is_said_as_the_event_is_at_delivery(db_session, push_on, apns_mock, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    event.location = "Hall 63"  # changed again by something that queues no push
    db_session.commit()
    frozen.set(EIGHT_AM_TOMORROW)
    assert deliver().sent == 1
    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert row.payload["en"]["body"] == "New place: \u2068Hall 63\u2069"
    assert apns_mock.payloads()[0]["aps"]["alert"]["body"] == "المكان الجديد: \u2068Hall 63\u2069"  # an Arabic phone


def test_a_waiting_update_whose_change_was_undone_is_skipped(db_session, push_on, apns_mock, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    event.location = "Hall 60"
    db_session.commit()
    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "unchanged")
    assert apns_mock.requests == []


def test_folding_uses_the_event_as_it_is_not_the_waiting_rows_values(db_session, push_on, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    event.location = "Hall 63"  # the waiting row still says Hall 61
    db_session.flush()
    assert change(db_session, event, start_datetime=datetime(2026, 7, 28, 17, 0)) == 1
    _, merged = rows(db_session, PushKind.EVENT_UPDATED)
    assert merged.payload["en"]["body"] == "New time: Jul 28 · 5:00 PM · New place: \u2068Hall 63\u2069"


def test_folding_leaves_other_kinds_and_finished_rows_alone(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    member = app_user(db_session)
    register(db_session, event, member)
    assert change(db_session, event, location="Hall 61") == 1
    first = rows(db_session, PushKind.EVENT_UPDATED)[0]
    first.status, first.sent_at = PushStatus.SENT, frozen.now
    _queue(db_session, PushKind.EVENT_LIVE, member, f"event_live:{event.id}:x:{member.id}", event_id=event.id)
    db_session.flush()
    assert change(db_session, event, location="Hall 62") == 1
    assert rows(db_session, PushKind.EVENT_UPDATED)[0].status == PushStatus.SENT
    assert rows(db_session, PushKind.EVENT_LIVE)[0].status == PushStatus.PENDING


def test_changing_back_to_an_earlier_value_is_told_again(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    for hour in (18, 16, 18):
        frozen.advance(minutes=10)
        assert change(db_session, event, start_datetime=datetime(2026, 7, 28, hour, 0)) == 1
        db_session.commit()
        assert deliver().sent == 1
    sent = rows(db_session, PushKind.EVENT_UPDATED)
    assert [row.payload["en"]["body"] for row in sent] == [
        "New time: Jul 28 · 6:00 PM",
        "New time: Jul 28 · 4:00 PM",
        "New time: Jul 28 · 6:00 PM",
    ]
    assert len({row.dedupe_key for row in sent}) == 3


def test_a_waiting_update_is_folded_into_the_next_one(db_session, push_on, apns_mock, frozen):
    """23:00 the time and place move; 23:05 a typo in the place is fixed. 08:00: one push says both."""
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    moved = {"start_datetime": datetime(2026, 7, 29, 17, 0), "end_datetime": datetime(2026, 7, 29, 19, 0)}
    assert change(db_session, event, **moved, location="Hal 61") == 1
    frozen.advance(minutes=5)
    assert change(db_session, event, location="Hall 61") == 1
    db_session.commit()

    first, second = rows(db_session, PushKind.EVENT_UPDATED)
    assert (first.status, first.last_error) == (PushStatus.SKIPPED, "superseded")
    assert second.payload["cause"]["from"] == {
        "start_datetime": "2026-07-28T16:00:00",
        "end_datetime": "2026-07-28T18:00:00",
        "location": "Hall 60",
    }
    assert second.payload["en"]["body"] == "New time: Jul 29 · 5:00 PM · New place: \u2068Hall 61\u2069"
    assert second.not_before == EIGHT_AM_TOMORROW

    frozen.set(EIGHT_AM_TOMORROW)
    assert deliver().sent == 1
    assert rows(db_session, PushKind.EVENT_UPDATED)[1].status == PushStatus.SENT


def test_a_waiting_update_undone_before_it_goes_sends_nothing(db_session, push_on, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    frozen.advance(minutes=5)
    assert change(db_session, event, location="Hall 60") == 0
    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "superseded")


def test_an_update_already_out_for_delivery_is_not_folded(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location="Hall 60")
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    claimed = rows(db_session, PushKind.EVENT_UPDATED)[0]
    claimed.attempts, claimed.not_before = 1, frozen.now + timedelta(minutes=5)  # leased by a worker
    db_session.flush()
    frozen.advance(minutes=1)
    assert change(db_session, event, start_datetime=datetime(2026, 7, 28, 17, 0)) == 1
    first, second = rows(db_session, PushKind.EVENT_UPDATED)
    assert first.status == PushStatus.PENDING
    assert second.payload["cause"]["changed"] == {"start_datetime": "2026-07-28T17:00:00"}


def test_a_new_place_or_link_is_what_the_text_says(db_session, push_on, frozen):
    event = make_event(
        db_session, start=datetime(2026, 7, 28, 16, 0), location_type=EventsLocationType.ONLINE, location="Online"
    )
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, meeting_url="https://meet.google.com/abc-defg-hij") == 1
    assert (
        change(db_session, event, location_type=EventsLocationType.ON_SITE, location="Hall 60", meeting_url=None) == 1
    )
    link, place = rows(db_session, PushKind.EVENT_UPDATED)
    # The link one had not gone out yet: the second says what is left of both (the link went again).
    assert (link.status, link.last_error) == (PushStatus.SKIPPED, "superseded")
    assert link.payload["en"]["body"] == "New link to join"
    assert link.payload["ar"]["body"] == "رابط جديد للانضمام"
    assert place.payload["en"]["body"] == "New place: \u2068Hall 60\u2069"
    assert place.payload["ar"]["body"] == "المكان الجديد: \u2068Hall 60\u2069"


def test_no_update_push_for_a_draft_a_finished_event_or_a_removed_link(db_session, push_on, frozen):
    draft = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=EventsStatus.DRAFT)
    over = make_event(db_session, start=datetime(2026, 7, 10, 16, 0))
    online = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), location_type=EventsLocationType.ONLINE)
    online.meeting_url = "https://meet.google.com/abc-defg-hij"
    for event in (draft, over, online):
        register(db_session, event, app_user(db_session))
    assert change(db_session, draft, location="Elsewhere") == 0
    assert change(db_session, over, location="Elsewhere") == 0
    assert change(db_session, online, meeting_url=None) == 0
    assert change(db_session, online, name="A new name", description="New text") == 0
    assert rows(db_session) == []


def test_an_update_at_night_waits_for_8am_and_a_later_change_replaces_it(db_session, push_on, apns_mock, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, location="Hall 61") == 1
    assert rows(db_session, PushKind.EVENT_UPDATED)[0].not_before == EIGHT_AM_TOMORROW
    assert change(db_session, event, location="Hall 62") == 1
    db_session.commit()

    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    first, second = rows(db_session, PushKind.EVENT_UPDATED)
    assert (first.status, first.last_error) == (PushStatus.SKIPPED, "superseded")
    assert second.status == PushStatus.SENT
    assert second.payload["en"]["body"] == "New place: \u2068Hall 62\u2069"
    assert len(apns_mock.requests) == 1


def test_the_meeting_url_route_pushes_and_delivers(
    admin_client, db_session, outbound, push_on, apns_mock, frozen, kicks
):
    event = make_event(
        db_session, start=datetime(2026, 7, 28, 16, 0), location_type=EventsLocationType.ONLINE, location="Online"
    )
    member = app_user(db_session)
    register(db_session, event, member)
    db_session.commit()
    response = admin_client.put(f"/events/{event.id}/meeting-url", json={"meeting_url": "https://meet.google.com/abc"})
    assert_2xx(response)
    assert kicks == [1]
    assert deliver().sent == 1
    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert row.member_id == member.id
    assert row.status == PushStatus.SENT and len(apns_mock.requests) == 1


def test_the_edit_form_pushes_a_new_time(admin_client, db_session, seed_refs, outbound, push_on, frozen, kicks):
    details = {
        "name": "Workshop",
        "location_type": "on-site",
        "location": "Hall 60",
        "start_datetime": "2026-07-28T16:00:00",
        "end_datetime": "2026-07-28T18:00:00",
        "status": "open",
    }
    payload = make_create_event_payload(seed_refs=seed_refs, event=event_body(**details), form_type="registration")
    created = admin_client.post("/events", json=payload)
    assert_2xx(created)
    event = db_session.get(Events, created.json()["id"])
    member = app_user(db_session)
    register(db_session, event, member)
    db_session.commit()

    actions = admin_client.get(f"/events/{event.id}/details").json()["actions"]
    moved = event_body(**{**details, "start_datetime": "2026-07-28T17:00:00", "end_datetime": "2026-07-28T19:00:00"})
    assert_2xx(admin_client.put(f"/events/{event.id}", json={"event": moved, "actions": actions}))

    [row] = rows(db_session, PushKind.EVENT_UPDATED)
    assert row.member_id == member.id
    assert row.payload["en"]["body"] == "New time: Jul 28 · 5:00 PM"
    assert kicks == [1]


# ---------------------------------------------------------------- event_cancelled


def test_closing_an_event_before_it_starts_tells_its_registrants_even_at_night(
    admin_client, db_session, outbound, push_on, apns_mock, frozen, kicks
):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    accepted, pending = app_user(db_session), app_user(db_session)
    register(db_session, event, accepted)
    register(db_session, event, pending, accepted=False)
    db_session.commit()

    assert_2xx(admin_client.put(f"/events/{event.id}/status", json={"status": "closed"}))
    assert kicks == [1]
    assert rows(db_session, PushKind.EVENT_CANCELLED)[0].not_before == ELEVEN_PM  # not held until 08:00
    deliver()

    [row] = rows(db_session, PushKind.EVENT_CANCELLED)
    recorded = db_session.scalars(select(EventHistory).where(EventHistory.event_id == event.id)).one()
    assert (row.member_id, row.dedupe_key) == (accepted.id, f"event_cancelled:{event.id}:{accepted.id}:{recorded.id}")
    assert row.payload["en"] == {"title": f"Cancelled: {NAME}", "body": "Jul 28 · 4:00 PM · It will not take place"}
    assert row.payload["ar"] == {"title": f"أُلغيت: {NAME}", "body": "28 يوليو · 4:00 م · لن تُقام في موعدها"}
    assert row.status == PushStatus.SENT
    assert rows(db_session, PushKind.EVENT_UPDATED) == []


@pytest.mark.parametrize(
    "before,after", [(EventsStatus.OPEN, EventsStatus.DRAFT), (EventsStatus.ACTIVE, EventsStatus.CLOSED)]
)
def test_back_to_draft_or_closing_an_active_event_is_a_cancellation(db_session, push_on, frozen, before, after):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=before)
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, status=after) == 1
    assert change(db_session, event, status=after) == 0  # one per member, ever


def test_closing_a_started_or_never_public_event_is_not_a_cancellation(db_session, push_on, frozen):
    started = make_event(db_session, start=datetime(2026, 7, 20, 11, 0), end=datetime(2026, 7, 20, 13, 0))
    draft = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=EventsStatus.DRAFT)
    for event in (started, draft):
        register(db_session, event, app_user(db_session))
        assert change(db_session, event, status=EventsStatus.CLOSED) == 0
    assert rows(db_session) == []


def test_a_cancellation_undone_before_delivery_is_skipped(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, status=EventsStatus.CLOSED) == 1
    event.status = EventsStatus.OPEN
    db_session.commit()
    deliver()
    [row] = rows(db_session, PushKind.EVENT_CANCELLED)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "event_reopened")
    assert apns_mock.requests == []

    # A real cancellation later still goes out: each cancellation has its own key.
    frozen.advance(hours=2)
    assert change(db_session, event, status=EventsStatus.CLOSED) == 1
    db_session.commit()
    assert deliver().sent == 1
    assert [row.status for row in rows(db_session, PushKind.EVENT_CANCELLED)] == [PushStatus.SKIPPED, PushStatus.SENT]


def test_a_cancellation_run_again_keeps_its_row(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    before = cases.event_state(event)
    event.status = EventsStatus.CLOSED
    recorded = event_history.record(db_session, event, EventHistoryAction.STATUS_CHANGED, None, {"status": []})
    assert cases.event_changed(db_session, event, before, recorded.id) == 1
    assert cases.event_changed(db_session, event, before, recorded.id) == 0
    [row] = rows(db_session, PushKind.EVENT_CANCELLED)
    assert row.status == PushStatus.PENDING


def test_a_cancellation_waiting_on_a_retry_is_superseded_by_the_next_one(db_session, push_on, apns_mock, frozen):
    """Cancel (Apple answers 5xx, the row waits to retry), reopen, cancel again: one push, not two."""
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0))
    register(db_session, event, app_user(db_session))
    assert change(db_session, event, status=EventsStatus.CLOSED) == 1
    first = rows(db_session, PushKind.EVENT_CANCELLED)[0]
    first.attempts, first.not_before = 1, frozen.now + timedelta(minutes=2)
    frozen.advance(seconds=30)
    change(db_session, event, status=EventsStatus.OPEN)
    frozen.advance(seconds=30)
    assert change(db_session, event, status=EventsStatus.CLOSED) == 1
    db_session.commit()
    frozen.advance(minutes=5)
    assert deliver().sent == 1
    first, second = rows(db_session, PushKind.EVENT_CANCELLED)
    assert (first.status, first.last_error) == (PushStatus.SKIPPED, "superseded")
    assert second.status == PushStatus.SENT


def test_an_older_cancellation_retried_after_a_newer_one_went_out_is_skipped(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=EventsStatus.CLOSED)
    member = app_user(db_session)
    cause = {"submission_id": register(db_session, event, member).id, "status": "closed"}
    for key in ("older", "newer"):
        _queue(db_session, PushKind.EVENT_CANCELLED, member, f"event_cancelled:{key}", cause=cause, event_id=event.id)
    older, newer = rows(db_session, PushKind.EVENT_CANCELLED)
    newer.status, newer.sent_at = PushStatus.SENT, frozen.now
    db_session.commit()
    deliver()
    assert (rows(db_session, PushKind.EVENT_CANCELLED)[0].last_error) == "superseded"
    assert apns_mock.requests == []


# ---------------------------------------------------------------- event_live


def test_an_online_day_going_live_tells_registrants_not_checked_in(db_session, push_on, frozen):
    event = make_event(
        db_session,
        start=datetime(2026, 7, 20, 12, 0),
        end=datetime(2026, 7, 21, 14, 0),
        location_type=EventsLocationType.ONLINE,
        location="Online",
    )
    waiting, checked_in, pending = app_user(db_session), app_user(db_session), app_user(db_session)
    for member in (waiting, checked_in):
        register(db_session, event, member)
    register(db_session, event, pending, accepted=False)
    attended(db_session, event, checked_in, day=datetime(2026, 7, 20, 11, 58))
    db_session.commit()

    assert sweep(db_session, frozen.now).push_event_live == 1
    [row] = rows(db_session, PushKind.EVENT_LIVE)
    assert (row.member_id, row.dedupe_key) == (waiting.id, f"event_live:{event.id}:2026-07-20:{waiting.id}")
    assert row.payload["en"] == {"title": f"Live now: {NAME}", "body": "Day 1 of 2 · Tap to join"}
    assert row.payload["ar"] == {"title": f"بدأت الآن: {NAME}", "body": "اليوم 1 من 2 · اضغط للانضمام"}
    assert row.expires_at == datetime(2026, 7, 20, 11, 0)  # that day's end, 14:00 Riyadh
    assert sweep(db_session, frozen.now + timedelta(minutes=5)).push_event_live == 0


def test_live_is_for_online_events_and_only_just_after_the_start(db_session, push_on):
    on_site = make_event(db_session, start=datetime(2026, 7, 20, 12, 0))
    online = make_event(db_session, start=datetime(2026, 7, 20, 12, 0), location_type=EventsLocationType.ONLINE)
    for event in (on_site, online):
        register(db_session, event, app_user(db_session))
    db_session.commit()
    assert sweep(db_session, datetime(2026, 7, 20, 8, 59)).push_event_live == 0  # 11:59 Riyadh: not yet
    assert sweep(db_session, datetime(2026, 7, 20, 9, 15)).push_event_live == 0  # 12:15: too late
    assert sweep(db_session, datetime(2026, 7, 20, 9, 10)).push_event_live == 1
    [row] = rows(db_session, PushKind.EVENT_LIVE)
    assert row.event_id == online.id


def test_live_is_not_held_for_quiet_hours(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 20, 23, 0), location_type=EventsLocationType.ONLINE)
    register(db_session, event, app_user(db_session))
    db_session.commit()
    sweep(db_session, ELEVEN_PM)
    [row] = rows(db_session, PushKind.EVENT_LIVE)
    assert row.not_before == ELEVEN_PM


def test_checking_in_before_delivery_skips_the_live_push(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 20, 12, 0), location_type=EventsLocationType.ONLINE)
    member = app_user(db_session)
    register(db_session, event, member)
    db_session.commit()
    sweep(db_session, frozen.now)
    attended(db_session, event, member, day=datetime(2026, 7, 20, 12, 1))
    db_session.commit()
    deliver()
    [row] = rows(db_session, PushKind.EVENT_LIVE)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "checked_in")


# ---------------------------------------------------------------- registration_closing


def test_a_day_before_an_open_event_members_who_have_not_registered_hear_of_it(db_session, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 21, 12, 0), location="Hall 60")
    never_saved = app_user(db_session)  # no saved switches: club news is on
    registered, cancelled, partial = app_user(db_session), app_user(db_session), app_user(db_session)
    app_user(db_session, news=False)
    make_member(db_session)  # no app
    register(db_session, event, registered, accepted=False)
    register(db_session, event, cancelled).cancelled_at = datetime(2026, 7, 10)
    register(db_session, event, partial).submission_type = SubmissionsSubmissionType.PARTIAL
    db_session.commit()

    # 11:59 Riyadh: 24 hours and a minute before the start.
    assert sweep(db_session, datetime(2026, 7, 20, 8, 59)).push_registration_closing == 0
    assert sweep(db_session, frozen.now).push_registration_closing == 1
    [row] = rows(db_session, PushKind.REGISTRATION_CLOSING)
    assert (row.member_id, row.dedupe_key) == (never_saved.id, f"registration_closing:{event.id}:{never_saved.id}")
    assert row.payload["en"] == {
        "title": f"Last chance to register: {NAME}",
        "body": "Starts Jul 21 · 12:00 PM · \u2068Hall 60\u2069",
    }
    assert row.payload["ar"] == {
        "title": f"آخر فرصة للتسجيل: {NAME}",
        "body": "تبدأ 21 يوليو · 12:00 م · \u2068Hall 60\u2069",
    }
    assert row.expires_at == datetime(2026, 7, 21, 9, 0)
    assert sweep(db_session, frozen.now + timedelta(hours=1)).push_registration_closing == 0


def test_no_closing_push_for_late_opened_unregistrable_or_long_missed_events(db_session, push_on):
    app_user(db_session)
    now = datetime(2026, 7, 20, 10, 0)  # 13:00 Riyadh
    # Opened after its 24-hour mark (12:30 Riyadh): new_event just announced it.
    make_event(db_session, start=datetime(2026, 7, 21, 12, 30), created_at=datetime(2026, 7, 20, 9, 45))
    make_event(db_session, start=datetime(2026, 7, 21, 12, 30), form_type=FormType.NONE)
    make_event(db_session, start=datetime(2026, 7, 21, 12, 30), location_type=EventsLocationType.HIDDEN)
    make_event(db_session, start=datetime(2026, 7, 21, 12, 30), status=EventsStatus.ACTIVE)
    # Its mark was 6.5 hours ago (a deploy, an outage): too late to say "last chance".
    make_event(db_session, start=datetime(2026, 7, 21, 6, 30))
    db_session.commit()
    assert sweep(db_session, now).push_registration_closing == 0


def test_a_closing_push_at_night_waits_for_8am(db_session, push_on):
    make_event(db_session, start=datetime(2026, 7, 22, 1, 0))  # its mark: 01:00 Riyadh on the 21st
    app_user(db_session)
    db_session.commit()
    sweep(db_session, datetime(2026, 7, 20, 22, 0))
    [row] = rows(db_session, PushKind.REGISTRATION_CLOSING)
    assert row.not_before == EIGHT_AM_TOMORROW


def test_registering_before_delivery_skips_the_closing_push(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 21, 12, 0))
    member = app_user(db_session)
    db_session.commit()
    sweep(db_session, frozen.now)
    register(db_session, event, member, accepted=False)
    db_session.commit()
    deliver()
    [row] = rows(db_session, PushKind.REGISTRATION_CLOSING)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "registered")


# ---------------------------------------------------------------- certificate


def send_certificates(session, event: Events, sent_by) -> None:
    """The certificate job, as ``POST /emails/{event_id}`` runs it after its response."""
    attendance = log_queries.get_event_attendance(session, event.id, "exclusive_all")
    email_jobs.send_certificates_by_event_id(event, attendance, "18 July 2026", sent_by.id, event.id)


def test_each_certificate_sent_pushes_to_its_member_once(db_session, outbound, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    member, muted = app_user(db_session), app_user(db_session, points=False)
    attended(db_session, event, member, muted, day=datetime(2026, 7, 18, 16, 30))
    db_session.commit()

    send_certificates(db_session, event, member)
    assert len(outbound.calls) >= 2  # both members got their e-mail
    deliver()
    [row] = rows(db_session, PushKind.CERTIFICATE)
    assert (row.member_id, row.dedupe_key) == (member.id, f"certificate:{event.id}:{member.id}")
    assert row.payload["url"] == "gdgq://profile/certificates"
    assert row.payload["en"] == {"title": "Your certificate is ready", "body": f"{NAME} · Sent to your email"}
    assert row.payload["ar"] == {"title": "شهادتك جاهزة", "body": f"{NAME} · أُرسلت إلى بريدك"}
    assert row.status == PushStatus.SENT

    send_certificates(db_session, event, member)
    assert len(rows(db_session, PushKind.CERTIFICATE)) == 1


@pytest.mark.parametrize("path", ["/emails/{event}", "/emails/manual-certificate"])
def test_the_certificate_routes_deliver_after_their_job(admin_client, db_session, outbound, push_on, kicks, path):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    db_session.commit()
    body = None
    if path == "/emails/manual-certificate":
        body = {"event_id": event.id, "members": [], "language": "ar"}
    response = admin_client.post(path.format(event=event.id), json=body)
    assert_2xx(response)
    assert kicks == [1]


def test_a_certificate_for_no_event_or_no_member_pushes_nothing(db_session, push_on, frozen):
    member, event = app_user(db_session), make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    assert cases.certificate_sent(db_session, None, member.id) == 0
    assert cases.certificate_sent(db_session, event.id, None) == 0


def test_a_certificate_at_night_waits_and_is_skipped_without_its_email(db_session, push_on, apns_mock, frozen):
    frozen.set(ELEVEN_PM)
    member, event = app_user(db_session), make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    assert cases.certificate_sent(db_session, event.id, member.id) == 1
    assert rows(db_session, PushKind.CERTIFICATE)[0].not_before == EIGHT_AM_TOMORROW
    db_session.commit()
    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    [row] = rows(db_session, PushKind.CERTIFICATE)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "certificate_removed")


# ---------------------------------------------------------------- event_content


def add_link(client, event_id, **body):
    response = client.post(f"/events/{event_id}/content-links", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_published_slides_tell_the_members_who_attended(
    admin_client, db_session, outbound, push_on, apns_mock, frozen, kicks
):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    came = app_user(db_session)
    app_user(db_session)  # did not attend
    attended(db_session, event, came)
    db_session.commit()

    link_id = add_link(admin_client, event.id, label="Slides", url="https://example.com/slides.pdf", kind="slides")
    assert kicks == [1]
    deliver()
    [row] = rows(db_session, PushKind.EVENT_CONTENT)
    assert (row.member_id, row.dedupe_key) == (came.id, f"event_content:{event.id}:{link_id}:{came.id}")
    assert row.payload["en"] == {"title": f"Slides added: {NAME}", "body": "\u2068Slides\u2069"}
    assert row.payload["ar"] == {"title": f"أُضيفت الشرائح: {NAME}", "body": "\u2068Slides\u2069"}
    assert row.status == PushStatus.SENT


def test_a_draft_link_pushes_once_it_is_published_and_other_content_never(
    admin_client, db_session, outbound, push_on, frozen, kicks
):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attended(db_session, event, app_user(db_session))
    db_session.commit()

    add_link(admin_client, event.id, label="Survey", url="https://example.com/survey", kind="other")
    draft = add_link(
        admin_client, event.id, label="Recording", url="https://example.com/video", kind="recording", published=False
    )
    assert rows(db_session, PushKind.EVENT_CONTENT) == []

    for published in (True, False, True):
        assert_2xx(admin_client.put(f"/events/{event.id}/content-links/{draft}", json={"published": published}))
    [row] = rows(db_session, PushKind.EVENT_CONTENT)
    assert row.payload["en"]["title"] == f"Recording added: {NAME}"
    assert kicks == [1]


@pytest.fixture
def attendable(monkeypatch):
    """Any log of the event is its attendance log (production hardcodes action ids; see test_attendance.py)."""
    from app.DB.schema import Logs

    monkeypatch.setattr(
        log_queries,
        "get_attendable_logs",
        lambda session, event_id: session.scalar(select(Logs).where(Logs.event_id == event_id).order_by(Logs.id)),
    )


def test_attendance_recorded_after_the_slides_tells_that_member(
    admin_client, db_session, attendable, outbound, push_on, frozen, kicks
):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attendance_log(db_session, event)
    late = app_user(db_session)
    recent = EventContentLinks(
        event_id=event.id,
        label="Slides",
        url="https://example.com/s.pdf",
        kind=EventsContentLinkKind.SLIDES,
        published=1,
        published_at=frozen.now - timedelta(days=1),
    )
    old = EventContentLinks(
        event_id=event.id,
        label="Old recording",
        url="https://example.com/r",
        kind=EventsContentLinkKind.RECORDING,
        published=1,
        published_at=frozen.now - timedelta(days=8),
    )
    draft = EventContentLinks(
        event_id=event.id, label="Draft", url="https://example.com/d", kind=EventsContentLinkKind.SLIDES, published=0
    )
    db_session.add_all([recent, old, draft])
    db_session.commit()

    response = admin_client.post(f"/attendance/{event.id}/manual", json={"member_ids": [late.id], "day": 1})
    assert_2xx(response)
    [row] = rows(db_session, PushKind.EVENT_CONTENT)
    assert (row.member_id, row.dedupe_key) == (late.id, f"event_content:{event.id}:{recent.id}:{late.id}")
    # Publishing more later reaches them through content_added; this link is not told twice.
    assert cases.content_added(db_session, recent) == 0
    assert kicks


def test_a_draft_published_today_counts_from_today_for_late_attendance(
    admin_client, db_session, attendable, outbound, push_on, frozen, kicks
):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attendance_log(db_session, event)
    late = app_user(db_session)
    db_session.commit()
    draft = add_link(
        admin_client, event.id, label="Recording", url="https://example.com/v", kind="recording", published=False
    )
    link = db_session.get(EventContentLinks, draft)
    assert link is not None and link.published_at is None
    link.created_at = frozen.now - timedelta(days=20)  # drafted long ago
    db_session.commit()
    assert_2xx(admin_client.put(f"/events/{event.id}/content-links/{draft}", json={"published": True}))
    db_session.expire_all()
    assert db_session.get(EventContentLinks, draft).published_at == frozen.now

    assert_2xx(admin_client.post(f"/attendance/{event.id}/manual", json={"member_ids": [late.id], "day": 1}))
    content = [row for row in rows(db_session, PushKind.EVENT_CONTENT) if row.member_id == late.id]
    assert [row.dedupe_key for row in content] == [f"event_content:{event.id}:{draft}:{late.id}"]


def test_late_attendance_looks_up_the_content_once_per_request(
    admin_client, db_session, attendable, outbound, push_on, frozen, kicks, monkeypatch
):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0), end=datetime(2026, 7, 19, 18, 0))
    attendance_log(db_session, event)
    members = [app_user(db_session), app_user(db_session)]
    db_session.commit()
    calls: list[list[int]] = []
    real = cases.attendance_added

    def counted(session, event, member_ids):
        calls.append(list(member_ids))
        return real(session, event, member_ids)

    monkeypatch.setattr(cases, "attendance_added", counted)
    body = {"member_ids": [m.id for m in members], "days": [1, 2]}
    assert_2xx(admin_client.post(f"/attendance/{event.id}/manual", json=body))
    assert calls == [[m.id for m in members]]


def test_content_taken_down_before_delivery_is_skipped(db_session, push_on, apns_mock, frozen):
    frozen.set(ELEVEN_PM)
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attended(db_session, event, app_user(db_session))
    link = EventContentLinks(
        event_id=event.id,
        label="Slides",
        url="https://example.com/s.pdf",
        kind=EventsContentLinkKind.SLIDES,
        published=1,
    )
    db_session.add(link)
    db_session.flush()
    assert cases.content_added(db_session, link) == 1
    assert rows(db_session, PushKind.EVENT_CONTENT)[0].not_before == EIGHT_AM_TOMORROW
    db_session.delete(link)
    db_session.commit()
    frozen.set(EIGHT_AM_TOMORROW)
    deliver()
    [row] = rows(db_session, PushKind.EVENT_CONTENT)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "content_removed")


# ---------------------------------------------------------------- weekly_summary

THURSDAY_8PM = datetime(2026, 7, 23, 17, 0)  # 20:00 Riyadh, ISO week 2026-W30, in Summer 2026


def test_on_thursday_evening_each_member_with_points_gets_their_week(db_session, push_on):
    this_week = make_event(db_session, start=datetime(2026, 7, 21, 16, 0))
    earlier = make_event(db_session, start=datetime(2026, 7, 5, 16, 0))
    week_log, earlier_log = (
        attendance_log(db_session, this_week, points=5),
        attendance_log(db_session, earlier, points=3),
    )
    busy, steady, muted = app_user(db_session), app_user(db_session), app_user(db_session, points=False)
    app_user(db_session)  # no points
    for member, log, day in (
        (busy, week_log, datetime(2026, 7, 21, 16, 30)),
        (busy, earlier_log, datetime(2026, 7, 5, 16, 30)),
        (steady, earlier_log, datetime(2026, 7, 5, 16, 30)),
        (muted, week_log, datetime(2026, 7, 21, 16, 30)),
    ):
        db_session.add(MembersLogs(member_id=member.id, log_id=log.id, date=day))
    db_session.commit()

    assert sweep(db_session, THURSDAY_8PM - timedelta(minutes=1)).push_weekly_summaries == 0
    assert sweep(db_session, THURSDAY_8PM).push_weekly_summaries == 2
    found = {row.member_id: row for row in rows(db_session, PushKind.WEEKLY_SUMMARY)}
    assert set(found) == {busy.id, steady.id}
    assert found[busy.id].dedupe_key == f"weekly_summary:2026-W30:{busy.id}"
    assert found[busy.id].payload["en"] == {
        "title": "Your week at GDG Qassim",
        "body": "+5 points this week · 8 points this semester",
    }
    assert found[busy.id].payload["ar"] == {
        "title": "ملخص أسبوعك",
        "body": "\u2066+5\u2069 نقاط هذا الأسبوع · \u20668\u2069 نقاط هذا الفصل",
    }
    assert found[steady.id].payload["en"]["body"] == "3 points this semester"
    assert found[busy.id].payload["url"] == "gdgq://points/history"
    assert found[busy.id].not_before == THURSDAY_8PM
    assert sweep(db_session, THURSDAY_8PM + timedelta(hours=1)).push_weekly_summaries == 0


def test_custom_points_given_this_week_on_an_old_event_count_this_week(db_session, push_on):
    old = make_event(db_session, start=datetime(2026, 7, 2, 0, 0), location_type=EventsLocationType.NONE)
    log = attendance_log(db_session, old, points=20)
    member = app_user(db_session)
    db_session.add(MembersLogs(member_id=member.id, log_id=log.id, date=datetime(2026, 7, 22, 10, 0)))
    db_session.commit()
    sweep(db_session, THURSDAY_8PM)
    [row] = rows(db_session, PushKind.WEEKLY_SUMMARY)
    assert row.payload["en"]["body"] == "+20 points this week · 20 points this semester"


def test_editing_a_custom_points_entry_keeps_when_its_points_were_given(
    admin_client, db_session, push_on, frozen, kicks
):
    """``PUT /custom/members/{log_id}`` keeps the rows of the members who stay, so the edit moves no points."""
    event = make_event(db_session, start=datetime(2026, 7, 2, 0, 0), location_type=EventsLocationType.NONE)
    log = attendance_log(db_session, event, points=4)
    staying, added = app_user(db_session), app_user(db_session)
    db_session.add(MembersLogs(member_id=staying.id, log_id=log.id, date=datetime(2026, 7, 5, 10, 0)))
    db_session.commit()

    response = admin_client.put(f"/custom/members/{log.id}", json={"member_ids": [staying.id, added.id], "points": 4})
    assert_2xx(response)
    db_session.expire_all()
    found = db_session.execute(select(MembersLogs.member_id, MembersLogs.date).where(MembersLogs.log_id == log.id))
    dates = dict(found.tuples().all())
    assert set(dates) == {staying.id, added.id}
    assert dates[staying.id] == datetime(2026, 7, 5, 10, 0)

    sweep(db_session, THURSDAY_8PM)
    summary = next(r for r in rows(db_session, PushKind.WEEKLY_SUMMARY) if r.member_id == staying.id)
    assert summary.payload["en"]["body"] == "4 points this semester"

    # Taken off the entry: their row goes.
    assert_2xx(admin_client.put(f"/custom/members/{log.id}", json={"member_ids": [added.id], "points": 4}))
    db_session.expire_all()
    assert db_session.scalars(select(MembersLogs.member_id).where(MembersLogs.log_id == log.id)).all() == [added.id]


def test_no_summary_on_other_days_late_at_night_or_between_semesters(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 21, 16, 0))
    member = app_user(db_session)
    log = attendance_log(db_session, event)
    db_session.add(MembersLogs(member_id=member.id, log_id=log.id, date=datetime(2026, 7, 21, 16, 30)))
    db_session.commit()
    assert sweep(db_session, datetime(2026, 7, 22, 17, 0)).push_weekly_summaries == 0  # Wednesday
    assert sweep(db_session, datetime(2026, 7, 23, 19, 0)).push_weekly_summaries == 0  # 22:00, quiet hours
    assert sweep(db_session, datetime(2026, 8, 27, 17, 0)).push_weekly_summaries == 0  # Summer 2026 ended 08-20


# ---------------------------------------------------------------- every new kind


def _queue(session, kind: PushKind, member, key: str, **extra) -> int:
    return outbox.enqueue(
        session,
        kind,
        [Push(member_id=member.id, dedupe_key=key, cause=extra.pop("cause", None))],
        text=messages.welcome(),
        url=links.DEFAULT_URL,
        thread_id="news",
        expires_at=extra.pop("expires_at", datetime(2026, 7, 21, 9, 0)),
        **extra,
    )


def test_each_new_kind_answers_to_its_switch():
    assert {kind.value: outbox.SWITCH_FOR[kind] for kind in cases.KINDS} == {
        "event_updated": "remind",
        "event_cancelled": "remind",
        "event_live": "remind",
        "registration_closing": "news",
        "certificate": "points",
        "event_content": "remind",
        "weekly_summary": "points",
        "welcome": "news",
    }


@pytest.mark.parametrize("kind", sorted(cases.KINDS), ids=str)
def test_a_switch_turned_off_before_delivery_skips_every_new_kind(db_session, push_on, apns_mock, frozen, kind):
    member = app_user(db_session)
    assert _queue(db_session, kind, member, f"{kind.value}:switch:{member.id}") == 1
    set_prefs(db_session, member, **{outbox.SWITCH_FOR[kind]: False})
    db_session.commit()
    deliver()
    [row] = rows(db_session, kind)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "switch_off")
    assert apns_mock.requests == []


def test_three_capped_pushes_a_day_but_live_and_cancelled_always_go(db_session, push_on, apns_mock, frozen):
    member = app_user(db_session)
    db_session.add(
        PushNotifications(
            member_id=member.id,
            kind=PushKind.WELCOME,
            dedupe_key=f"welcome:{member.id}",
            payload={},
            status=PushStatus.SENT,
            not_before=frozen.now - timedelta(hours=1),
            expires_at=frozen.now + timedelta(days=1),
            created_at=frozen.now - timedelta(hours=1),
            sent_at=frozen.now - timedelta(hours=1),
        )
    )
    for n in range(3):
        _queue(db_session, PushKind.WEEKLY_SUMMARY, member, f"weekly_summary:test-{n}:{member.id}")
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=EventsStatus.CLOSED)
    submission = register(db_session, event, member)
    cause = {"submission_id": submission.id, "status": "closed"}
    _queue(db_session, PushKind.EVENT_CANCELLED, member, f"event_cancelled:{event.id}:{member.id}", cause=cause)
    db_session.commit()

    deliver()
    summaries = rows(db_session, PushKind.WEEKLY_SUMMARY)
    assert sorted((row.status.value, row.last_error) for row in summaries) == [
        ("sent", None),
        ("sent", None),
        ("skipped", "daily_cap"),
    ]
    [cancelled] = rows(db_session, PushKind.EVENT_CANCELLED)
    assert cancelled.status == PushStatus.SENT


def test_the_cap_counts_pushes_another_worker_has_out_for_delivery(db_session, push_on, apns_mock, frozen):
    member = app_user(db_session)
    for n in range(3):
        _queue(db_session, PushKind.WEEKLY_SUMMARY, member, f"weekly_summary:out-{n}:{member.id}")
    for row in rows(db_session, PushKind.WEEKLY_SUMMARY):
        row.attempts, row.not_before = 1, frozen.now + timedelta(minutes=5)  # leased, not recorded yet
    _queue(db_session, PushKind.WEEKLY_SUMMARY, member, f"weekly_summary:due:{member.id}")
    db_session.commit()
    deliver()
    due = next(row for row in rows(db_session, PushKind.WEEKLY_SUMMARY) if row.dedupe_key.endswith(f"due:{member.id}"))
    assert (due.status, due.last_error) == (PushStatus.SKIPPED, "daily_cap")
    assert apns_mock.requests == []


def test_capped_pushes_wait_while_another_worker_holds_the_member(db_session, push_on, apns_mock, frozen, monkeypatch):
    member = app_user(db_session)
    _queue(db_session, PushKind.WEEKLY_SUMMARY, member, f"weekly_summary:held:{member.id}")
    _queue(db_session, PushKind.BROADCAST, member, f"broadcast:held:{member.id}", switch="news")
    db_session.commit()
    lock_members = delivery.lock_members
    monkeypatch.setattr(delivery, "lock_members", lambda session, member_ids: set())
    deliver()
    [summary] = rows(db_session, PushKind.WEEKLY_SUMMARY)
    assert (summary.status, summary.attempts) == (PushStatus.PENDING, 0)
    assert rows(db_session, PushKind.BROADCAST)[0].status == PushStatus.SENT
    monkeypatch.setattr(delivery, "lock_members", lock_members)
    deliver()
    assert rows(db_session, PushKind.WEEKLY_SUMMARY)[0].status == PushStatus.SENT


def test_a_1_0_2_phone_gets_the_new_kinds_too(db_session, push_on, apns_mock, frozen):
    """1.0.2 (build 4) reads an unknown ``kind`` as none, still shows the alert and opens the link (Notifications
    when it does not know the link), and never reads ``GET /notifications/me``: nothing needs hiding from it."""
    member = make_member(db_session)
    device = add_device(db_session, member)
    device.app_version, device.app_build = "1.0.2", "4"
    _queue(db_session, PushKind.WELCOME, member, f"welcome:{member.id}")
    db_session.commit()
    assert deliver().sent == 1
    [payload] = apns_mock.payloads()
    assert payload["kind"] == "welcome" and payload["body"]["kind"] == "welcome"


def test_the_certificates_link_is_allowed():
    assert links.is_allowed("gdgq://profile/certificates")
    assert not links.is_allowed("gdgq://profile/certificates/1")
