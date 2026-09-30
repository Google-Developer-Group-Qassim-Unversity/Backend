"""Semester resolution shared by the points, events and custom-points routers.

A semester is a row in ``semesters`` and every event points at one through
``events.semester_id``. Two questions are answered here, both with the same
rule - "the most recent semester that had started by that day":

- which semester is current (today, in Riyadh), and
- which semester a new or edited event belongs to (its end date).

Editing a semester's dates never moves an existing event; the dates only
decide where events land when they are saved.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.DB import semesters as semesters_queries
from app.DB.schema import Semesters
from app.exceptions import NoSemesterForDate, NoSemestersDefined, SemesterNotFound

CLUB_TIMEZONE = ZoneInfo("Asia/Riyadh")


def today() -> date:
    """Today's date where the club is. Tests patch this to pin the calendar."""
    return datetime.now(CLUB_TIMEZONE).date()


def current_semester(session: Session, public_only: bool = False) -> Semesters | None:
    return semesters_queries.get_semester_started_by(session, today(), public_only=public_only)


def resolve_semester(session: Session, hijri_code: int | None) -> Semesters:
    """Look up a semester by its Hijri code (471), or the current one when it is ``None``."""
    if hijri_code is None:
        current = current_semester(session)
        if current is None:
            raise NoSemestersDefined()
        return current

    semester = semesters_queries.get_semester_by_hijri_code(session, hijri_code)
    if semester is None:
        raise SemesterNotFound(hijri_code)
    return semester


def resolve_semester_for_caller(session: Session, hijri_code: int | None, is_super_admin: bool) -> Semesters:
    """Resolve the semester a public-facing request is asking for, or its default, and authorize it.

    An explicit ``?semester`` is honoured as-is. When none is given the default is
    the current semester - but a public caller must not start getting 403s just
    because a super admin made the current semester private, so they get the
    current *public* one, matching what ``/points/semesters`` advertises.
    """
    semester = resolve_semester(session, hijri_code)
    if hijri_code is None and not semester.is_public and not is_super_admin:
        semester = current_semester(session, public_only=True) or semester

    if not semester.is_public and not is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Semester {semester.hijri_code} is not publicly accessible. Super admin credentials required.",
        )
    return semester


def semester_for_event(session: Session, end_datetime: datetime, semester_id: str | None = None) -> Semesters:
    """The semester an event is saved into.

    An explicit ``semester_id`` wins, so an admin can file an event under a
    different term. Otherwise it is the semester that had started by the
    event's end date.
    """
    if semester_id is not None:
        semester = semesters_queries.get_semester_by_id(session, semester_id)
        if semester is None:
            raise SemesterNotFound(semester_id)
        return semester

    semester = semesters_queries.get_semester_started_by(session, end_datetime.date())
    if semester is None:
        raise NoSemesterForDate(end_datetime.date())
    return semester
