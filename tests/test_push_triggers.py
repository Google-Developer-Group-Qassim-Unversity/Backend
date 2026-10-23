"""What queues a push: the routes (acceptance, attendance, custom points) and the sweep's rules, with a frozen clock.

The clock is naive UTC 2026-07-20 09:00, which is 12:00 in Riyadh; event times
are Riyadh wall clock, so an event "at 13:00" starts at 10:00 UTC.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB import logs as log_queries
from app.DB import members as member_queries
from app.DB.schema import (
    EventHistory,
    EventHistoryAction,
    EventsLocationType,
    EventsStatus,
    FormType,
    MemberDevices,
    MemberNotificationPrefs,
    MembersLogs,
    PushEnvironment,
    PushKind,
    PushNotifications,
    PushStatus,
)
from app.services.pipeline_sweep import SweepResult, run_sweep
from app.services.push import triggers
from tests.push_support import add_device, attendance_log, deliver, make_event, make_member, register, set_prefs
from tests.utils import assert_2xx


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


@pytest.fixture
def attendable(monkeypatch):
    """Any log of the event is its attendance log (production hardcodes action ids; see test_attendance.py)."""
    from app.DB.schema import Logs

    monkeypatch.setattr(
        log_queries,
        "get_attendable_logs",
        lambda session, event_id: session.scalar(select(Logs).where(Logs.event_id == event_id).order_by(Logs.id)),
    )


# ---------------------------------------------------------------- accepted


def send_acceptance(client, event_id):
    response = client.post(
        f"/emails/acceptance/blasts/{event_id}",
        params={"subject": "Accepted"},
        content=b"<p>You are in</p>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert_2xx(response)
    return response.json()


def test_sending_the_acceptance_pushes_to_accepted_app_users(
    admin_client, db_session, outbound, push_on, apns_mock, frozen
):
    event = make_event(db_session, start=datetime(2026, 7, 25, 16, 30))
    with_app, without_app, muted = app_user(db_session), make_member(db_session), app_user(db_session, accept=False)
    submissions = {m.id: register(db_session, event, m) for m in (with_app, without_app, muted)}
    db_session.commit()

    send_acceptance(admin_client, event.id)

    [row] = rows(db_session, PushKind.ACCEPTED)
    submission = submissions[with_app.id]
    epoch = int((submission.submitted_at - datetime(1970, 1, 1)).total_seconds())
    assert row.member_id == with_app.id
    assert row.dedupe_key == f"accepted:{submission.id}:{epoch}"
    assert row.payload["ar"] == {"title": "تم قبول تسجيلك", "body": "\u2068Intro to ML\u2069 · 25 يوليو · 4:30 م"}
    assert row.payload["url"] == f"gdgq://events/{event.id}"
    assert row.expires_at == frozen.now + timedelta(days=7)
    # Kicked after the response.
    assert row.status == PushStatus.SENT and len(apns_mock.requests) == 1


def test_a_second_acceptance_send_pushes_nothing_new(admin_client, db_session, outbound, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 25, 16, 30))
    register(db_session, event, app_user(db_session))
    db_session.commit()
    send_acceptance(admin_client, event.id)
    # Nobody left uninvited; even if the claim were released and sent again, the key is the same.
    send_acceptance(admin_client, event.id)
    assert len(rows(db_session, PushKind.ACCEPTED)) == 1


def test_registering_again_after_cancelling_gets_a_new_acceptance_push(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 25, 16, 30))
    member = app_user(db_session)
    submission = register(db_session, event, member)
    view_row = type(
        "Row", (), {"id": member.id, "submission_id": submission.id, "submitted_at": submission.submitted_at}
    )
    assert triggers.acceptance_sent(db_session, event, [view_row]) == 1
    assert triggers.acceptance_sent(db_session, event, [view_row]) == 0
    view_row.submitted_at = submission.submitted_at + timedelta(days=1)
    assert triggers.acceptance_sent(db_session, event, [view_row]) == 1


# ---------------------------------------------------------------- points


def mark(client, event_id, member_id, day=1):
    response = client.post(f"/attendance/{event_id}/manual", json={"member_ids": [member_id], "day": day})
    assert_2xx(response)


def test_staff_attendance_pushes_the_days_points(admin_client, db_session, attendable, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0), end=datetime(2026, 7, 19, 18, 0))
    log = attendance_log(db_session, event, points=5)
    member = app_user(db_session)
    db_session.commit()

    mark(admin_client, event.id, member.id, day=1)
    mark(admin_client, event.id, member.id, day=1)  # already marked: no new attendance, no new push
    mark(admin_client, event.id, member.id, day=2)

    found = rows(db_session, PushKind.POINTS)
    assert [r.dedupe_key for r in found] == [
        f"points:{log.id}:{member.id}:2026-07-18",
        f"points:{log.id}:{member.id}:2026-07-19",
    ]
    assert found[0].payload["ar"]["title"] == "\u2066+5\u2069 نقاط"
    assert found[0].payload["en"]["body"] == "\u2068Intro to ML\u2069 · added to your balance"
    assert found[0].payload["url"] == "gdgq://points/history"
    assert {r.status for r in found} == {PushStatus.SENT}


def test_points_include_the_logs_bonus(db_session, attendable, push_on):
    from app.DB.schema import Modifications, ModificationsType

    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    log = attendance_log(db_session, event, points=5)
    db_session.add(Modifications(log_id=log.id, type=ModificationsType.BONUS, value=3))
    db_session.add(Modifications(log_id=log.id, type=ModificationsType.DISCOUNT, value=1))
    db_session.flush()
    assert triggers.log_points(db_session, log.id) == 7


def test_no_points_push_without_the_app_or_with_the_switch_off(admin_client, db_session, attendable, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attendance_log(db_session, event)
    no_app, muted = make_member(db_session), app_user(db_session, points=False)
    dev_build = make_member(db_session)
    add_device(db_session, dev_build, environment=PushEnvironment.DEVELOPMENT)
    db_session.commit()
    for member in (no_app, muted, dev_build):
        mark(admin_client, event.id, member.id)
    assert rows(db_session, PushKind.POINTS) == []


def test_nothing_is_queued_while_push_is_off(admin_client, db_session, attendable):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attendance_log(db_session, event)
    member = app_user(db_session)
    db_session.commit()
    mark(admin_client, event.id, member.id)
    assert rows(db_session) == []


def test_a_members_own_qr_check_in_queues_no_push(db_session, push_on):
    """The check-in route (POST /attendance/{id}?token=) is not a trigger; only staff recording is."""
    import inspect

    from app.routers import attendance

    assert "push_triggers" not in inspect.getsource(attendance.mark_attendance)


def give_custom(client, event_id, member_ids, points=4):
    response = client.post(
        "/custom/members",
        json={
            "event_id": event_id,
            "event_name": "ignored",
            "location_type": "online",
            "start_datetime": "2026-07-18T00:00:00",
            "end_datetime": "2026-07-18T00:00:00",
            "point_deatils": [{"member_ids": member_ids, "points": points}],
        },
    )
    assert_2xx(response)


def test_custom_points_push_once_per_member_even_after_an_edit(admin_client, db_session, push_on, apns_mock, frozen):
    from app.DB.schema import Logs

    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    first, second = app_user(db_session), app_user(db_session)
    db_session.commit()

    give_custom(admin_client, event.id, [first.id])
    [row] = rows(db_session, PushKind.POINTS)
    log_id = db_session.scalars(select(Logs.id).where(Logs.event_id == event.id)).one()
    assert row.dedupe_key == f"points:custom:{log_id}:{first.id}"
    assert row.payload["en"]["title"] == "+4 points"

    # The edit deletes and recreates every members_logs row; only the added member hears about it.
    response = admin_client.put(f"/custom/members/{log_id}", json={"member_ids": [first.id, second.id], "points": 4})
    assert_2xx(response)
    assert sorted(r.member_id for r in rows(db_session, PushKind.POINTS)) == sorted([first.id, second.id])


def test_a_deduction_pushes_nothing(admin_client, db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    member = app_user(db_session)
    db_session.commit()
    give_custom(admin_client, event.id, [member.id], points=-3)
    assert rows(db_session, PushKind.POINTS) == []


# ---------------------------------------------------------------- reminders


def sweep(session, now) -> SweepResult:
    result = run_sweep(session, now)
    session.commit()
    return result


def test_a_reminder_goes_out_an_hour_before_the_start_in_riyadh_time(db_session, push_on, frozen):
    # 13:00 Riyadh = 10:00 UTC.
    event = make_event(db_session, start=datetime(2026, 7, 20, 13, 0), location="Hall 60")
    member = app_user(db_session)
    register(db_session, event, member)
    db_session.commit()

    # 11:59 Riyadh: 13:00 is not within the hour yet.
    assert sweep(db_session, datetime(2026, 7, 20, 8, 59)).push_reminders == 0
    # 12:00 Riyadh: it is.
    assert sweep(db_session, datetime(2026, 7, 20, 9, 0)).push_reminders == 1
    [row] = rows(db_session, PushKind.REMINDER)
    assert row.dedupe_key == f"reminder:{event.id}:2026-07-20:{member.id}"
    assert row.payload["ar"] == {
        "title": "تذكير: \u2068Intro to ML\u2069",
        "body": "تبدأ اليوم الساعة 1:00 م · \u2068Hall 60\u2069",
    }
    assert row.expires_at == datetime(2026, 7, 20, 10, 0)
    assert row.not_before == datetime(2026, 7, 20, 9, 0)
    # Running again adds nothing.
    assert sweep(db_session, datetime(2026, 7, 20, 9, 30)).push_reminders == 0


def test_reminders_ignore_quiet_hours(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 20, 23, 0))  # 23:00 Riyadh
    register(db_session, event, app_user(db_session))
    db_session.commit()
    now = datetime(2026, 7, 20, 19, 30)  # 22:30 Riyadh
    sweep(db_session, now)
    [row] = rows(db_session, PushKind.REMINDER)
    assert row.not_before == now


def test_each_day_of_a_multi_day_event_gets_its_reminder(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 19, 16, 0), end=datetime(2026, 7, 21, 18, 0))
    register(db_session, event, app_user(db_session))
    db_session.commit()
    sweep(db_session, datetime(2026, 7, 20, 12, 30))  # 15:30 Riyadh on day 2
    [row] = rows(db_session, PushKind.REMINDER)
    assert row.payload["ar"]["body"].startswith("اليوم 2 من 3 يبدأ الساعة 4:00 م")
    assert row.payload["en"]["body"].startswith("Day 2 of 3 starts at 4:00 PM")


def test_reminders_skip_pending_registrations_check_ins_and_unattended_events(db_session, push_on):
    event = make_event(db_session, start=datetime(2026, 7, 20, 13, 0))
    log = attendance_log(db_session, event)
    accepted, pending, checked_in = app_user(db_session), app_user(db_session), app_user(db_session)
    register(db_session, event, accepted)
    register(db_session, event, pending, accepted=False)
    register(db_session, event, checked_in)
    db_session.add(MembersLogs(member_id=checked_in.id, log_id=log.id, date=datetime(2026, 7, 20, 11, 0)))
    for status, location, form in (
        (EventsStatus.DRAFT, EventsLocationType.ON_SITE, FormType.REGISTRATION),
        (EventsStatus.CLOSED, EventsLocationType.ON_SITE, FormType.REGISTRATION),
        (EventsStatus.OPEN, EventsLocationType.HIDDEN, FormType.REGISTRATION),
        (EventsStatus.OPEN, EventsLocationType.ON_SITE, FormType.NONE),
    ):
        other = make_event(
            db_session, start=datetime(2026, 7, 20, 13, 0), status=status, location_type=location, form_type=form
        )
        if form != FormType.NONE:
            register(db_session, other, app_user(db_session))
    db_session.commit()
    sweep(db_session, datetime(2026, 7, 20, 9, 0))
    assert [r.member_id for r in rows(db_session, PushKind.REMINDER)] == [accepted.id]


def test_a_cancelled_registration_loses_its_waiting_reminder(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 20, 13, 0))
    submission = register(db_session, event, app_user(db_session))
    db_session.commit()
    sweep(db_session, frozen.now)
    submission.cancelled_at = frozen.now
    db_session.commit()
    deliver()
    [row] = rows(db_session, PushKind.REMINDER)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "not_accepted")
    assert apns_mock.requests == []


def test_a_rescheduled_event_drops_the_old_reminder(db_session, push_on, apns_mock, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 20, 13, 0))
    register(db_session, event, app_user(db_session))
    db_session.commit()
    sweep(db_session, frozen.now)
    event.start_datetime = datetime(2026, 7, 20, 14, 0)
    event.end_datetime = datetime(2026, 7, 20, 16, 0)
    db_session.commit()
    deliver()
    assert rows(db_session, PushKind.REMINDER)[0].last_error == "event_changed"


# ---------------------------------------------------------------- new events


def test_an_event_created_open_is_announced_to_members_with_club_news_on(db_session, push_on, frozen):
    reader, other = app_user(db_session), app_user(db_session, news=False)
    event = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), created_at=frozen.now - timedelta(hours=3))
    db_session.commit()
    assert sweep(db_session, frozen.now).push_new_events == 1
    [row] = rows(db_session, PushKind.NEW_EVENT)
    assert (row.member_id, row.dedupe_key) == (reader.id, f"new_event:{event.id}:{reader.id}")
    assert row.payload["en"] == {
        "title": "New event: \u2068Intro to ML\u2069",
        "body": "Jul 28 · 4:00 PM · \u2068Hall 60\u2069",
    }
    assert row.expires_at == datetime(2026, 7, 28, 13, 0)
    assert other.id not in {r.member_id for r in rows(db_session)}
    assert sweep(db_session, frozen.now).push_new_events == 0


def test_a_draft_opened_today_is_announced_but_an_old_open_event_is_not(db_session, push_on, frozen):
    app_user(db_session, news=True)
    opened_today = make_event(db_session, start=datetime(2026, 7, 28, 16, 0), created_at=datetime(2026, 6, 1))
    db_session.add(
        EventHistory(
            event_id=opened_today.id,
            action=EventHistoryAction.STATUS_CHANGED,
            at=frozen.now - timedelta(hours=1),
            details={"status": ["draft", "open"]},
            event_name=opened_today.name,
        )
    )
    make_event(db_session, start=datetime(2026, 7, 28, 16, 0), created_at=datetime(2026, 6, 1))  # open for weeks
    make_event(db_session, start=datetime(2026, 7, 28, 16, 0), status=EventsStatus.DRAFT, created_at=frozen.now)
    make_event(db_session, start=datetime(2026, 7, 19, 16, 0), created_at=frozen.now)  # already started
    db_session.commit()
    sweep(db_session, frozen.now)
    assert [r.event_id for r in rows(db_session, PushKind.NEW_EVENT)] == [opened_today.id]


def test_announcements_wait_for_8am_and_two_a_day_reach_a_member(db_session, push_on, apns_mock, frozen):
    app_user(db_session, news=True)
    for hour in (1, 2, 3):
        make_event(db_session, start=datetime(2026, 7, 28, 16, hour), created_at=datetime(2026, 7, 20, 19, 0))
    db_session.commit()
    night = datetime(2026, 7, 20, 20, 0)  # 23:00 Riyadh
    sweep(db_session, night)
    assert {r.not_before for r in rows(db_session, PushKind.NEW_EVENT)} == {datetime(2026, 7, 21, 5, 0)}
    frozen.set(night)
    deliver()
    assert apns_mock.requests == []
    frozen.set(datetime(2026, 7, 21, 5, 0))
    deliver()
    found = rows(db_session, PushKind.NEW_EVENT)
    assert sorted(r.status.value for r in found) == ["sent", "sent", "skipped"]
    assert [r.last_error for r in found if r.status == PushStatus.SKIPPED] == ["daily_cap"]
    assert {r.headers["apns-priority"] for r in apns_mock.requests} == {"5"}


# ---------------------------------------------------------------- clean-up and account deletion


def test_the_sweep_drops_old_rows_and_phones_unseen_for_half_a_year(db_session, push_on, frozen):
    member = make_member(db_session)
    stale = add_device(db_session, member, seen=frozen.now - timedelta(days=181))
    fresh = add_device(db_session, member, seen=frozen.now - timedelta(days=179))
    for key, age in (("old", 91), ("recent", 89)):
        db_session.add(
            PushNotifications(
                member_id=member.id,
                kind=PushKind.BROADCAST,
                dedupe_key=key,
                payload={},
                not_before=frozen.now,
                expires_at=frozen.now,
                created_at=frozen.now - timedelta(days=age),
            )
        )
    db_session.commit()
    assert sweep(db_session, frozen.now).push_cleaned == 2
    db_session.expire_all()
    assert [r.dedupe_key for r in rows(db_session)] == ["recent"]
    assert db_session.get(MemberDevices, stale.id) is None and db_session.get(MemberDevices, fresh.id) is not None


def test_deleting_an_account_removes_phones_switches_and_every_notification(db_session, push_on, frozen):
    member = app_user(db_session, news=True)
    keep = app_user(db_session)
    for owner, status in ((member, PushStatus.SENT), (member, PushStatus.PENDING), (keep, PushStatus.SENT)):
        db_session.add(
            PushNotifications(
                member_id=owner.id,
                kind=PushKind.BROADCAST,
                dedupe_key=f"{owner.id}:{status.value}",
                payload={},
                status=status,
                not_before=frozen.now,
                expires_at=frozen.now,
                created_at=frozen.now,
            )
        )
    db_session.flush()
    member_queries.anonymize_member(db_session, member)
    db_session.commit()
    db_session.expire_all()
    for table in (PushNotifications, MemberDevices, MemberNotificationPrefs):
        assert db_session.scalars(select(table).where(table.member_id == member.id)).all() == []
    assert db_session.scalars(select(PushNotifications).where(PushNotifications.member_id == keep.id)).all()


def test_removed_attendance_drops_the_waiting_points_push(admin_client, db_session, attendable, push_on, frozen):
    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    attendance_log(db_session, event)
    member = app_user(db_session)
    db_session.commit()
    frozen.set(datetime(2026, 7, 20, 20, 0))  # 23:00 Riyadh: held until 08:00
    mark(admin_client, event.id, member.id)
    response = admin_client.request(
        "DELETE", f"/attendance/{event.id}/manual", json={"member_ids": [member.id], "day": 1}
    )
    assert_2xx(response)
    frozen.set(datetime(2026, 7, 21, 5, 0))
    deliver()
    [row] = rows(db_session, PushKind.POINTS)
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "points_removed")


# ---------------------------------------------------------------- a push never blocks its cause


def test_a_failing_push_never_blocks_the_attendance(admin_client, db_session, attendable, push_on, monkeypatch):
    from app.services.push import outbox

    event = make_event(db_session, start=datetime(2026, 7, 18, 16, 0))
    log = attendance_log(db_session, event)
    member = app_user(db_session)
    db_session.commit()

    def broken(*args, **kwargs):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(outbox, "enqueue", broken)
    mark(admin_client, event.id, member.id)
    db_session.expire_all()
    assert db_session.scalars(select(MembersLogs).where(MembersLogs.log_id == log.id)).all()


def test_a_failing_push_rule_never_stops_the_sweep(db_session, push_on, frozen, monkeypatch):
    from app.DB import push as push_queries

    def broken(*args, **kwargs):
        raise RuntimeError("clean-up down")

    monkeypatch.setattr(push_queries, "delete_old", broken)
    result = sweep(db_session, frozen.now)
    assert result.push_cleaned == 0
