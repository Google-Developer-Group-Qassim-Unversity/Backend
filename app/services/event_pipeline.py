"""The events pipeline's rules: which days can be booked, and by whom.

Every day is bookable unless it falls in the lockout (today plus the next three
days, in Riyadh) or Logistics banned it. The time comes from
``event_pipeline_clock`` so tests can freeze it.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from sqlalchemy.orm import Session

from app.DB import department_permissions as permission_queries
from app.DB import event_pipeline as queries
from app.DB.schema import PipelineTeam
from app.exceptions import DepartmentForbidden, PipelineConflict
from app.services import event_pipeline_clock as clock
from app.services.department_permissions import PipelineActor

logger = logging.getLogger(__name__)

# Today and the next three days can never be booked; the first bookable day is today + 4.
LOCKOUT_DAYS = 4
# The widest window one calendar read may ask for.
MAX_CALENDAR_DAYS = 120


class DayStatus(str, Enum):
    LOCKED = "locked"
    BANNED = "banned"
    OPEN = "open"


@dataclass
class CalendarDay:
    date: date
    status: DayStatus
    reason: str | None = None


def first_bookable_day() -> date:
    return clock.today() + timedelta(days=LOCKOUT_DAYS)


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def check_range(start: date, end: date, limit: int = MAX_CALENDAR_DAYS) -> None:
    if end < start:
        raise PipelineConflict("bad_range", "The end date is before the start date", 422)
    if (end - start).days + 1 > limit:
        raise PipelineConflict("range_too_long", f"At most {limit} days at once", 422)


def calendar(session: Session, start: date, end: date) -> list[CalendarDay]:
    check_range(start, end)
    bans = {ban.date: ban for ban in queries.get_bans(session, start, end)}
    first_open = first_bookable_day()
    days = []
    for day in days_between(start, end):
        if day in bans:
            days.append(CalendarDay(day, DayStatus.BANNED, bans[day].reason))
        elif day < first_open:
            days.append(CalendarDay(day, DayStatus.LOCKED))
        else:
            days.append(CalendarDay(day, DayStatus.OPEN))
    return days


def require_team(session: Session, actor: PipelineActor, team: PipelineTeam) -> int:
    """The department playing ``team``, if the caller can act for it."""
    department_id = permission_queries.get_team_department_id(session, team)
    if department_id is None:
        raise PipelineConflict("team_not_set", f"No department is set as the {team.value} team yet")
    if not actor.can_act_for(department_id):
        raise DepartmentForbidden(department_id)
    return department_id


def _check_ban_days(days: list[date]) -> list[date]:
    unique = sorted(set(days))
    if not unique:
        raise PipelineConflict("no_days", "Pick at least one day", 422)
    if unique[0] < clock.today():
        raise PipelineConflict("day_in_past", "Days in the past cannot be banned or unbanned", 422)
    check_range(unique[0], unique[-1], 366)
    return unique


def ban(session: Session, actor: PipelineActor, days: list[date], reason: str | None) -> list[date]:
    require_team(session, actor, PipelineTeam.LOGISTICS)
    unique = _check_ban_days(days)
    queries.ban_days(session, unique, reason, actor.member.id)
    logger.info("Banned %d day(s) from %s to %s", len(unique), unique[0], unique[-1])
    return unique


def unban(session: Session, actor: PipelineActor, days: list[date]) -> int:
    require_team(session, actor, PipelineTeam.LOGISTICS)
    unique = _check_ban_days(days)
    removed = queries.unban_days(session, unique)
    logger.info("Unbanned %d day(s)", removed)
    return removed
