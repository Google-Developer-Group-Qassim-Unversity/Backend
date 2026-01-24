from sqlalchemy.orm import Session
from app.DB.schema import t_members_points, t_member_event_history, t_departments_points, t_departments_points_history
from sqlalchemy import select


def get_all_members_points(session: Session):
    statement = select(t_members_points)
    members_points = session.execute(statement).all()
    return [dict(row._mapping) for row in members_points]

def get_member_points(session: Session, member_id: int):
    statement = select(t_members_points).where(t_members_points.c.member_id == member_id)
    member_points = session.execute(statement).first()
    if member_points is None:
        return None
    return dict(member_points._mapping)

def get_member_points_history(session: Session, member_id: int):
    statement = select(t_member_event_history).where(t_member_event_history.c.member_id == member_id)
    member_points = session.execute(statement).all()
    return [dict(row._mapping) for row in member_points]

def get_all_departments_points(session: Session):
    statement = select(t_departments_points)
    departments_points = session.execute(statement).all()
    return [dict(row._mapping) for row in departments_points]

def get_department_points(session: Session, department_id: int):
    statement = select(t_departments_points).where(t_departments_points.c.department_id == department_id)
    department_points = session.execute(statement).first()
    return dict(department_points._mapping)

def get_department_points_history(session: Session, department_id: int):
    statement = select(t_departments_points_history).where(t_departments_points_history.c.department_id == department_id)
    department_points = session.execute(statement).all()
    return [dict(row._mapping) for row in department_points]