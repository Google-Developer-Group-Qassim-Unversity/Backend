"""Semester resolution shared by the points and events routers.

Semesters live in the ``semesters`` table (see ``app/DB/semesters.py``) rather
than in config, so every lookup here hits the DB and picks up admin edits on
the very next request.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.DB import semesters as semesters_queries
from app.DB.schema import Semesters
from app.exceptions import NoSemestersDefined, SemesterNotFound


def semester_date_bounds(semester: Semesters) -> tuple[str, str]:
    """The half-open ``[start, end)`` range to filter events by, as date strings.

    The stored ``end_date`` is the last day *inside* the semester, so the
    exclusive upper bound handed to the queries is the day after it. Without
    this an event ending on the final day would fall outside every semester.
    """
    return semester.start_date.isoformat(), (semester.end_date + timedelta(days=1)).isoformat()


def resolve_semester(session: Session, semester_id: int | None) -> Semesters:
    """Look up ``semester_id``, or the current semester when it is ``None``."""
    if semester_id is None:
        current = semesters_queries.get_current_semester(session)
        if current is None:
            raise NoSemestersDefined()
        return current

    semester = semesters_queries.get_semester_by_id(session, semester_id)
    if semester is None:
        raise SemesterNotFound(semester_id)
    return semester
