"""What queues a push, and the check that it still holds when it is delivered.

Routes call these in the same transaction as their own change, before their
commit: an organizer sending the acceptance (``accepted``), staff recording
attendance or custom points (``points``). The pipeline sweep runs the two
time-based rules: ``reminder`` (an hour before each event day) and
``new_event`` (an event that became open). A member's own QR check-in queues
nothing; the app shows its own success screen.

Event times are Riyadh wall clock, our columns UTC (``clock``). Attendance
rows (``members_logs.date``) are written with the process clock, so their
day is read exactly as ``is_member_marked_for_day`` reads it: the stored value
through ``get_effective_date``, never converted.

A row can wait until 08:00 (quiet hours), so delivery asks ``cause_gone``
again first: staff can remove attendance, a registration can be cancelled.
"""

import functools
import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import config
from app.DB import push as push_queries
from app.DB.schema import (
    Actions,
    EventHistory,
    EventHistoryAction,
    Events,
    EventsLocationType,
    EventsStatus,
    Forms,
    FormType,
    Logs,
    MembersLogs,
    Modifications,
    ModificationsType,
    PushKind,
    PushNotifications,
    Submissions,
    SubmissionsSubmissionType,
)
from app.helpers import get_effective_date
from app.services.push import clock, links, messages, outbox
from app.services.push.outbox import Push

logger = logging.getLogger(__name__)

REMINDER_LEAD = timedelta(minutes=60)
ACCEPTED_TTL = timedelta(days=7)
POINTS_TTL = timedelta(days=3)
# An event announced as new only if it became open this recently, so a deploy never announces old events.
NEW_EVENT_WINDOW = timedelta(hours=48)
KEEP_ROWS_DAYS = 90
KEEP_DEVICES_DAYS = 180

# Events members attend; custom-points entries ("none") and hidden ones get no reminders or announcements.
ATTENDED_LOCATIONS = (EventsLocationType.ONLINE, EventsLocationType.ON_SITE)
REGISTRATION_FORMS = (FormType.REGISTRATION, FormType.GOOGLE)


# ---------------------------------------------------------------- shared


def event_days(event: Events) -> int:
    """As ``app.routers.attendance.get_event_days``."""
    return (event.end_datetime - event.start_datetime).days + 1


def day_start(event: Events, day: int) -> datetime:
    """Day ``day`` (1-based) starts on the start date + day - 1, at the start time. Riyadh wall clock."""
    return event.start_datetime + timedelta(days=day - 1)


def log_points(session: Session, log_id: int) -> int:
    """One attendance (or custom-points entry) on this log is worth: the action's points plus its bonus or discount.

    The same rule as the ``members_points`` view.
    """
    base = session.scalar(select(Actions.points).join(Logs, Logs.action_id == Actions.id).where(Logs.id == log_id))
    modifier = session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Modifications.type == ModificationsType.DISCOUNT, -func.abs(Modifications.value)),
                        else_=func.abs(Modifications.value),
                    )
                ),
                0,
            )
        ).where(Modifications.log_id == log_id)
    )
    return int(base or 0) + int(modifier or 0)


def _effective_day(value: datetime):
    return get_effective_date(value, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)


def never_blocks(trigger: Callable[..., int]) -> Callable[..., int]:
    """A push is a side effect: if queueing it fails, the change that caused it still goes through.

    The trigger runs in a savepoint; an error rolls back only what it wrote, is
    logged (and reported to Sentry), and the route or sweep carries on.
    """

    @functools.wraps(trigger)
    def run(session: Session, *args, **kwargs) -> int:
        try:
            with session.begin_nested():
                return trigger(session, *args, **kwargs)
        except Exception:
            logger.exception("Queueing pushes failed in %s; the change itself went through", trigger.__name__)
            return 0

    return run


# ---------------------------------------------------------------- route triggers


@never_blocks
def acceptance_sent(session: Session, event: Events, submissions: Sequence[Any]) -> int:
    """The organizers sent the acceptance: ``submissions`` are the ``forms_submissions`` rows it claimed."""
    if not config.push_enabled:
        return 0
    pushes = [
        Push(
            member_id=row.id,
            dedupe_key=f"accepted:{row.submission_id}:{clock.epoch(row.submitted_at)}",
            cause={"submission_id": row.submission_id, "submitted_at": row.submitted_at.isoformat()},
        )
        for row in submissions
        if row.id is not None and row.submitted_at is not None
    ]
    return outbox.enqueue(
        session,
        PushKind.ACCEPTED,
        pushes,
        text=messages.accepted(event.name, event.start_datetime),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.now() + ACCEPTED_TTL,
    )


@never_blocks
def attendance_recorded(session: Session, event: Events, log: Logs, member_log: MembersLogs) -> int:
    """Staff recorded a day's attendance (card scan, manual, import). One push per member per event day."""
    if not config.push_enabled:
        return 0
    count = log_points(session, log.id)
    if count <= 0:
        return 0
    day = _effective_day(member_log.date).isoformat()
    push = Push(
        member_id=member_log.member_id,
        dedupe_key=f"points:{log.id}:{member_log.member_id}:{day}",
        cause={"type": "attendance", "log_id": log.id, "day": day},
    )
    return _points(session, event, [push], count)


@never_blocks
def custom_points_given(session: Session, event: Events, log_id: int, member_ids: Iterable[int]) -> int:
    """Staff gave custom points. Editing the entry later notifies only the members it adds.

    Keyed by log and member, not by the ``members_logs`` row: an edit
    (``PUT /custom/members/{log_id}``) deletes and recreates every row, which
    would notify everyone again. A deduction (zero or fewer points) sends nothing.
    """
    if not config.push_enabled:
        return 0
    count = log_points(session, log_id)
    if count <= 0:
        return 0
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"points:custom:{log_id}:{member_id}",
            cause={"type": "custom", "log_id": log_id},
        )
        for member_id in dict.fromkeys(member_ids)
    ]
    return _points(session, event, pushes, count)


def _points(session: Session, event: Events, pushes: list[Push], count: int) -> int:
    return outbox.enqueue(
        session,
        PushKind.POINTS,
        pushes,
        text=messages.points(event.name, count),
        url=links.POINTS_URL,
        thread_id="points",
        event_id=event.id,
        expires_at=clock.now() + POINTS_TTL,
    )


# ---------------------------------------------------------------- sweep rules


def enqueue_event_reminders(session: Session, now: datetime, result) -> None:
    """An hour before each event day starts: the members accepted to it who have not checked in that day."""
    if not config.push_enabled:
        return
    local_now = clock.to_riyadh(now)
    horizon = local_now + REMINDER_LEAD
    events = session.scalars(
        select(Events).where(
            Events.status.in_([EventsStatus.OPEN, EventsStatus.ACTIVE]),
            Events.location_type.in_(ATTENDED_LOCATIONS),
            Events.start_datetime <= horizon,
            Events.end_datetime >= local_now - timedelta(days=1),
        )
    ).all()
    for event in events:
        total = event_days(event)
        for day in range(1, total + 1):
            starts = day_start(event, day)
            if local_now < starts <= horizon:
                result.push_reminders += _remind(session, event, day, total, starts, now)


def _remind(session: Session, event: Events, day: int, total: int, starts: datetime, now: datetime) -> int:
    registrations = session.execute(
        select(Submissions.id, Submissions.member_id)
        .join(Forms, Forms.id == Submissions.form_id)
        .where(
            Forms.event_id == event.id,
            Forms.form_type.in_(REGISTRATION_FORMS),
            Submissions.is_accepted == 1,
            Submissions.cancelled_at.is_(None),
            Submissions.submission_type != SubmissionsSubmissionType.PARTIAL,
        )
    ).all()
    if not registrations:
        return 0
    checked_in = _checked_in(session, event.id, starts.date())
    day_key = starts.date().isoformat()
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"reminder:{event.id}:{day_key}:{member_id}",
            cause={"submission_id": submission_id, "day": day_key, "starts": starts.isoformat()},
        )
        for submission_id, member_id in registrations
        if member_id not in checked_in
    ]
    return outbox.enqueue(
        session,
        PushKind.REMINDER,
        pushes,
        text=messages.reminder(event.name, event.location, starts, day, total),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.to_utc(starts),
        quiet_hours=False,
        now=now,
    )


def _checked_in(session: Session, event_id: int, day) -> set[int]:
    rows = session.execute(
        select(MembersLogs.member_id, MembersLogs.date)
        .join(Logs, Logs.id == MembersLogs.log_id)
        .where(Logs.event_id == event_id)
    ).all()
    return {member_id for member_id, logged in rows if _effective_day(logged) == day}


def enqueue_new_events(session: Session, now: datetime, result) -> None:
    """An event that became open in the last 48 hours and has not started: members with club news on."""
    if not config.push_enabled:
        return
    local_now = clock.to_riyadh(now)
    events = session.scalars(
        select(Events).where(
            Events.status == EventsStatus.OPEN,
            Events.location_type.in_(ATTENDED_LOCATIONS),
            Events.start_datetime > local_now,
        )
    ).all()
    fresh = [
        event for event in events if (opened := became_open_at(session, event)) and opened >= now - NEW_EVENT_WINDOW
    ]
    if not fresh:
        return
    readers = outbox.reachable(session, "news")
    for event in fresh:
        pushes = [Push(member_id=member_id, dedupe_key=f"new_event:{event.id}:{member_id}") for member_id in readers]
        result.push_new_events += outbox.enqueue(
            session,
            PushKind.NEW_EVENT,
            pushes,
            text=messages.new_event(event.name, event.location, event.start_datetime),
            url=links.event_url(event.id),
            thread_id=f"event-{event.id}",
            event_id=event.id,
            expires_at=clock.to_utc(event.start_datetime),
            now=now,
            reachable_ids=readers,
        )


def became_open_at(session: Session, event: Events) -> datetime | None:
    """When the event last became open: the newest history entry that set its status to open, else its creation.

    A draft opened later through ``PUT /events/{id}/status`` (or the edit form)
    has such an entry; an event created open (the pipeline's publish,
    ``POST /events/``) has none, and ``created_at`` is when it opened.
    """
    entries = session.scalars(
        select(EventHistory)
        .where(
            EventHistory.event_id == event.id,
            EventHistory.action.in_([EventHistoryAction.STATUS_CHANGED, EventHistoryAction.EDITED]),
        )
        .order_by(EventHistory.at.desc())
    ).all()
    for entry in entries:
        status = (entry.details or {}).get("status")
        if isinstance(status, list) and len(status) == 2 and status[1] == EventsStatus.OPEN.value:
            return entry.at
    return event.created_at


def clean_up(session: Session, now: datetime, result) -> None:
    """Notification rows past 90 days, and phones not registered again for 180 days."""
    result.push_cleaned += push_queries.delete_old(
        session, now, rows_after_days=KEEP_ROWS_DAYS, devices_after_days=KEEP_DEVICES_DAYS
    )


def _sweep_rule(rule: Callable[[Session, datetime, Any], None]) -> Callable[[Session, datetime, Any], None]:
    """A push rule that fails never stops the events pipeline's own rules in the same sweep."""

    @functools.wraps(rule)
    def run(session: Session, now: datetime, result) -> None:
        try:
            with session.begin_nested():
                rule(session, now, result)
        except Exception:
            logger.exception("Push sweep rule %s failed; the rest of the sweep went on", rule.__name__)

    return run


SWEEP_RULES = [_sweep_rule(rule) for rule in (enqueue_event_reminders, enqueue_new_events, clean_up)]


# ---------------------------------------------------------------- at delivery


def cause_gone(session: Session, row: PushNotifications) -> str | None:
    """Why this push should no longer go out, or None when its cause still holds."""
    cause = row.payload.get("cause") or {}
    if row.kind in (PushKind.REMINDER, PushKind.ACCEPTED):
        submission = session.get(Submissions, cause.get("submission_id"))
        if submission is None or submission.cancelled_at is not None or not submission.is_accepted:
            return "not_accepted"
        if row.kind == PushKind.ACCEPTED:
            if submission.submitted_at.isoformat() != cause.get("submitted_at"):
                return "registration_changed"
            return None
        event = session.get(Events, row.event_id)
        if event is None or event.status not in (EventsStatus.OPEN, EventsStatus.ACTIVE):
            return "event_closed"
        starts = [day_start(event, day).isoformat() for day in range(1, event_days(event) + 1)]
        return None if cause.get("starts") in starts else "event_changed"
    if row.kind == PushKind.POINTS:
        log_id = cause.get("log_id")
        dates = session.scalars(
            select(MembersLogs.date).where(MembersLogs.member_id == row.member_id, MembersLogs.log_id == log_id)
        ).all()
        if cause.get("type") == "custom":
            return None if dates else "points_removed"
        return None if any(_effective_day(d).isoformat() == cause.get("day") for d in dates) else "points_removed"
    if row.kind == PushKind.NEW_EVENT:
        event = session.get(Events, row.event_id)
        return None if event is not None and event.status == EventsStatus.OPEN else "event_closed"
    return None
