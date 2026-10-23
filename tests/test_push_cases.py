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
    Events,
    EventsContentLinkKind,
    EventsLocationType,
    EventsStatus,
    FormType,
    MembersLogs,
    PushKind,
    PushNotifications,
    PushStatus,
    SubmissionsSubmissionType,
)
from app.services import email_jobs
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
    """Change the event as a route would, then run the trigger the routes run."""
    before = cases.event_state(event)
    for key, value in values.items():
        setattr(event, key, value)
    session.flush()
    return cases.event_changed(session, event, before)


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
    prefix, digest = row.dedupe_key.rsplit(":", 1)
    assert prefix == f"event_updated:{event.id}:{accepted.id}" and len(digest) == 16
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
    db_session.flush()
    assert cases.event_changed(db_session, event, before) == 1
    assert cases.event_changed(db_session, event, before) == 0
    assert change(db_session, event, location="Hall 62") == 1
    assert len(rows(db_session, PushKind.EVENT_UPDATED)) == 2


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
    assert (first.status, first.last_error) == (PushStatus.SKIPPED, "event_changed")
    assert second.status == PushStatus.SENT
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
    assert (row.member_id, row.dedupe_key) == (accepted.id, f"event_cancelled:{event.id}:{accepted.id}")
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
