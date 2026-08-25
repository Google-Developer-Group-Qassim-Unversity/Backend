from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .schema import Semesters


def get_semesters(session: Session, public_only: bool = False):
    """All semesters, newest first (semester codes increase over time)."""
    statement = select(Semesters).order_by(Semesters.start_date.desc())
    if public_only:
        statement = statement.where(Semesters.is_public == 1)
    return session.scalars(statement).all()


def get_semester_by_id(session: Session, semester_id: int) -> Semesters | None:
    return session.scalars(select(Semesters).where(Semesters.id == semester_id)).first()


def get_current_semester(session: Session) -> Semesters | None:
    """The semester flagged as current, falling back to the most recent one."""
    current = session.scalars(select(Semesters).where(Semesters.is_current == 1)).first()
    if current:
        return current
    return session.scalars(select(Semesters).order_by(Semesters.start_date.desc())).first()


def create_semester(
    session: Session,
    semester_id: int,
    name: str | None,
    start_date: date,
    end_date: date,
    is_public: bool,
    is_current: bool,
) -> Semesters:
    semester = Semesters(
        id=semester_id, name=name, start_date=start_date, end_date=end_date, is_public=int(is_public), is_current=0
    )
    session.add(semester)
    session.flush()
    if is_current:
        set_current_semester(session, semester_id)
    return semester


def update_semester(
    session: Session, semester: Semesters, name: str | None, start_date: date, end_date: date, is_public: bool
) -> Semesters:
    semester.name = name
    semester.start_date = start_date
    semester.end_date = end_date
    semester.is_public = int(is_public)
    session.flush()
    return semester


def set_current_semester(session: Session, semester_id: int) -> None:
    """Flag one semester as current, clearing the flag on every other row."""
    session.execute(update(Semesters).where(Semesters.id != semester_id).values(is_current=0))
    session.execute(update(Semesters).where(Semesters.id == semester_id).values(is_current=1))
    session.flush()


def delete_semester(session: Session, semester: Semesters) -> None:
    session.delete(semester)
    session.flush()


def get_overlapping_semesters(session: Session, start_date: date, end_date: date, exclude_id: int | None = None):
    """Semesters whose date range intersects [start_date, end_date]."""
    statement = select(Semesters).where(Semesters.start_date <= end_date, Semesters.end_date >= start_date)
    if exclude_id is not None:
        statement = statement.where(Semesters.id != exclude_id)
    return session.scalars(statement).all()
