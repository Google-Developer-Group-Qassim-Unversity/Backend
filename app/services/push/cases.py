"""The member app 1.0.3's pushes: eight kinds on top of the 1.0.2 ones in ``triggers``.

Route triggers run in the route's transaction, before its commit, like the
1.0.2 ones (``never_blocks``: a failure never stops the change itself); the
route kicks delivery after the commit:

- ``event_changed`` (``PUT /events/{id}``, ``/status``, ``/meeting-url``):
  ``event_updated`` when an open or active event's start, end, place or join
  link changes; ``event_cancelled`` when an open or active event that has not
  started is closed or set back to draft. There is no "cancelled" status:
  ``closed`` before the start, or back to ``draft``, is what cancelling looks
  like here (only a draft can be deleted). One row per registrant per change
  (keyed by its ``event_history`` row), held back 2 minutes; delivery sends
  only the newest and states the event as it is then.
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
ever, and quiet hours except ``event_live``, ``event_cancelled`` and an
``event_updated`` "Back on". At most ``DAILY_CAP`` pushes of the ``CAPPED``
kinds (registration_closing, certificate, event_content, weekly_summary,
welcome) reach a member per Riyadh day (``delivery``); a change to an event
they registered for is never capped. ``cause_gone`` checks the cause again at
delivery.
"""

import logging
from datetime import datetime, time, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

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
    PipelineTeam,
    PushKind,
    PushNotifications,
    PushStatus,
    Submissions,
    SubmissionsSubmissionType,
)
from app.DB.semesters import get_semester_started_by
from app.services import event_deliverables, event_history
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
# A real change to an event the member registered for is never dropped by the cap.
CAPPED = KINDS - URGENT - {PushKind.EVENT_UPDATED}
DAILY_CAP = 3
# One push per registrant and event: delivery sends only the newest row of these (``_superseded``).
NEWEST_ONLY = frozenset({PushKind.EVENT_UPDATED, PushKind.EVENT_CANCELLED})

PUBLIC = (EventsStatus.OPEN, EventsStatus.ACTIVE)
PUBLIC_VALUES = frozenset(status.value for status in PUBLIC)
# What a registrant is told about when it changes. The edit form also sends the status.
WATCHED = ("start_datetime", "end_datetime", "location_type", "location", "meeting_url")
# event_updated and event_cancelled wait this long, so quick edits (and a double submit) end as one push.
DEBOUNCE = timedelta(minutes=2)
# An ordinary update waits out quiet hours only for an event that starts this long after they end (09:00).
QUIET_MARGIN = timedelta(hours=1)

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


def _riyadh(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ---------------------------------------------------------------- route triggers


@never_blocks
def event_changed(session: Session, event: Events, before: dict, change_id: str | None) -> int:
    """An organizer changed an event; ``before`` is ``event_state`` from before the change.

    ``change_id`` is the ``event_history`` row the route recorded for the change
    (None: nothing changed); it keys the pushes, one per registrant per change.
    Only events the app lists (online, on-site) push; one moved to hidden or
    none sends nothing.

    - Public (open, active) and not started -> closed or draft: ``event_cancelled``,
      due at once.
    - Closed or draft -> public, not ended: ``event_updated`` "Back on" to the
      registrants who were sent (or are being sent) its cancellation; urgent like
      a cancellation: due at once, not held for quiet hours, not capped. The other
      registrants get an ordinary update when its time, place or link moved while
      it was closed.
    - Public, not ended, a watched field changed: ``event_updated``, held back
      ``DEBOUNCE``.

    Delivery sends only a registrant's newest row of a kind for the event and
    says the event as it is then (``cause_gone``).
    """
    if not config.push_enabled or change_id is None or event.location_type not in ATTENDED_LOCATIONS:
        return 0
    after = event_state(event)
    was_public, is_public = before.get("status") in PUBLIC_VALUES, after["status"] in PUBLIC_VALUES
    now = clock.now()
    local_now = clock.to_riyadh(now)
    if was_public and not is_public:
        planned = _riyadh(before["start_datetime"])
        if planned is None or planned <= local_now:
            return 0  # it has started (or ended): closing it now is not a cancellation
        return _cancelled(session, event, change_id, before, planned, now)
    if not is_public or event.end_datetime <= local_now:
        return 0
    fields = sorted(field for field in WATCHED if before[field] != after[field])
    if not was_public:
        # Back on (also after the start: closed by mistake at 17:59, reopened at 18:00), to those told.
        return _updated(session, event, change_id, fields, before, now, back_on=True)
    if not fields or (fields == ["meeting_url"] and not after["meeting_url"]):
        return 0  # nothing watched changed, or only the link was taken away
    return _updated(session, event, change_id, fields, before, now, back_on=False)


def _updated(
    session: Session, event: Events, change_id: str, fields: list[str], before: dict, now: datetime, *, back_on: bool
) -> int:
    registrations = _accepted(session, event.id)
    local_now = clock.to_riyadh(now)
    if not back_on:
        known = _known_start([before], local_now)
        return _queue_updates(session, event, change_id, registrations, fields, known, now, back_on=False)
    # The ones told it was cancelled, or about to be (a cancellation skipped as event_reopened just
    # means one extra, truthful Back on), hear it is on again.
    cancellations = session.scalars(
        select(PushNotifications)
        .where(
            PushNotifications.kind == PushKind.EVENT_CANCELLED,
            PushNotifications.event_id == event.id,
            PushNotifications.status.in_([PushStatus.SENT, PushStatus.PENDING]),
        )
        .order_by(PushNotifications.id)
    ).all()
    told = {row.member_id for row in cancellations}
    known_states = [before]
    # What changed while it was closed (a new link, a new time) counts too: against the event as it was cancelled.
    if cancellations:
        as_cancelled = (cancellations[-1].payload.get("cause") or {}).get("state") or {}
        after = event_state(event)
        fields = sorted(set(fields) | {f for f in WATCHED if f in as_cancelled and as_cancelled[f] != after[f]})
        # The others knew it as it was cancelled too, not only as ``before`` has it (moved while closed, say).
        known_states.append(as_cancelled)
    known = _known_start(known_states, local_now)
    queued = _queue_updates(
        session, event, change_id, [(s, m) for s, m in registrations if m in told], fields, known, now, back_on=True
    )
    # The others never heard it was cancelled; what moved while it was closed is an ordinary update to them.
    if fields and not (fields == ["meeting_url"] and not event.meeting_url):
        others = [(s, m) for s, m in registrations if m not in told]
        queued += _queue_updates(session, event, change_id, others, fields, known, now, back_on=False)
    return queued


def _known_start(states: list[dict], local_now: datetime) -> datetime | None:
    """The start of the next day registrants knew, if still ahead; the earliest when they may know more than one.

    ``states`` are ``event_state``s; each one's next day is found as ``_next_day`` finds the event's. A day
    that has started does not count, nor does a state that ends before it starts (it has no days): the create
    form lets one through, only the edit form refuses it.
    """
    starts = []
    for state in states:
        start, end = _riyadh(state.get("start_datetime")), _riyadh(state.get("end_datetime"))
        if start is None or end is None or end < start:
            continue
        _, _, day_starts, _ = _next_day(start, end, local_now)
        if day_starts > local_now:
            starts.append(day_starts)
    return min(starts, default=None)


def _queue_updates(
    session: Session,
    event: Events,
    change_id: str,
    registrations: list[tuple[int, int]],
    fields: list[str],
    known: datetime | None,
    now: datetime,
    *,
    back_on: bool,
) -> int:
    cause = {"fields": fields, "back_on": back_on}
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"event_updated:{event.id}:{member_id}:{change_id}",
            cause={"submission_id": submission_id, **cause},
        )
        for submission_id, member_id in registrations
    ]
    if not pushes:
        return 0
    local_now = clock.to_riyadh(now)
    _, _, starts, _ = _next_day(event.start_datetime, event.end_datetime, local_now)
    # The next day registrants knew counts too (``known``): moved at 23:00 from 07:30 to 10:00 tomorrow,
    # they hear it now.
    if known is not None:
        starts = min(starts, known)
    # Quiet hours hold an ordinary update only when it can wait: its next day (and the one registrants
    # knew) starts at 09:00 or later after the quiet hours end. An event tonight, under way, or early
    # tomorrow is told now.
    release = clock.after_quiet_hours(now + DEBOUNCE)
    can_wait = clock.to_utc(starts) >= release + QUIET_MARGIN
    return outbox.enqueue(
        session,
        PushKind.EVENT_UPDATED,
        pushes,
        text=_update_text(event, cause, local_now),
        url=links.event_url(event.id),
        thread_id=f"event-{event.id}",
        event_id=event.id,
        expires_at=clock.to_utc(event.end_datetime),
        now=now,
        # "Back on" is urgent, like the cancellation it corrects: due at once, at night too.
        quiet_hours=not back_on and can_wait,
        delay=timedelta(0) if back_on else DEBOUNCE,
    )


def is_back_on(row: PushNotifications) -> bool:
    return row.kind == PushKind.EVENT_UPDATED and bool((row.payload.get("cause") or {}).get("back_on"))


def counts_toward_cap(kind: PushKind) -> bool:
    """Whether a push of ``kind`` counts toward (and is held to) the daily cap: never an event_updated."""
    return kind in CAPPED


def _folded(session: Session, row: PushNotifications) -> tuple[bool, set[str]]:
    """What the older rows this one stands for said: whether one was a "Back on", and the fields they changed.

    The older rows are the ones superseded (or about to be) since the last one sent, and the ones
    skipped because the event was closed or hidden at the time: a link changed just before a close
    still says "New link to join" in the "Back on".
    """
    same = (
        PushNotifications.kind == row.kind,
        PushNotifications.event_id == row.event_id,
        PushNotifications.member_id == row.member_id,
    )
    last_sent = session.scalar(
        select(func.max(PushNotifications.id)).where(*same, PushNotifications.status == PushStatus.SENT)
    )
    older = select(PushNotifications).where(
        *same,
        PushNotifications.id < row.id,
        or_(
            PushNotifications.status == PushStatus.PENDING,
            and_(
                PushNotifications.status == PushStatus.SKIPPED,
                PushNotifications.last_error.in_(["superseded", "event_closed", "event_hidden"]),
            ),
        ),
    )
    if last_sent is not None:
        older = older.where(PushNotifications.id > last_sent)
    back_on, fields = False, set()
    for other in session.scalars(older).all():
        back_on = back_on or is_back_on(other)
        fields |= set((other.payload.get("cause") or {}).get("fields") or [])
    return back_on, fields


def _cancelled(session: Session, event: Events, change_id: str, before: dict, planned: datetime, now: datetime) -> int:
    """Due at once (not debounced), so deleting the draft right after cannot swallow it; kicked by the route.

    ``state``: the event as registrants knew it, so a "Back on" can tell what changed while it was closed.
    """
    pushes = [
        Push(
            member_id=member_id,
            dedupe_key=f"event_cancelled:{event.id}:{member_id}:{change_id}",
            cause={"submission_id": submission_id, "starts": planned.isoformat(), "state": before},
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


def _next_day(start: datetime, end: datetime, local_now: datetime) -> tuple[int, int, datetime, datetime]:
    """(day, days, its start, its end) of the first day that has not ended; the last day when all have.

    Of an event from ``start`` to ``end`` (Riyadh wall clock), days counted as ``event_days`` and ``day_start``
    count them: the event as it is, or as registrants knew it (``_known_start``).
    """
    total = (end - start).days + 1
    for day in range(1, total + 1):
        starts = start + timedelta(days=day - 1)
        ends = _day_end(starts, end)
        if ends > local_now or day == total:
            return day, total, starts, ends
    raise AssertionError("an event has at least one day")


def _update_text(event: Events, cause: dict, local_now: datetime) -> messages.Text:
    """The event as it is: its next day's date and time and its place (or Online), never a diff."""
    day, total, starts, ends = _next_day(event.start_datetime, event.end_datetime, local_now)
    return messages.event_updated(
        event.name,
        starts,
        ends,
        event.location,
        day=day,
        total_days=total,
        online=event.location_type == EventsLocationType.ONLINE,
        new_link="meeting_url" in (cause.get("fields") or []) and bool(event.meeting_url),
        back_on=bool(cause.get("back_on")),
    )


@never_blocks
def event_deleted(session: Session, event_id: int) -> int:
    """A draft is about to be deleted: its cancellations still waiting are kept and sent.

    The foreign key would delete them with the event. Each registrant's newest
    one is detached (``event_id`` NULL, opens Notifications) and goes out as it
    is; older ones are ``superseded``. Call before deleting; kick after the commit.
    """
    if not config.push_enabled:
        return 0
    rows = session.scalars(
        select(PushNotifications)
        .where(
            PushNotifications.kind == PushKind.EVENT_CANCELLED,
            PushNotifications.event_id == event_id,
            PushNotifications.status == PushStatus.PENDING,
        )
        .order_by(PushNotifications.member_id, PushNotifications.id.desc())
    ).all()
    kept: set[int] = set()
    for row in rows:
        if row.member_id in kept:
            row.status, row.last_error = PushStatus.SKIPPED, "superseded"
            continue
        kept.add(row.member_id)
        row.event_id = None
        row.payload = {
            **row.payload,
            "url": links.DEFAULT_URL,
            "cause": {**(row.payload.get("cause") or {}), "deleted": True},
        }
    session.flush()
    return len(kept)


def _superseded(session: Session, row: PushNotifications) -> bool:
    """A newer row of the same kind for this member and event went out, is going out, or is due about as soon.

    A newer one held much longer (by quiet hours, say) does not stop this one:
    this one goes, said as the event is now.
    """
    if row.event_id is None:
        return False
    newer = session.scalar(
        select(PushNotifications.id)
        .where(
            PushNotifications.kind == row.kind,
            PushNotifications.event_id == row.event_id,
            PushNotifications.member_id == row.member_id,
            # Ids are time-ordered (UUIDv7).
            PushNotifications.id > row.id,
            or_(
                PushNotifications.status == PushStatus.SENT,
                and_(
                    PushNotifications.status == PushStatus.PENDING,
                    or_(
                        PushNotifications.attempts > 0,  # claimed: going out now (this batch or another worker's)
                        PushNotifications.not_before <= row.not_before + DEBOUNCE,
                    ),
                ),
            ),
        )
        .limit(1)
    )
    return newer is not None


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
def attendance_added(session: Session, event: Events, member_ids) -> int:
    """Staff recorded attendance after slides or a recording went up: those members hear of them too.

    Called once per request, after ``triggers.attendance_recorded`` (scan,
    manual, import). Only content published in the last 7 days
    (``published_at``): a member added to an old event's attendance is not
    told about its slides weeks later. The keys are ``content_added``'s, so
    nobody hears of one link twice.
    """
    member_ids = list(dict.fromkeys(member_ids))
    if not config.push_enabled or not member_ids:
        return 0
    published = session.scalars(
        select(EventContentLinks)
        .where(
            EventContentLinks.event_id == event.id,
            EventContentLinks.published == 1,
            EventContentLinks.kind.in_(CONTENT_KINDS),
            EventContentLinks.published_at >= clock.now() - CONTENT_TTL,
        )
        .order_by(EventContentLinks.published_at, EventContentLinks.id)
    ).all()
    return sum(_content(session, event, link, member_ids) for link in published)


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
    pipeline event that mixes the two (``online_days``) while it still has its
    Meet link.
    """
    if not config.push_enabled:
        return
    local_now = clock.to_riyadh(now)
    events = session.scalars(
        select(Events).where(
            Events.status.in_(PUBLIC),
            Events.location_type.in_(ATTENDED_LOCATIONS),
            Events.start_datetime <= local_now,
            Events.end_datetime >= local_now - timedelta(days=1),
        )
    ).all()
    mixed = online_days(session, [e for e in events if e.location_type == EventsLocationType.ON_SITE])
    for event in events:
        total = event_days(event)
        for day in range(1, total + 1):
            starts = day_start(event, day)
            # A mixed event's online day needs its Meet link: an edit clears it (update_event).
            online = event.location_type == EventsLocationType.ONLINE or (
                day in mixed.get(event.id, ()) and bool(event.meeting_url)
            )
            if online and local_now - LIVE_WINDOW < starts <= local_now:
                result.push_event_live += _live(session, event, day, total, starts, now)


def online_days(session: Session, events) -> dict[int, set[int]]:
    """{event: its online day numbers (1-based)} for on-site events a pipeline request published with online days.

    The pipeline publishes a mix of on-site and online days as one on-site event
    with the Meet link (``event_pipeline``); which days are online is in what
    Logistics confirmed, the same source the event was published from. Days are
    counted from the confirmed start, so moving the event's dates afterwards
    (``PUT /events/{id}``) keeps them; a day the confirmation never had is
    on-site.
    """
    ids = [event.id for event in events]
    if not ids:
        return {}
    found: dict[int, set[int]] = {}
    requests = session.scalars(
        select(EventRequests).where(EventRequests.event_id.in_(ids)).options(selectinload(EventRequests.tasks))
    ).all()
    for request in requests:
        task = next((task for task in request.tasks if task.team == PipelineTeam.LOGISTICS), None)
        confirmed = event_deliverables.logistics_confirmation(request, task)
        if confirmed is None or not confirmed.day_modes or request.event_id is None:
            continue
        modes = confirmed.day_modes
        first = confirmed.start_date or min(modes)
        days = {(day - first).days + 1 for day, mode in modes.items() if getattr(mode, "value", mode) == "online"}
        if days:
            found[request.event_id] = days
    return found


def _day_end(starts: datetime, end: datetime) -> datetime:
    """That day's end: its date at the event's end time, the next day for an overnight one. Riyadh wall clock."""
    ends = datetime.combine(starts.date(), end.time())
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
        expires_at=clock.to_utc(_day_end(starts, event.end_datetime)),
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
    semester that are not drafts. Points count for the day their
    ``members_logs`` row was written, read as attendance reads it: attendance on
    the day it records, custom points when they were given. Editing a
    custom-points entry keeps the rows of the members who stay on it
    (``app/DB/logs.py`` ``set_member_logs``), so an edit never moves points into
    this week.
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
    for member_id, logged, value in rows:
        total, week = found.get(member_id, (0, 0))
        value = int(value or 0)
        found[member_id] = (total + value, week + (value if _effective_day(logged) >= week_from else 0))
    return found


SWEEP_RULES = [
    _sweep_rule(rule) for rule in (enqueue_event_live, enqueue_registration_closing, enqueue_weekly_summaries)
]


# ---------------------------------------------------------------- at delivery


def cause_gone(session: Session, row: PushNotifications) -> str | None:
    """Why this push should no longer go out, or None when its cause still holds.

    ``event_updated`` and ``event_cancelled``: only a registrant's newest row
    goes (older ones are ``superseded``), and its text is written again here
    from the event as it is now (``row.payload``); a cancellation names the
    start that was cancelled.
    """
    cause = row.payload.get("cause") or {}
    event = session.get(Events, row.event_id) if row.event_id is not None else None
    if row.kind == PushKind.EVENT_UPDATED:
        if _superseded(session, row):
            return "superseded"
        if not _still_accepted(session, cause):
            return "not_accepted"
        if event is None or event.status not in PUBLIC:
            return "event_closed"
        if event.location_type not in ATTENDED_LOCATIONS:
            return "event_hidden"
        local_now = clock.to_riyadh(clock.now())
        if event.end_datetime <= local_now:
            return "event_over"
        # It stands for the rows it superseded: a "Back on" among them makes it one (urgent too), and their
        # changed fields count (a new link one of them was about still says "New link to join").
        back_on, fields = _folded(session, row)
        cause = {
            **cause,
            "back_on": bool(cause.get("back_on")) or back_on,
            "fields": sorted(set(cause.get("fields") or []) | fields),
        }
        row.payload = {**row.payload, **_update_text(event, cause, local_now), "cause": cause}
        return None
    if row.kind == PushKind.EVENT_CANCELLED:
        if _superseded(session, row):
            return "superseded"
        planned = _riyadh(cause.get("starts"))
        if row.event_id is None:
            return None  # the draft was deleted after it (event_deleted): it goes as written
        if not _still_accepted(session, cause):
            return "not_accepted"
        if event is None:
            return "event_closed"
        if event.status in PUBLIC:
            return "event_reopened"
        row.payload = {**row.payload, **messages.event_cancelled(event.name, planned or event.start_datetime)}
        return None
    if row.kind == PushKind.EVENT_LIVE:
        if not _still_accepted(session, cause):
            return "not_accepted"
        if event is None or event.status not in PUBLIC:
            return "event_closed"
        starts = [day_start(event, day).isoformat() for day in range(1, event_days(event) + 1)]
        if cause.get("starts") not in starts:
            return "event_changed"
        if event.location_type != EventsLocationType.ONLINE and not event.meeting_url:
            return "no_link"  # a mixed event whose Meet link was removed: nothing to join
        day = datetime.fromisoformat(cause["starts"]).date()
        return "checked_in" if row.member_id in _checked_in(session, event.id, day) else None
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
