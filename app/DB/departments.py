from sqlalchemy.orm import Session
from sqlalchemy import select
from .schema import Departments, SemesterDepartments


def get_departments(session: Session):
    statement = select(Departments)
    departments = session.scalars(statement).all()
    return departments


def get_semester_departments(session: Session, semester_id: str):
    """The departments enrolled in a semester, minus the ones hidden from the
    leaderboard (the Board, Leadership) - the list an event's departments
    should be picked from."""
    statement = (
        select(Departments)
        .join(SemesterDepartments, SemesterDepartments.department_id == Departments.id)
        .where(SemesterDepartments.semester_id == semester_id)
        .where(Departments.show_in_leaderboard == 1)
        .order_by(Departments.id)
    )
    departments = session.scalars(statement).all()
    return departments


def get_department_by_id(session: Session, department_id: int):
    statement = select(Departments).where(Departments.id == department_id)
    department = session.scalars(statement).first()
    return department
