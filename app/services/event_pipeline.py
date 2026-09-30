"""The events pipeline's rules: which days can be booked, by whom, and what a request may do next.

Every day is bookable unless it falls in the lockout (today plus the next three
days, in Riyadh) or Logistics banned it. A booking holds its days for 24 hours
while it is a draft, and keeps them from submit until the event is published.
The time comes from ``event_pipeline_clock`` so tests can freeze it.

Correctness never waits for the sweep: a draft whose hold ran out already
counts as free here, even before anything has marked it.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB import pipeline_teams as team_queries
from app.DB import event_pipeline as queries
from app.DB import logs as log_queries
from app.DB.schema import (
    Departments,
    EventsLocationType,
    EventRequests,
    EventRequestStage,
    EventRequestTasks,
    EventRequestTaskStatus,
    EventRequestUndatedReason,
    PipelineNotificationKind,
    PipelinePenalties,
    PipelineTeam,
)
from app.exceptions import DepartmentForbidden, IncompleteRequest, NotFound, PipelineConflict
from app.routers.models import Events_model, createEvent_model
from app.services import event_briefs
from app.services import event_pipeline_clock as clock
from app.services import pipeline_notifications as notifications
from app.services.pipeline_notifications import PendingEmail
from app.services.pipeline_actor import PipelineActor
from app.services.events import create_full_event

logger = logging.getLogger(__name__)

# Today and the next three days can never be booked; the first bookable day is today + 4.
LOCKOUT_DAYS = 4
# The widest window one calendar read may ask for.
MAX_CALENDAR_DAYS = 120
# The longest event one request can book.
MAX_BOOKING_DAYS = 4
HOLD = timedelta(hours=24)

# Official working hours: Sunday to Thursday, 08:00 to 15:00 (Python weekday: Monday is 0).
OFFICIAL_DAYS = {6, 0, 1, 2, 3}
OFFICIAL_START = time(8, 0)
OFFICIAL_END = time(15, 0)


class DayStatus(str, Enum):
    LOCKED = "locked"
    BANNED = "banned"
    OPEN = "open"
    HELD = "held"
    BOOKED = "booked"
    PUBLISHED = "published"


@dataclass
class CalendarDay:
    date: date
    status: DayStatus
    reason: str | None = None
    requests: list[EventRequests] = field(default_factory=list)


def first_bookable_day() -> date:
    return clock.today() + timedelta(days=LOCKOUT_DAYS)


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def check_range(start: date, end: date, limit: int = MAX_CALENDAR_DAYS) -> None:
    if end < start:
        raise PipelineConflict("bad_range", "The end date is before the start date", 422)
    if (end - start).days + 1 > limit:
        raise PipelineConflict("range_too_long", f"At most {limit} days at once", 422)


def takes_its_days(request: EventRequests, now: datetime) -> bool:
    """Whether the request's dates are taken right now.

    A draft only while its hold lasts; any later stage until it is published,
    and a published event keeps them for good.
    """
    if request.start_date is None or request.stage == EventRequestStage.CANCELLED:
        return False
    if request.stage == EventRequestStage.DRAFT:
        return request.hold_expires_at is not None and request.hold_expires_at > now
    return True


def _day_status_for(request: EventRequests) -> DayStatus:
    if request.stage == EventRequestStage.PUBLISHED:
        return DayStatus.PUBLISHED
    if request.stage == EventRequestStage.DRAFT:
        return DayStatus.HELD
    return DayStatus.BOOKED


_PRIORITY = [DayStatus.PUBLISHED, DayStatus.BOOKED, DayStatus.HELD]


def calendar(session: Session, start: date, end: date) -> list[CalendarDay]:
    check_range(start, end)
    now = clock.now()
    bans = {ban.date: ban for ban in queries.get_bans(session, start, end)}
    taking = [r for r in queries.get_dated_requests(session, start, end) if takes_its_days(r, now)]
    first_open = first_bookable_day()
    days = []
    for day in days_between(start, end):
        on_day = [r for r in taking if r.start_date <= day <= r.end_date]  # type: ignore[operator]
        if day in bans:
            days.append(CalendarDay(day, DayStatus.BANNED, bans[day].reason, on_day))
        elif on_day:
            status = min((_day_status_for(r) for r in on_day), key=_PRIORITY.index)
            days.append(CalendarDay(day, status, None, on_day))
        elif day < first_open:
            days.append(CalendarDay(day, DayStatus.LOCKED))
        else:
            days.append(CalendarDay(day, DayStatus.OPEN))
    return days


def team_department_id(session: Session, team: PipelineTeam) -> int:
    department_id = team_queries.get_team_department_id(session, team)
    if department_id is None:
        raise PipelineConflict("team_not_set", f"No department is set as the {team.value} team yet")
    return department_id


def require_team(session: Session, actor: PipelineActor, team: PipelineTeam) -> int:
    """The department playing ``team``, if the caller can act for it."""
    department_id = team_department_id(session, team)
    if not actor.can_act_for(department_id):
        raise DepartmentForbidden(department_id)
    return department_id


# --------------------------------------------------------------------------- bans


def _check_ban_days(days: list[date]) -> list[date]:
    unique = sorted(set(days))
    if not unique:
        raise PipelineConflict("no_days", "Pick at least one day", 422)
    if unique[0] < clock.today():
        raise PipelineConflict("day_in_past", "Days in the past cannot be banned or unbanned", 422)
    check_range(unique[0], unique[-1], 366)
    return unique


def ban(
    session: Session, actor: PipelineActor, days: list[date], reason: str | None
) -> tuple[list[date], list[EventRequests]]:
    """Close ``days`` and take them away from every request covering one of them.

    That applies at any stage before publish: the request keeps everything
    else, loses its dates and says why. Returns the days and the requests that
    lost their dates, so the caller can tell their teams.
    """
    require_team(session, actor, PipelineTeam.LOGISTICS)
    unique = _check_ban_days(days)
    queries.ban_days(session, unique, reason, actor.member.id)
    undated = []
    for request in queries.get_dated_requests(session, unique[0], unique[-1], lock=True):
        if request.stage == EventRequestStage.PUBLISHED:
            continue
        if any(request.start_date <= day <= request.end_date for day in unique):  # type: ignore[operator]
            lost = {"start_date": request.start_date.isoformat(), "end_date": request.end_date.isoformat()}  # type: ignore[union-attr]
            _undate(request, EventRequestUndatedReason.DAY_BANNED)
            notifications.notify(
                session,
                request.department_id,
                request,
                PipelineNotificationKind.DATES_BANNED,
                {**lost, "reason": reason},
            )
            undated.append(request)
    logger.info(
        "Banned %d day(s) from %s to %s; %d request(s) lost their dates",
        len(unique),
        unique[0],
        unique[-1],
        len(undated),
    )
    return unique, undated


def unban(session: Session, actor: PipelineActor, days: list[date]) -> int:
    require_team(session, actor, PipelineTeam.LOGISTICS)
    unique = _check_ban_days(days)
    removed = queries.unban_days(session, unique)
    logger.info("Unbanned %d day(s)", removed)
    return removed


def _undate(request: EventRequests, reason: EventRequestUndatedReason) -> None:
    request.start_date = None
    request.end_date = None
    request.hold_expires_at = None
    request.undated_reason = reason


# --------------------------------------------------------------------------- booking


@contextmanager
def booking_lock(session: Session):
    """Hold the booking lock for a change to who holds which day. Commit inside it."""
    queries.acquire_booking_lock(session)
    try:
        yield
    finally:
        queries.release_booking_lock(session)


def _check_bookable(session: Session, actor: PipelineActor, start: date, end: date, ignore_id: int | None) -> None:
    """Super admins have full authority: they skip every rule here but a sane range."""
    if actor.is_super_admin:
        check_range(start, end, MAX_CALENDAR_DAYS)
        return
    check_range(start, end, MAX_BOOKING_DAYS)
    if start < first_bookable_day():
        raise PipelineConflict(
            "day_locked", f"The first day you can book is {first_bookable_day().isoformat()}", status_code=409
        )
    if queries.get_bans(session, start, end, lock=True):
        raise PipelineConflict("day_banned", "Logistics closed one of these days")
    now = clock.now()
    for request in queries.get_dated_requests(session, start, end, lock=True):
        if request.id != ignore_id and takes_its_days(request, now):
            raise PipelineConflict("day_taken", "One of these days is already taken")


def _check_one_live_hold(session: Session, department_id: int, ignore_id: int | None = None) -> None:
    held = [
        r for r in queries.get_department_drafts_with_hold(session, department_id, clock.now()) if r.id != ignore_id
    ]
    if held:
        raise PipelineConflict(
            "hold_exists", f"Your department already holds dates for request {held[0].id}; finish or cancel it first"
        )


def book(session: Session, actor: PipelineActor, department_id: int, start: date, end: date) -> EventRequests:
    """Create a draft holding ``start``..``end`` for 24 hours. Call inside ``booking_lock``."""
    actor.require_act_for(department_id)
    if session.get(Departments, department_id) is None:
        raise NotFound("Department", department_id)
    if not actor.is_super_admin:
        _check_one_live_hold(session, department_id)
    _check_bookable(session, actor, start, end, None)
    request = EventRequests(
        department_id=department_id,
        created_by=actor.member.id,
        stage=EventRequestStage.DRAFT,
        start_date=start,
        end_date=end,
        hold_expires_at=clock.now() + HOLD,
    )
    session.add(request)
    session.flush()
    logger.info("Request %s booked %s..%s for department %s", request.id, start, end, department_id)
    return request


def redate(session: Session, actor: PipelineActor, request: EventRequests, start: date, end: date) -> None:
    """Give a request that lost its dates new ones. Call inside ``booking_lock``.

    A draft gets a fresh 24-hour hold. A submitted request that lost its days
    to a ban takes the new ones outright, as its old ones were.
    """
    actor.require_act_for(request.department_id)
    if request.stage in (EventRequestStage.PUBLISHED, EventRequestStage.CANCELLED):
        raise PipelineConflict("not_redatable", "This request can no longer change its dates")
    if takes_its_days(request, clock.now()) and not actor.is_super_admin:
        raise PipelineConflict("still_held", "This request still holds its dates")
    is_draft = request.stage == EventRequestStage.DRAFT
    if is_draft and not actor.is_super_admin:
        _check_one_live_hold(session, request.department_id, ignore_id=request.id)
    _check_bookable(session, actor, start, end, request.id)
    request.start_date = start
    request.end_date = end
    request.hold_expires_at = clock.now() + HOLD if is_draft else None
    request.undated_reason = None
    # Keep the modes of the days that are still in the range.
    if request.day_modes:
        kept = {d.isoformat() for d in days_between(start, end)}
        request.day_modes = {k: v for k, v in request.day_modes.items() if k in kept} or None
    session.flush()
    logger.info("Request %s re-dated to %s..%s", request.id, start, end)


def cancel(session: Session, actor: PipelineActor, request: EventRequests) -> None:
    """A team drops its own draft. Its dates, if it still held any, are free again."""
    actor.require_act_for(request.department_id)
    if request.stage != EventRequestStage.DRAFT and not (
        actor.is_super_admin and request.stage != EventRequestStage.PUBLISHED
    ):
        raise PipelineConflict("not_a_draft", "Only a draft can be cancelled")
    request.stage = EventRequestStage.CANCELLED
    request.hold_expires_at = None
    session.flush()


# --------------------------------------------------------------------------- details

# A returned request is open to its team again until it is resubmitted.
EDITABLE_STAGES = {EventRequestStage.DRAFT, EventRequestStage.RETURNED}


def can_edit(actor: PipelineActor, request: EventRequests) -> bool:
    """The team edits its draft; a super admin edits anything not yet published."""
    if actor.is_super_admin:
        return request.stage not in (EventRequestStage.PUBLISHED, EventRequestStage.CANCELLED)
    return actor.can_act_for(request.department_id) and request.stage in EDITABLE_STAGES


def update_details(session: Session, actor: PipelineActor, request: EventRequests, fields: dict) -> None:
    """Save any subset of the event details. Nothing is required until submit."""
    actor.require_act_for(request.department_id)
    if not can_edit(actor, request):
        raise PipelineConflict("not_editable", "The details are frozen once the request is submitted")

    partners = fields.pop("partner_department_ids", None)
    if "day_modes" in fields and fields["day_modes"] is not None:
        modes = {d.isoformat() if isinstance(d, date) else str(d): v for d, v in fields["day_modes"].items()}
        if request.start_date is None:
            raise PipelineConflict("no_dates", "Book dates before choosing each day's mode", 422)
        allowed = {d.isoformat() for d in days_between(request.start_date, request.end_date)}  # type: ignore[arg-type]
        if not set(modes) <= allowed:
            raise PipelineConflict("mode_outside_dates", "Every day with a mode must be one of the booked days", 422)
        fields["day_modes"] = modes
    for key in ("is_official",):
        if key in fields and fields[key] is not None:
            fields[key] = int(fields[key])
    for key, value in fields.items():
        setattr(request, key, value)
    if partners is not None:
        if request.department_id in partners:
            raise PipelineConflict("self_partner", "A department cannot partner with itself", 422)
        if partners and len(team_queries.get_departments(session, set(partners))) != len(set(partners)):
            raise PipelineConflict("unknown_department", "A partner department does not exist", 422)
        queries.set_partners(session, request, partners)
    session.flush()


def within_official_hours(request: EventRequests) -> bool | None:
    """Whether every booked day is Sun-Thu and the daily times fit in 08:00-15:00."""
    if request.start_date is None or request.daily_start_time is None or request.daily_end_time is None:
        return None
    days = days_between(request.start_date, request.end_date)  # type: ignore[arg-type]
    return all(d.weekday() in OFFICIAL_DAYS for d in days) and (
        OFFICIAL_START <= request.daily_start_time and request.daily_end_time <= OFFICIAL_END
    )


# --------------------------------------------------------------------------- reading


def get_request_for(session: Session, actor: PipelineActor, request_id: int, lock: bool = False) -> EventRequests:
    request = queries.get_request(session, request_id, lock=lock)
    if request is None or request.stage == EventRequestStage.CANCELLED:
        raise NotFound("Event request", request_id)
    if not can_view(session, actor, request):
        raise DepartmentForbidden(request.department_id, "see requests of")
    return request


def can_view(session: Session, actor: PipelineActor, request: EventRequests) -> bool:
    """The requesting department, and every team the request has reached."""
    if actor.can_act_for(request.department_id):
        return True
    team_departments = {row.team: row.department_id for row in team_queries.get_teams(session)}
    return any(
        task.status != EventRequestTaskStatus.BRIEF
        and task.team in team_departments
        and actor.can_act_for(team_departments[task.team])
        for task in request.tasks
    )


def visible_department_ids(actor: PipelineActor) -> set[int] | None:
    return None if actor.is_super_admin else actor.acting_department_ids


# --------------------------------------------------------------------------- briefs and submit

BRIEF_TEAMS = (PipelineTeam.DESIGN, PipelineTeam.LOGISTICS)


def get_task(request: EventRequests, team: PipelineTeam) -> EventRequestTasks | None:
    return next((task for task in request.tasks if task.team == team), None)


def ensure_task(session: Session, request: EventRequests, team: PipelineTeam) -> EventRequestTasks:
    task = get_task(request, team)
    if task is None:
        task = EventRequestTasks(request_id=request.id, team=team, status=EventRequestTaskStatus.BRIEF)
        request.tasks.append(task)
        session.flush()
    return task


def save_brief(session: Session, actor: PipelineActor, request: EventRequests, team: PipelineTeam, brief: dict) -> None:
    """Save a draft brief as it is. Submit checks it."""
    actor.require_act_for(request.department_id)
    if team not in BRIEF_TEAMS:
        raise PipelineConflict("no_brief", f"The {team.value} team has no brief", 422)
    if not can_edit(actor, request):
        raise PipelineConflict("not_editable", "The briefs are frozen once the request is submitted")
    task = ensure_task(session, request, team)
    task.brief = event_briefs.clean_brief(team, brief)
    task.brief_version = event_briefs.BRIEF_VERSION
    session.flush()


def submit(session: Session, actor: PipelineActor, request: EventRequests) -> list[PendingEmail | None]:
    """Send a complete draft to Design and Logistics at the same moment.

    From here on the dates stay taken without a hold. Both teams get a
    notification; returns their emails, to send once this commits.
    """
    actor.require_act_for(request.department_id)
    if request.stage != EventRequestStage.DRAFT:
        raise PipelineConflict("not_a_draft", "Only a draft can be submitted")
    if not takes_its_days(request, clock.now()) and not (actor.is_super_admin and request.start_date is not None):
        raise PipelineConflict("hold_expired", "The hold on your dates ran out; book dates again, then submit")
    for team in BRIEF_TEAMS:
        ensure_task(session, request, team)
    missing = event_briefs.missing_fields(request)
    if missing:
        raise IncompleteRequest(missing)
    now = clock.now()
    request.stage = EventRequestStage.IN_REVIEW
    request.submitted_at = now
    request.hold_expires_at = None
    request.undated_reason = None
    for team in BRIEF_TEAMS:
        task = ensure_task(session, request, team)
        task.status = EventRequestTaskStatus.OPEN
        task.opened_at = now
    session.flush()
    logger.info("Request %s submitted to Design and Logistics", request.id)
    return [
        reach_team(session, actor, request, team, PipelineNotificationKind.REQUEST_RECEIVED) for team in BRIEF_TEAMS
    ]


def reach_team(
    session: Session,
    actor: PipelineActor,
    request: EventRequests,
    team: PipelineTeam,
    kind: PipelineNotificationKind,
    note: str | None = None,
) -> PendingEmail | None:
    """A request reached a team: notify its department and prepare its email."""
    department_id = team_department_id(session, team)
    notifications.notify(session, department_id, request, kind, {"team": team.value})
    return notifications.department_email(session, department_id, request, kind, actor.member, note)


# --------------------------------------------------------------------------- review, return, done

RETURN_WINDOW = timedelta(days=2)
FIX_WINDOW = timedelta(hours=12)
PENALTY_POINTS_PER_LATE_DAY = 1
ALL_TEAMS = (PipelineTeam.DESIGN, PipelineTeam.LOGISTICS, PipelineTeam.MEDIA)


def return_deadline(request: EventRequests) -> datetime | None:
    return request.submitted_at + RETURN_WINDOW if request.submitted_at else None


def can_return(session: Session, actor: PipelineActor, request: EventRequests) -> bool:
    """Design can return a request once, within two days of receiving it. Super admins any time."""
    if request.stage != EventRequestStage.IN_REVIEW:
        return False
    design = get_task(request, PipelineTeam.DESIGN)
    if design is None or design.status != EventRequestTaskStatus.OPEN:
        return False
    if actor.is_super_admin:
        return True
    department_id = team_queries.get_team_department_id(session, PipelineTeam.DESIGN)
    deadline = return_deadline(request)
    return (
        department_id is not None
        and actor.can_act_for(department_id)
        and request.return_count == 0
        and deadline is not None
        and clock.now() <= deadline
    )


def return_request(session: Session, actor: PipelineActor, request: EventRequests, notes: str) -> PendingEmail | None:
    if not can_return(session, actor, request):
        require_team(session, actor, PipelineTeam.DESIGN)
        raise PipelineConflict("cannot_return", "Design can return a request once, within two days of receiving it")
    now = clock.now()
    request.stage = EventRequestStage.RETURNED
    request.returned_at = now
    request.return_due_at = now + FIX_WINDOW
    request.return_notes = notes
    request.return_count += 1
    task = get_task(request, PipelineTeam.DESIGN)
    task.status = EventRequestTaskStatus.RETURNED  # type: ignore[union-attr]
    notifications.notify(session, request.department_id, request, PipelineNotificationKind.RETURNED, {"notes": notes})
    session.flush()
    logger.info("Request %s returned by Design", request.id)
    return notifications.department_email(
        session, request.department_id, request, PipelineNotificationKind.RETURNED, actor.member, notes
    )


def late_days(due: datetime, now: datetime) -> int:
    """Every started 24 hours after the due time is one late day."""
    if now <= due:
        return 0
    seconds = (now - due).total_seconds()
    return int(-(-seconds // 86400))


def record_penalty(session: Session, request: EventRequests, now: datetime) -> PipelinePenalties | None:
    """Create or grow the request's penalty to match how late it is. One row per request."""
    if request.return_due_at is None:
        return None
    days = late_days(request.return_due_at, now)
    if days == 0:
        return None
    penalty = session.scalar(
        select(PipelinePenalties).where(PipelinePenalties.request_id == request.id).with_for_update()
    )
    if penalty is None:
        penalty = PipelinePenalties(
            request_id=request.id,
            department_id=request.department_id,
            late_days=days,
            points=days * PENALTY_POINTS_PER_LATE_DAY,
            reason=f"Returned request {request.id} fixed {days} day(s) late",
        )
        session.add(penalty)
    elif penalty.late_days < days and penalty.applied_log_id is None:
        penalty.late_days = days
        penalty.points = days * PENALTY_POINTS_PER_LATE_DAY
        penalty.reason = f"Returned request {request.id} fixed {days} day(s) late"
    session.flush()
    return penalty


def get_penalty(session: Session, request: EventRequests) -> PipelinePenalties | None:
    return session.scalar(select(PipelinePenalties).where(PipelinePenalties.request_id == request.id))


def resubmit(session: Session, actor: PipelineActor, request: EventRequests) -> PendingEmail | None:
    """The team fixed its returned request; it goes back to Design. Late costs points."""
    actor.require_act_for(request.department_id)
    if request.stage != EventRequestStage.RETURNED:
        raise PipelineConflict("not_returned", "Only a returned request can be resubmitted")
    missing = event_briefs.missing_fields(request)
    if missing:
        raise IncompleteRequest(missing)
    now = clock.now()
    record_penalty(session, request, now)
    request.stage = EventRequestStage.IN_REVIEW
    request.return_due_at = None
    task = ensure_task(session, request, PipelineTeam.DESIGN)
    task.status = EventRequestTaskStatus.OPEN
    task.opened_at = now
    session.flush()
    logger.info("Request %s resubmitted to Design", request.id)
    return reach_team(session, actor, request, PipelineTeam.DESIGN, PipelineNotificationKind.REQUEST_RECEIVED)


def can_complete(session: Session, actor: PipelineActor, request: EventRequests, team: PipelineTeam) -> bool:
    task = get_task(request, team)
    if task is None or task.status != EventRequestTaskStatus.OPEN:
        return False
    if actor.is_super_admin:
        return True
    department_id = team_queries.get_team_department_id(session, team)
    return department_id is not None and actor.can_act_for(department_id)


def complete(
    session: Session, actor: PipelineActor, request: EventRequests, team: PipelineTeam
) -> list[PendingEmail | None]:
    """A team marks its part done. Design done sends the request to Media; all three done makes it ready."""
    if not can_complete(session, actor, request, team):
        require_team(session, actor, team)
        raise PipelineConflict("cannot_complete", f"The {team.value} part is not open")
    now = clock.now()
    task = get_task(request, team)
    task.status = EventRequestTaskStatus.DONE  # type: ignore[union-attr]
    task.completed_at = now  # type: ignore[union-attr]
    task.completed_by = actor.member.id  # type: ignore[union-attr]
    notifications.notify(
        session, request.department_id, request, PipelineNotificationKind.TASK_DONE, {"team": team.value}
    )
    emails: list[PendingEmail | None] = []

    if team == PipelineTeam.DESIGN:
        media = ensure_task(session, request, PipelineTeam.MEDIA)
        media.status = EventRequestTaskStatus.OPEN
        media.opened_at = now
        if request.stage == EventRequestStage.IN_REVIEW:
            request.stage = EventRequestStage.MEDIA
        emails.append(reach_team(session, actor, request, PipelineTeam.MEDIA, PipelineNotificationKind.MEDIA_RECEIVED))

    if all(
        (t := get_task(request, team_)) is not None and t.status == EventRequestTaskStatus.DONE for team_ in ALL_TEAMS
    ):
        request.stage = EventRequestStage.READY
        notifications.notify(session, request.department_id, request, PipelineNotificationKind.READY_TO_PUBLISH)
        emails.append(
            notifications.department_email(
                session, request.department_id, request, PipelineNotificationKind.READY_TO_PUBLISH, actor.member
            )
        )
    session.flush()
    logger.info("Request %s: %s done", request.id, team.value)
    return emails


def inbox(
    session: Session, actor: PipelineActor, team: PipelineTeam | None
) -> list[tuple[EventRequests, EventRequestTasks]]:
    """Open tasks for the teams the caller can act for (every team for a super admin)."""
    team_departments = {row.team: row.department_id for row in team_queries.get_teams(session)}
    teams = [
        t
        for t in ALL_TEAMS
        if (team is None or t == team)
        and (actor.is_super_admin or (t in team_departments and actor.can_act_for(team_departments[t])))
    ]
    if not teams:
        return []
    rows = session.execute(
        select(EventRequests, EventRequestTasks)
        .join(EventRequestTasks, EventRequestTasks.request_id == EventRequests.id)
        .where(
            EventRequestTasks.team.in_(teams),
            EventRequestTasks.status.in_([EventRequestTaskStatus.OPEN, EventRequestTaskStatus.RETURNED]),
            EventRequests.stage.not_in([EventRequestStage.CANCELLED, EventRequestStage.PUBLISHED]),
        )
        .order_by(EventRequestTasks.opened_at)
    ).all()
    return [(r, t) for r, t in rows]


# --------------------------------------------------------------------------- publish

PUBLISHABLE_BY_SUPER_ADMIN = {
    EventRequestStage.IN_REVIEW,
    EventRequestStage.RETURNED,
    EventRequestStage.MEDIA,
    EventRequestStage.READY,
}


def can_publish(actor: PipelineActor, request: EventRequests) -> bool:
    if actor.is_super_admin:
        return request.stage in PUBLISHABLE_BY_SUPER_ADMIN
    return actor.can_act_for(request.department_id) and request.stage == EventRequestStage.READY


def event_for(request: EventRequests, department_action_id: int, member_action_id: int, image_url: str | None):
    """The ``POST /events/`` payload a ready request becomes.

    Times are wall-clock Riyadh times, the way the event form stores them. A
    day mix of on-site and online is published as on-site; the Meet link is
    added from the event page like any other.
    """
    modes = set((request.day_modes or {}).values())
    on_site = "on_site" in modes or not modes
    logistics = get_task(request, PipelineTeam.LOGISTICS)
    venue = (logistics.brief or {}).get("venue") if logistics else None
    registration = request.registration.value if request.registration else "none"
    return createEvent_model(
        event=Events_model(
            name=request.title or f"Event request {request.id}",
            description=request.description,
            location_type=EventsLocationType.ON_SITE if on_site else EventsLocationType.ONLINE,
            location=(venue or "-")[:100] if on_site else "Online",
            start_datetime=datetime.combine(request.start_date, request.daily_start_time or time(0, 0)),  # type: ignore[arg-type]
            end_datetime=datetime.combine(request.end_date, request.daily_end_time or time(23, 59)),  # type: ignore[arg-type]
            # A draft: admins review a published request before members can see it.
            status="draft",
            image_url=image_url,
            is_official=int(bool(request.is_official)),
        ),
        form_type="none" if registration == "none" else "registration",
        department_action_id=department_action_id,
        member_action_id=member_action_id,
        department_id=request.department_id,
    )


def publish(
    session: Session,
    actor: PipelineActor,
    request: EventRequests,
    department_action_id: int,
    member_action_id: int,
    image_url: str | None,
) -> int:
    """Create the real event, in the same transaction, the same way ``POST /events/`` does.

    Any late penalty is taken off the department's log for the new event, once.
    Returns the event id.
    """
    if not can_publish(actor, request):
        actor.require_act_for(request.department_id)
        raise PipelineConflict("not_ready", "Only a request every team has finished can be published")
    if request.start_date is None:
        raise PipelineConflict("no_dates", "This request has no dates")
    event, department_log = create_full_event(
        session, event_for(request, department_action_id, member_action_id, image_url)
    )
    penalty = get_penalty(session, request)
    if penalty is not None and penalty.applied_log_id is None and penalty.points > 0:
        log_queries.create_modification(session, department_log.id, "discount", penalty.points)
        penalty.applied_log_id = department_log.id
    request.event_id = event.id
    request.stage = EventRequestStage.PUBLISHED
    session.flush()
    logger.info("Request %s published as event %s", request.id, event.id)
    return event.id
