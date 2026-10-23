"""The member app 1.0.3's pushes: eight kinds on top of the 1.0.2 ones in ``triggers``.

Route triggers run in the route's transaction, before its commit, like the
1.0.2 ones (``never_blocks``: a failure never stops the change itself); the
route kicks delivery after the commit:

- ``event_changed`` (``PUT /events/{id}``, ``/status``, ``/meeting-url``):
  ``event_updated`` when an open or active event's start, end, place or join
  link changes; ``event_cancelled`` when an open or active event that has not
  started is closed or set back to draft. There is no "cancelled" status:
  ``closed`` before the start, or back to ``draft``, is what cancelling looks
  like here (only a draft can be deleted).
- ``certificate_sent`` (the certificate e-mail jobs): per member, as each
  certificate e-mail goes out.
- ``content_added`` (content links, the Drive slides upload): slides or a
  recording published on an event; ``attendance_added`` (staff recording
  attendance) brings the last 7 days' content to a member recorded later.
- ``first_device`` (``POST /notifications/devices``): a member's first phone,
  within 7 days of their member record being created.

Sweep rules take the ``RULES`` signature ``(session, now, result)``:

- ``enqueue_event_live``: an online day has just started (an online event's,
  or a mixed pipeline event's online one).
- ``enqueue_registration_closing``: 24 hours before an open event starts. The
  database has no registration deadline: a ``registration`` or ``google`` form
  takes registrations while its event is ``open`` (the app reads leaving the
  open view as "registration closed"), and organizers move it on when it
  starts. So registration "closes" at the event's start at the latest, and
  that is the time this push names.
- ``enqueue_weekly_summaries``: Thursday 20:00 Riyadh, during a semester.

Every kind has one switch (``outbox.SWITCH_FOR``), one row per dedupe key
ever, quiet hours except ``event_live`` and ``event_cancelled``, and at most
``DAILY_CAP`` pushes of the capped kinds per member per Riyadh day
(``delivery``). ``cause_gone`` checks the cause again at delivery.
"""

import hashlib
import json
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.config import config
from app.DB.schema import (
    Actions,
    EmailLogs,
    EmailLogsEmailType,
    EventContentLinks,
    EventRequests,
    Events,
    EventsContentLinkKind,
    EventsLocationType,
    EventsStatus,
    Forms,
    Logs,
    Members,
    MembersLogs,
    Modifications,
    ModificationsType,
    PushKind,
    PushNotifications,
    PushStatus,
    Submissions,
    SubmissionsSubmissionType,
)
from app.DB.semesters import get_semester_started_by
from app.services import event_history
from app.services.push import clock, links, messages, outbox
from app.services.push.outbox import Push
from app.services.push.triggers import (
    ATTENDED_LOCATIONS,
    REGISTRATION_FORMS,
    _checked_in,
    _effective_day,
    _sweep_rule,
    became_open_at,
    day_start,
    event_days,
    never_blocks,
)

logger = logging.getLogger(__name__)

KINDS = frozenset(
    {
        PushKind.EVENT_UPDATED,
        PushKind.EVENT_CANCELLED,
        PushKind.EVENT_LIVE,
        PushKind.REGISTRATION_CLOSING,
        PushKind.CERTIFICATE,
        PushKind.EVENT_CONTENT,
        PushKind.WEEKLY_SUMMARY,
        PushKind.WELCOME,
    }
)
# Time-critical: sent at night too, and not counted against the daily cap.
URGENT = frozenset({PushKind.EVENT_LIVE, PushKind.EVENT_CANCELLED})
# At most this many pushes of these kinds a member gets in a Riyadh day; more are skipped.
CAPPED = KINDS - URGENT
DAILY_CAP = 3

PUBLIC = (EventsStatus.OPEN, EventsStatus.ACTIVE)
# What a registrant is told about when it changes. The edit form also sends the status.
WATCHED = ("start_datetime", "end_datetime", "location_type", "location", "meeting_url")

LIVE_WINDOW = timedelta(minutes=15)
CLOSING_LEAD = timedelta(hours=24)
# Sent within 6 hours after the 24-hour mark or not at all: a deploy or an outage never sends it late.
CLOSING_WINDOW = timedelta(hours=6)
SUMMARY_WEEKDAY = 3  # Thursday
SUMMARY_FROM = time(20, 0)
SUMMARY_UNTIL = clock.QUIET_FROM
SUMMARY_TTL = timedelta(hours=12)
CERTIFICATE_TTL = timedelta(days=7)
CONTENT_TTL = timedelta(days=7)
WELCOME_TTL = timedelta(days=3)
WELCOME_WITHIN = timedelta(days=7)
CONTENT_KINDS = (EventsContentLinkKind.SLIDES, EventsContentLinkKind.RECORDING)


# ---------------------------------------------------------------- shared


def _accepted(session: Session, event_id: int) -> list[tuple[int, int]]:
    """(submission id, member id) of the event's accepted, not cancelled registrations."""
    rows = session.execute(
        select(Submissions.id, Submissions.member_id)
        .join(Forms, Forms.id == Submissions.form_id)
        .where(
            Forms.event_id == event_id,
            Forms.form_type.in_(REGISTRATION_FORMS),
            Submissions.is_accepted == 1,
            Submissions.cancelled_at.is_(None),
            Submissions.submission_type != SubmissionsSubmissionType.PARTIAL,
        )
    ).all()
    return [(submission_id, member_id) for submission_id, member_id in rows]


def _still_accepted(session: Session, cause: dict) -> bool:
    submission = session.get(Submissions, cause.get("submission_id"))
    return submission is not None and submission.cancelled_at is None and bool(submission.is_accepted)


def event_state(event: Events) -> dict:
    """The status and the watched fields, as plain values: taken before a route changes the event."""
    return event_history.snapshot(event, ("status", *WATCHED))


def _digest(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _riyadh(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- route triggers


@never_blocks
def event_changed(session: Session, event: Events, before: dict) -> int:
    """An organizer changed an event; ``before`` is ``event_state`` from before the change.

    Returns how many pushes were queued. A request retried with nothing left to
    change finds no change and queues nothing.
    """
    if not config.push_enabled or before.get("status") not in {status.value for status in PUBLIC}:
        return 0
    after = event_state(event)
    now = clock.now()
    local_now = clock.to_riyadh(now)
    if after["status"] not in {status.value for status in PUBLIC}:
        planned = _riyadh(before["start_datetime"])
        if planned is None or planned <= local_now:
            return 0  # it has started (or ended): closing it now is not a cancellation
        return _cancelled(session, event, planned, after["status"], now)
    changed = {field: after[field] for field in WATCHED if before[field] != after[field]}
    if not changed or event.end_datetime <= local_now:
        return 0
    original = {field: before[field] for field in changed}
    return _updated(session, event, original, changed, now)


def _updated(session: Session, event: Events, original: dict, changed: dict, now: datetime) -> int:
    """One push per registrant that says everything not yet told: this change merged with their waiting ones.

    A registrant's earlier ``event_updated`` that has not gone out yet (held for
    quiet hours, never handed to Apple) is folded into this one and skipped as
    ``superseded``: the newest value of each field wins, and a field back at the
    value the registrant last heard of drops out (all of them back: no push).
    Each change has its own key (the fields' values before and after, and when),
    so a later change back to an earlier value is told again.
    """
    registrations = _accepted(session, event.id)
    if not registrations:
        return 0
    waiting: dict[int, list[PushNotifications]] = {}
    for row in session.scalars(
        select(PushNotifications)
        .where(
            PushNotifications.kind == PushKind.EVENT_UPDATED,
            PushNotifications.event_id == event.id,
            PushNotifications.status == PushStatus.PENDING,
            PushNotifications.attempts == 0,
            PushNotifications.member_id.in_({member_id for _, member_id in registrations}),
        )
        .order_by(PushNotifications.created_at, PushNotifications.id)
        .with_for_update()
    ).all():
        waiting.setdefault(row.member_id, []).append(row)

    pushes: list[Push] = []
    superseded: list[PushNotifications] = []
    for submission_id, member_id in registrations:
        told, news = {}, {}
        for row in waiting.get(member_id, []):
            cause = row.payload.get("cause") or {}
            for field, value in (cause.get("from") or {}).items():
                told.setdefault(field, value)
            news.update(cause.get("changed") or {})
        for field, value in original.items():
            told.setdefault(field, value)
        news.update(changed)
        net = {field: value for field, value in news.items() if told.get(field) != value}
        if set(net) == {"meeting_url"} and not net["meeting_url"]:
            net = {}  # only the link was taken away: nothing to tell
        key = None
        if net:
            told = {field: told.get(field) for field in net}
            key = f"event_updated:{event.id}:{member_id}:{_digest({'from': told, 'to': net, 'at': clock.epoch(now)})}"
            pushes.append(
                Push(
                    member_id=member_id,
                    dedupe_key=key,
                    cause={"submission_id": submission_id, "from": told, "changed": net},
                )
            )
        superseded += [row for row in waiting.get(member_id, []) if row.dedupe_key != key]
    for row in superseded:
        row.status = PushStatus.SKIPPED
        row.last_error = "superseded"
    session.flush()

    def text(push: Push) -> messages.Text:
        net = (push.cause or {})["changed"]
        starts = "start_datetime" in net
        return messages.event_updated(
            event.name,
            starts=event.start_datetime if starts else None,
            ends=event.end_datetime if "end_datetime" in net and not starts else None,
            location=event.location if {"location", "location_type"} & set(net) else None,
            new_link=bool(net.get("meeting_url")),
        )

    return outbox.enqueue(
        session,
        PushKind.EVENT_UPDATED,
        pushes,
        text=text,
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.to_utc(event.end_datetime),
        now=now,
    )


def _cancelled(session: Session, event: Events, planned: datetime, status: str, now: datetime) -> int:
    """Keyed by the moment of the cancellation: one undone before delivery leaves the next one free to go."""
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"event_cancelled:{event.id}:{member_id}:{clock.epoch(now)}",
            cause={"submission_id": submission_id, "status": status},
        )
        for submission_id, member_id in _accepted(session, event.id)
    ]
    return outbox.enqueue(
        session,
        PushKind.EVENT_CANCELLED,
        pushes,
        text=messages.event_cancelled(event.name, planned),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.to_utc(planned),
        quiet_hours=False,
        now=now,
    )


@never_blocks
def certificate_sent(session: Session, event_id: int | None, member_id: int | None) -> int:
    """A certificate e-mail went to a member. Manual certificates for no event, or for no member, push nothing."""
    if not config.push_enabled or event_id is None or member_id is None:
        return 0
    event = session.get(Events, event_id)
    if event is None:
        return 0
    return outbox.enqueue(
        session,
        PushKind.CERTIFICATE,
        [Push(member_id=member_id, dedupe_key=f"certificate:{event_id}:{member_id}", cause={"event_id": event_id})],
        text=messages.certificate(event.name),
        url=links.CERTIFICATES_URL,
        thread_id=f"event-{event_id}",
        event_id=event_id,
        expires_at=clock.now() + CERTIFICATE_TTL,
    )


@never_blocks
def content_added(session: Session, link: EventContentLinks) -> int:
    """Slides or a recording became visible on an event: the members who attended it."""
    if not config.push_enabled or not link.published or link.kind not in CONTENT_KINDS:
        return 0
    event = session.get(Events, link.event_id)
    if event is None:
        return 0
    attendees = session.scalars(
        select(MembersLogs.member_id)
        .distinct()
        .join(Logs, Logs.id == MembersLogs.log_id)
        .where(Logs.event_id == event.id)
    ).all()
    return _content(session, event, link, attendees)


@never_blocks
def attendance_added(session: Session, event: Events, member_id: int) -> int:
    """Staff recorded a member's attendance after slides or a recording went up: they hear of them too.

    Called next to ``triggers.attendance_recorded`` (scan, manual, import). Only
    content added in the last 7 days: a member added to an old event's
    attendance is not told about its slides weeks later. The keys are
    ``content_added``'s, so nobody hears of one link twice.
    """
    if not config.push_enabled:
        return 0
    added = session.scalars(
        select(EventContentLinks)
        .where(
            EventContentLinks.event_id == event.id,
            EventContentLinks.published == 1,
            EventContentLinks.kind.in_(CONTENT_KINDS),
            EventContentLinks.created_at >= clock.now() - CONTENT_TTL,
        )
        .order_by(EventContentLinks.created_at, EventContentLinks.id)
    ).all()
    return sum(_content(session, event, link, [member_id]) for link in added)


def _content(session: Session, event: Events, link: EventContentLinks, member_ids) -> int:
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"event_content:{event.id}:{link.id}:{member_id}",
            cause={"link_id": link.id},
        )
        for member_id in member_ids
    ]
    return outbox.enqueue(
        session,
        PushKind.EVENT_CONTENT,
        pushes,
        text=messages.event_content(event.name, link.kind.value, link.label),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.now() + CONTENT_TTL,
    )


@never_blocks
def first_device(session: Session, member: Members, *, had_device: bool) -> int:
    """The member's first phone, within 7 days of joining. Call it after the phone's row is written.

    "Joining" is the member record's creation (``members.created_at``, the
    process clock: a few hours either way do not matter here). A member who
    linked an older record at onboarding is not new and gets no welcome.
    """
    if not config.push_enabled or had_device or member.created_at is None:
        return 0
    now = clock.now()
    if now - member.created_at > WELCOME_WITHIN:
        return 0
    return outbox.enqueue(
        session,
        PushKind.WELCOME,
        [Push(member_id=member.id, dedupe_key=f"welcome:{member.id}")],
        text=messages.welcome(),
        url=links.DEFAULT_URL,
        thread_id="news",
        expires_at=now + WELCOME_TTL,
        now=now,
    )


# ---------------------------------------------------------------- sweep rules


def enqueue_event_live(session: Session, now: datetime, result) -> None:
    """An online day started in the last 15 minutes: accepted registrants not checked in that day.

    Online days are every day of an online event, and the online days of a
    pipeline event that mixes the two: it is published on-site with the Meet
    link, and its request's ``day_modes`` says which days are online.
    """
    if not config.push_enabled:
        return
    local_now = clock.to_riyadh(now)
    rows = session.execute(
        select(Events, EventRequests.day_modes)
        .outerjoin(EventRequests, EventRequests.event_id == Events.id)
        .where(
            Events.status.in_(PUBLIC),
            or_(
                Events.location_type == EventsLocationType.ONLINE,
                and_(Events.location_type == EventsLocationType.ON_SITE, Events.meeting_url.is_not(None)),
            ),
            Events.start_datetime <= local_now,
            Events.end_datetime >= local_now - timedelta(days=1),
        )
    ).all()
    for event, day_modes in rows:
        total = event_days(event)
        for day in range(1, total + 1):
            starts = day_start(event, day)
            if local_now - LIVE_WINDOW < starts <= local_now and _online_on(event, day_modes, starts):
                result.push_event_live += _live(session, event, day, total, starts, now)


def _online_on(event: Events, day_modes: dict | None, starts: datetime) -> bool:
    if event.location_type == EventsLocationType.ONLINE:
        return True
    return isinstance(day_modes, dict) and day_modes.get(starts.date().isoformat()) == "online"


def _day_end(event: Events, starts: datetime) -> datetime:
    """That day's end: its date at the event's end time, the next day for an overnight one. Riyadh wall clock."""
    ends = datetime.combine(starts.date(), event.end_datetime.time())
    return ends if ends > starts else ends + timedelta(days=1)


def _live(session: Session, event: Events, day: int, total: int, starts: datetime, now: datetime) -> int:
    registrations = _accepted(session, event.id)
    if not registrations:
        return 0
    checked_in = _checked_in(session, event.id, starts.date())
    day_key = starts.date().isoformat()
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"event_live:{event.id}:{day_key}:{member_id}",
            cause={"submission_id": submission_id, "day": day_key, "starts": starts.isoformat()},
        )
        for submission_id, member_id in registrations
        if member_id not in checked_in
    ]
    return outbox.enqueue(
        session,
        PushKind.EVENT_LIVE,
        pushes,
        text=messages.event_live(event.name, day, total),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.to_utc(_day_end(event, starts)),
        quiet_hours=False,
        now=now,
    )


def enqueue_registration_closing(session: Session, now: datetime, result) -> None:
    """24 hours before an open event with registration starts: members with club news on who have not registered.

    Only an event that was already open at the 24-hour mark: one opened later
    has just been announced (``new_event``).
    """
    if not config.push_enabled:
        return
    local_now = clock.to_riyadh(now)
    rows = session.execute(
        select(Events, Forms.id)
        .join(Forms, Forms.event_id == Events.id)
        .where(
            Events.status == EventsStatus.OPEN,
            Events.location_type.in_(ATTENDED_LOCATIONS),
            Forms.form_type.in_(REGISTRATION_FORMS),
            Events.start_datetime > local_now + CLOSING_LEAD - CLOSING_WINDOW,
            Events.start_datetime <= local_now + CLOSING_LEAD,
        )
    ).all()
    readers: set[int] | None = None
    for event, form_id in rows:
        mark = clock.to_utc(event.start_datetime - CLOSING_LEAD)
        opened = became_open_at(session, event)
        if opened is None or opened > mark:
            continue
        if readers is None:
            readers = outbox.reachable(session, "news")
        # Any row counts as registered: cancelled (they chose to) and partial (a Google Form response on its way).
        registered = set(session.scalars(select(Submissions.member_id).where(Submissions.form_id == form_id)).all())
        starts = event.start_datetime.isoformat()
        pushes = [
            Push(
                member_id=member_id,
                dedupe_key=f"registration_closing:{event.id}:{member_id}",
                cause={"form_id": form_id, "starts": starts},
            )
            for member_id in sorted(readers - registered)
        ]
        result.push_registration_closing += outbox.enqueue(
            session,
            PushKind.REGISTRATION_CLOSING,
            pushes,
            text=messages.registration_closing(event.name, event.location, event.start_datetime),
            url=links.event_url(event.id),
            thread_id=f"event-{event.id}",
            event_id=event.id,
            expires_at=clock.to_utc(event.start_datetime),
            now=now,
            reachable_ids=readers,
        )


def enqueue_weekly_summaries(session: Session, now: datetime, result) -> None:
    """Thursday 20:00-22:00 Riyadh, while a semester runs: each member with points this semester, their week."""
    if not config.push_enabled:
        return
    local = clock.to_riyadh(now)
    if local.weekday() != SUMMARY_WEEKDAY or not SUMMARY_FROM <= local.time() < SUMMARY_UNTIL:
        return
    semester = get_semester_started_by(session, local.date())
    if semester is None or local.date() > semester.end_date:
        return
    readers = outbox.reachable(session, "points")
    if not readers:
        return
    year, week, _ = local.date().isocalendar()
    week_key = f"{year}-W{week:02d}"
    points = semester_points(session, semester.id, readers, week_from=local.date() - timedelta(days=6))
    pushes = [
        Push(member_id=member_id, dedupe_key=f"weekly_summary:{week_key}:{member_id}", cause={"week": week_key})
        for member_id, (total, _) in sorted(points.items())
        if total > 0
    ]
    result.push_weekly_summaries += outbox.enqueue(
        session,
        PushKind.WEEKLY_SUMMARY,
        pushes,
        text=lambda push: messages.weekly_summary(points[push.member_id][1], points[push.member_id][0]),
        url=links.POINTS_URL,
        thread_id="points",
        expires_at=now + SUMMARY_TTL,
        now=now,
        reachable_ids=readers,
    )


def semester_points(
    session: Session, semester_id: str, member_ids: set[int], *, week_from
) -> dict[int, tuple[int, int]]:
    """{member: (points this semester, points earned from ``week_from`` on)}.

    The semester total is the leaderboard's (``app/DB/points.py``): the
    action's points plus the log's bonuses and discounts, on events of the
    semester that are not drafts. When points were earned is ``earned_on``.
    """
    if not member_ids:
        return {}
    modifiers = (
        select(
            Modifications.log_id,
            func.sum(
                case(
                    (
                        Modifications.type == ModificationsType.DISCOUNT,
                        -func.abs(func.coalesce(Modifications.value, 0)),
                    ),
                    (Modifications.type == ModificationsType.BONUS, func.abs(func.coalesce(Modifications.value, 0))),
                    else_=func.coalesce(Modifications.value, 0),
                )
            ).label("value"),
        )
        .group_by(Modifications.log_id)
        .subquery()
    )
    rows = session.execute(
        select(
            MembersLogs.member_id,
            MembersLogs.date,
            Events.end_datetime,
            func.coalesce(Actions.points, 0) + func.coalesce(modifiers.c.value, 0),
        )
        .join(Logs, Logs.id == MembersLogs.log_id)
        .join(Events, Events.id == Logs.event_id)
        .outerjoin(Actions, Actions.id == Logs.action_id)
        .outerjoin(modifiers, modifiers.c.log_id == Logs.id)
        .where(
            Events.semester_id == semester_id,
            Events.status != EventsStatus.DRAFT,
            MembersLogs.member_id.in_(member_ids),
        )
    ).all()
    found: dict[int, tuple[int, int]] = {}
    for member_id, logged, event_ends, value in rows:
        total, week = found.get(member_id, (0, 0))
        value = int(value or 0)
        found[member_id] = (total + value, week + (value if earned_on(logged, event_ends) >= week_from else 0))
    return found


def earned_on(logged: datetime, event_ends: datetime) -> date:
    """The day points were earned: the attendance row's effective day, never after its event's last day.

    An attendance row is written on the day it records. A custom-points row is
    rewritten whenever staff edit the entry (``PUT /custom/members/{log_id}``
    deletes and recreates them), so its own date can be much later than the
    event the points are for; the event's last day bounds it.
    """
    return min(_effective_day(logged), event_ends.date())


SWEEP_RULES = [
    _sweep_rule(rule) for rule in (enqueue_event_live, enqueue_registration_closing, enqueue_weekly_summaries)
]


# ---------------------------------------------------------------- at delivery


def cause_gone(session: Session, row: PushNotifications) -> str | None:
    """Why this push should no longer go out, or None when its cause still holds."""
    cause = row.payload.get("cause") or {}
    event = session.get(Events, row.event_id) if row.event_id is not None else None
    if row.kind in (PushKind.EVENT_UPDATED, PushKind.EVENT_LIVE):
        if not _still_accepted(session, cause):
            return "not_accepted"
        if event is None or event.status not in PUBLIC:
            return "event_closed"
        if row.kind == PushKind.EVENT_UPDATED:
            now = event_state(event)
            return None if all(now[f] == v for f, v in (cause.get("changed") or {}).items()) else "event_changed"
        starts = [day_start(event, day).isoformat() for day in range(1, event_days(event) + 1)]
        if cause.get("starts") not in starts:
            return "event_changed"
        day = datetime.fromisoformat(cause["starts"]).date()
        return "checked_in" if row.member_id in _checked_in(session, event.id, day) else None
    if row.kind == PushKind.EVENT_CANCELLED:
        if not _still_accepted(session, cause):
            return "not_accepted"
        return "event_reopened" if event is not None and event.status in PUBLIC else None
    if row.kind == PushKind.REGISTRATION_CLOSING:
        if event is None or event.status != EventsStatus.OPEN:
            return "event_closed"
        if event.start_datetime.isoformat() != cause.get("starts"):
            return "event_changed"
        registered = session.scalar(
            select(Submissions.id).where(
                Submissions.form_id == cause.get("form_id"), Submissions.member_id == row.member_id
            )
        )
        return "registered" if registered is not None else None
    if row.kind == PushKind.CERTIFICATE:
        sent = session.scalar(
            select(EmailLogs.id)
            .where(
                EmailLogs.member_id == row.member_id,
                EmailLogs.event_id == row.event_id,
                EmailLogs.email_type.in_([EmailLogsEmailType.EVENT_CERTIFICATE, EmailLogsEmailType.MANUAL_CERTIFICATE]),
            )
            .limit(1)
        )
        return None if sent is not None else "certificate_removed"
    if row.kind == PushKind.EVENT_CONTENT:
        link = session.get(EventContentLinks, cause.get("link_id"))
        return None if link is not None and link.published else "content_removed"
    return None
