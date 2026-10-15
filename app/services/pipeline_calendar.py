"""The team's Google Calendar: one entry per event request, from booking to the published event.

The club's leaders see every event in the making on one calendar. A request
gets its entry the moment its dates are booked, marked tentative while it is a
draft holding them, and from then on the entry follows it: details edited,
submitted, returned, each team finishing, dates moved by Logistics or lost to a
ban, published. It comes off when the request no longer holds dates (its hold
ran out, a ban took them, it was cancelled) and when its published event is
deleted. Once published, the entry follows the event, so an edit made from the
events page shows up too.

**What an entry says.** The title carries the stage (``[Held]``, ``[In
review]``, ...) and the department; the colour is the stage as well. The
description is for the team: who asked and how to reach them, which teams are
done, each day's mode, the Meet link, the poster, and a link to the request.
Before Logistics confirms, the times and venue are the ones asked for.
A request with no daily times yet is an all-day entry; with them, a daily
entry, one per day of the event.

**Never in the way.** Like the Notion card, the step commits first and the
entry is written after, in a background task. Every transition marks the
request's row in ``pipeline_calendar_entries`` in its own transaction, so a
write that fails (Google down, token revoked) leaves it marked and the sweep
retries it every few minutes; the error is kept on the row. The row has no
foreign key to the request, so the entry still comes off after the request is
deleted with its event. With ``GOOGLE_CALENDAR_ID`` or
``GOOGLE_CALENDAR_REFRESH_TOKEN`` unset nothing is sent and the rows wait:
setting them later writes every request marked since.

Entries are written as the account that can edit the calendar (a person's own,
see ``google_calendar``), so they show that account as their creator.

The entry's id is derived from the request's, with the environment in it, so
staging and production (whose request ids are the same, staging being a copy)
never write over each other's entries. Outside production the title is also
prefixed with the environment.
"""

import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import config
from app.DB import event_pipeline as queries
from app.DB.main import db_session
from app.DB.schema import (
    EventRequests,
    EventRequestStage,
    EventRequestTasks,
    EventRequestTaskStatus,
    Events,
    PipelineCalendarEntries,
    PipelineTeam,
)
from app.semesters import CLUB_TIMEZONE
from app.services import event_deliverables
from app.services import event_pipeline_clock as clock
from app.services import google_calendar as gcal
from app.services.pipeline_notion import EVENT_TYPES, MODES, request_link

logger = logging.getLogger(__name__)

# How many pending entries one sweep pushes.
SWEEP_BATCH = 20

# The stage's label in the title, and its colour (Google Calendar's event colour ids).
STAGES: dict[EventRequestStage, tuple[str, str]] = {
    EventRequestStage.DRAFT: ("[Held] ", "8"),  # graphite
    EventRequestStage.IN_REVIEW: ("[In review] ", "9"),  # blueberry
    EventRequestStage.RETURNED: ("[Returned] ", "11"),  # tomato
    EventRequestStage.MEDIA: ("[Media] ", "5"),  # banana
    EventRequestStage.READY: ("[Ready] ", "10"),  # basil
    EventRequestStage.PUBLISHED: ("", "3"),  # grape
}
STAGE_NAMES = {
    EventRequestStage.DRAFT: "محجوز مؤقتًا · Held (draft)",
    EventRequestStage.IN_REVIEW: "عند التصميم واللوجستيات · With Design and Logistics",
    EventRequestStage.RETURNED: "أُعيد للقسم · Returned to the department",
    EventRequestStage.MEDIA: "عند الإعلام · With Media",
    EventRequestStage.READY: "جاهز للنشر · Ready to publish",
    EventRequestStage.PUBLISHED: "منشور · Published",
}
TEAM_NAMES = {PipelineTeam.DESIGN: "Design", PipelineTeam.LOGISTICS: "Logistics", PipelineTeam.MEDIA: "Media"}
TASK_MARKS = {
    EventRequestTaskStatus.BRIEF: "—",
    EventRequestTaskStatus.OPEN: "…",
    EventRequestTaskStatus.RETURNED: "↩",
    EventRequestTaskStatus.DONE: "✓",
}
# The characters Google allows in an event id (base32hex).
ID_ALPHABET = set("0123456789abcdefghijklmnopqrstuv")


# --------------------------------------------------------------------------- marking, from inside transitions


def mark(session: Session, request: EventRequests) -> None:
    """The entry has to change: mark it in the transition's own transaction. Nothing is sent from here."""
    entry = session.scalar(
        select(PipelineCalendarEntries).where(PipelineCalendarEntries.request_id == request.id).with_for_update()
    )
    if entry is None:
        session.add(PipelineCalendarEntries(request_id=request.id, sync_pending=1))
    else:
        entry.sync_pending = 1
    session.flush()


def mark_event(session: Session, event_id: int) -> str | None:
    """An event was edited or is being deleted: mark the entry of the request that published it, if one did.

    Returns that request's id, for ``sync_after_commit``. Call before deleting
    the event - the request goes with it.
    """
    request = queries.get_request_for_event(session, event_id)
    if request is None:
        return None
    mark(session, request)
    return request.id


def sync_after_commit(background_tasks: BackgroundTasks, request_id: str | None) -> None:
    """Queue the sync of a request whose transition has committed. Call after ``session.commit()``."""
    if request_id and config.calendar_enabled:
        background_tasks.add_task(sync_request, request_id)


def pending_request_ids(session: Session, limit: int = SWEEP_BATCH) -> list[str]:
    """Requests whose entry still has to be written, oldest first, for the sweep to retry."""
    if not config.calendar_enabled:
        return []
    rows = session.scalars(
        select(PipelineCalendarEntries.request_id)
        .where(PipelineCalendarEntries.sync_pending > 0)
        .order_by(PipelineCalendarEntries.id)
        .limit(limit)
    ).all()
    return list(rows)


# --------------------------------------------------------------------------- the sync itself


def sync_request(request_id: str) -> bool:
    """Write, or remove, the request's entry. True when the calendar now shows the request as it is.

    Runs in its own session, after the transition committed. The entry's row is
    locked for the duration so a background task and the sweep never write the
    same entry twice. A failure keeps the row marked and stores the error. A
    request that is gone (deleted with its event) takes its row with it once
    its entry is off the calendar.
    """
    if not config.calendar_enabled:
        return False
    with db_session() as session:
        entry = session.scalar(
            select(PipelineCalendarEntries).where(PipelineCalendarEntries.request_id == request_id).with_for_update()
        )
        if entry is None:
            return False
        if not entry.sync_pending:
            return True
        request = queries.get_request(session, request_id)
        try:
            body = entry_body(session, request) if request is not None else None
            if body is None:
                gcal.delete_event(event_id(request_id))
                logger.info("Request %s: calendar entry removed", request_id)
            else:
                gcal.put_event(event_id(request_id), body)
                logger.info("Request %s: calendar entry written", request_id)
        except Exception as error:
            entry.error = str(error)[:2000]
            session.commit()
            logger.exception("Request %s: calendar entry not written, kept for the sweep", request_id)
            return False
        if request is None:
            session.delete(entry)
        else:
            entry.sync_pending = 0
            entry.synced_at = clock.now()
            entry.error = None
        session.commit()
        return True


def event_id(request_id: str) -> str:
    """Our id for the request's entry: the request's own, with the environment in it."""
    env = "".join(c for c in config.ENV if c in ID_ALPHABET)
    return f"gdg{env}{request_id.replace('-', '')}"


# --------------------------------------------------------------------------- the entry


def entry_body(session: Session, request: EventRequests) -> dict[str, Any] | None:
    """The whole entry as Google takes it, or None when the request should not be on the calendar."""
    if request.stage == EventRequestStage.CANCELLED:
        return None
    event = session.get(Events, request.event_id) if request.event_id else None
    tasks = {task.team: task for task in request.tasks}
    if event is not None:
        title = event.name
        start, end = event.start_datetime, event.end_datetime
        first_day, last_day = start.date(), end.date()
        daily = (start.time(), end.time())
        location = event.location if event.location and event.location != "-" else None
        meet_link = event.meeting_url
        modes = request.day_modes
    else:
        if request.start_date is None or request.end_date is None:
            return None
        if request.stage == EventRequestStage.DRAFT and (
            request.hold_expires_at is None or request.hold_expires_at <= clock.now()
        ):
            return None
        title = request.title
        first_day, last_day = request.start_date, request.end_date
        confirmed = _logistics(request, tasks.get(PipelineTeam.LOGISTICS))
        daily = (confirmed.daily_start_time, confirmed.daily_end_time)
        location = " · ".join(p.strip() for p in (confirmed.venue, confirmed.room) if p and p.strip()) or None
        meet_link = confirmed.meet_link
        modes = {d.isoformat(): m for d, m in (confirmed.day_modes or {}).items()} or request.day_modes

    label, colour = STAGES[request.stage]
    prefix = "" if config.ENV == "production" else f"({config.ENV}) "
    body: dict[str, Any] = {
        "summary": f"{prefix}{label}{title or 'بدون عنوان · Untitled'} · {request.department.name}",
        "description": description(request, tasks, modes, meet_link),
        "status": "tentative" if request.stage == EventRequestStage.DRAFT else "confirmed",
        "colorId": colour,
        "source": {"title": "GDG Qassim admin", "url": request_link(request)},
        "extendedProperties": {"private": {"request_id": request.id}},
        "reminders": {"useDefault": False, "overrides": []},
        **when(first_day, last_day, *daily),
    }
    if location:
        body["location"] = location
    return body


def _logistics(request: EventRequests, task: EventRequestTasks | None) -> event_deliverables.LogisticsDeliverableV1:
    """Logistics' confirmation once it is done; before that, the request as asked."""
    if task is not None and task.status == EventRequestTaskStatus.DONE:
        confirmed = event_deliverables.logistics_confirmation(request, task)
        if confirmed is not None:
            return confirmed
    return event_deliverables.logistics_prefill(request, task.brief if task else None)


def when(first_day, last_day, start: time | None, end: time | None) -> dict[str, Any]:
    """All day over the dates, or the daily times on each of them (wall-clock Riyadh)."""
    if start is None or end is None or end <= start:
        return {"start": {"date": first_day.isoformat()}, "end": {"date": (last_day + timedelta(days=1)).isoformat()}}
    zone = str(CLUB_TIMEZONE)
    days = (last_day - first_day).days + 1
    timed: dict[str, Any] = {
        "start": {"dateTime": datetime.combine(first_day, start).isoformat(), "timeZone": zone},
        "end": {"dateTime": datetime.combine(first_day, end).isoformat(), "timeZone": zone},
    }
    if days > 1:
        timed["recurrence"] = [f"RRULE:FREQ=DAILY;COUNT={days}"]
    return timed


def description(
    request: EventRequests,
    tasks: dict[PipelineTeam, EventRequestTasks],
    modes: dict[str, str] | None,
    meet_link: str | None,
) -> str:
    lines = [f"المرحلة · Stage: {STAGE_NAMES[request.stage]}"]
    if request.stage == EventRequestStage.DRAFT and request.hold_expires_at:
        expires = request.hold_expires_at.replace(tzinfo=UTC).astimezone(CLUB_TIMEZONE)
        lines.append(f"الحجز ينتهي · Hold expires: {expires:%Y-%m-%d %H:%M} (Riyadh)")
    department = request.department
    names = f"{department.ar_name} · {department.name}" if department.ar_name else department.name
    lines.append(f"القسم · Department: {names}")
    if request.event_type:
        ar, en = EVENT_TYPES.get(request.event_type.value, ("", request.event_type.value))
        lines.append(f"النوع · Type: {ar} · {en}")
    requester = request.requester
    contact = ", ".join(c for c in (requester.email, requester.phone_number) if c)
    lines.append(f"صاحب الطلب · Requested by: {requester.name}" + (f" ({contact})" if contact else ""))
    if request.presenter_name:
        lines.append(f"المقدّم · Presenter: {request.presenter_name}")
    if any(task.status != EventRequestTaskStatus.BRIEF for task in tasks.values()):
        teams = " · ".join(
            f"{name} {TASK_MARKS[tasks[team].status] if team in tasks else '—'}" for team, name in TEAM_NAMES.items()
        )
        lines.append(f"الفرق · Teams: {teams}")
    if modes:
        days = ", ".join(f"{day} {MODES.get(mode, ('', mode))[1]}" for day, mode in sorted(modes.items()))
        lines.append(f"كل يوم · Each day: {days}")
    if meet_link:
        lines.append(f"Meet: {meet_link}")
    design = tasks.get(PipelineTeam.DESIGN)
    poster = (design.deliverable or {}).get("poster_url") if design else None
    if poster:
        lines.append(f"الإعلان · Poster: {poster}")
    lines.append("")
    lines.append(f"الطلب · Request: {request_link(request)}")
    return "\n".join(lines)
