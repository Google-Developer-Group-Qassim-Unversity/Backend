from ast import stmt
from sqlalchemy.orm import Session
from app.DB.schema import (
    Events,
    Actions,
    DepartmentsLogs,
    Departments,
    Logs,
    Members,
    MembersLogs,
    Modifications,
)
from typing import Literal
from sqlalchemy import select, func, case
from sqlalchemy.orm import aliased, Session
from json import loads
from datetime import datetime, timedelta


def create_department_log(
    session: Session,
    department_id: int,
    log_id: int,
):
    new_department_log = DepartmentsLogs(
        department_id=department_id,
        log_id=log_id,
    )
    session.add(new_department_log)
    session.flush()
    return new_department_log


def create_member_log(
    session: Session, member_id: int, log_id: int, date: datetime = None
):
    if date is None:
        date = datetime.now()
    new_member_log = MembersLogs(member_id=member_id, log_id=log_id, date=date)
    session.add(new_member_log)
    session.flush()
    return new_member_log


def get_member_logs(session: Session, member_id: int, log_id: int):
    stmt = select(MembersLogs).where(
        MembersLogs.member_id == member_id, MembersLogs.log_id == log_id
    )
    member_logs = session.scalars(stmt).all()
    if not member_logs:
        return None
    return member_logs


def create_log(session: Session, event_id: int, action_id: int):
    new_log = Logs(event_id=event_id, action_id=action_id)
    session.add(new_log)
    session.flush()
    return new_log


def create_modification(
    session: Session, log_id: int, type: Literal["bonus", "discount"], value: int
):
    new_modification = Modifications(log_id=log_id, type=type, value=value)
    session.add(new_modification)
    session.flush()
    return new_modification


# absence was remove @jan 23 2026
# def create_absence(session: Session, member_log_id: int, date):
# 	new_absence = Absence(
# 		member_log_id=member_log_id,
# 		date=date
# 	)
# 	session.add(new_absence)
# 	session.flush()
# 	return new_absence


def get_expanded_members_logs(session: Session):
    # absence was remove @jan 23 2026
    # Abs = aliased(Absence)
    stmt = (
        session.query(
            Logs.id.label("log_id"),
            Events.id.label("event_id"),
            Events.name.label("event_name"),
            Events.start_datetime,
            Events.end_datetime,
            Events.location_type,
            Events.location,
            Events.description,
            Actions.action_name,
            Actions.action_type,
            func.sum(
                case((Modifications.type == "bonus", Modifications.value), else_=0)
            ).label("bonus"),
            func.sum(
                case((Modifications.type == "discount", Modifications.value), else_=0)
            ).label("discount"),
            func.JSON_ARRAYAGG(
                func.JSON_OBJECT(
                    "id",
                    Members.id,
                    "name",
                    Members.name,
                    "uni_id",
                    Members.uni_id,
                    # 'absence_date', Abs.date
                )
            ).label("members"),
        )
        .join(Events, Logs.event_id == Events.id)
        .join(Actions, Logs.action_id == Actions.id)
        .join(MembersLogs, Logs.id == MembersLogs.log_id)
        .join(Members, MembersLogs.member_id == Members.id)
        # .outerjoin(Abs, MembersLogs.id == Abs.member_log_id)
        .outerjoin(Modifications, Logs.id == Modifications.log_id)
        .filter(Actions.action_type.in_(["member", "composite"]))
        .group_by(Logs.id)
    )

    return [
        {
            **row._asdict(),
            "members": loads(row.members),
            "bonus": row.bonus / len(loads(row.members)) if row.bonus else 0,
            "discount": row.discount / len(loads(row.members)) if row.discount else 0,
        }
        for row in stmt.all()
    ]


def get_expanded_department_logs(session: Session):
    query = (
        session.query(Logs.id, Departments.name)
        .join(Actions, Actions.id == Logs.action_id)
        .join(DepartmentsLogs, DepartmentsLogs.log_id == Logs.id)
        .join(Departments, Departments.id == DepartmentsLogs.department_id)
        .filter(Actions.action_type == "composite")
    )

    return query.all()


def get_attendable_logs(session: Session, event_id: int):
    ATTENDABLE_ACTION_IDS = [76, 77, 78, 79, 87, 89]
    stmt = select(Logs).where(
        Logs.event_id == event_id, Logs.action_id.in_(ATTENDABLE_ACTION_IDS)
    )
    log = session.scalar(stmt)
    if not log:
        return None
    return log


def delete_department_logs_by_log_id(session: Session, log_id: int):
    stmt = select(DepartmentsLogs).where(DepartmentsLogs.log_id == log_id)
    department_logs = session.scalars(stmt).all()
    for dept_log in department_logs:
        session.delete(dept_log)
    session.flush()
    return len(department_logs)


def get_logs_by_event_id(session: Session, event_id: int):
    stmt = select(Logs).where(Logs.event_id == event_id)
    logs = session.scalars(stmt).all()
    return logs


def update_log_action_id(session: Session, log_id: int, new_action_id: int):
    stmt = select(Logs).where(Logs.id == log_id)
    log = session.scalar(stmt)
    if not log:
        return None
    log.action_id = new_action_id
    session.flush()
    return log


def get_department_id_from_log(session: Session, log_id: int):
    stmt = select(DepartmentsLogs).where(DepartmentsLogs.log_id == log_id)
    dept_log = session.scalar(stmt)
    if not dept_log:
        return None
    return dept_log.department_id


def get_department_logs_count(session: Session, log_id: int):
    stmt = select(DepartmentsLogs).where(DepartmentsLogs.log_id == log_id)
    department_logs = session.scalars(stmt).all()
    return len(department_logs)


def get_event_attendance(session: Session, event_id: int, day: str | int | None = None):
    # Get event start date to calculate day offset
    event = session.query(Events).filter(Events.id == event_id).first()
    if not event:
        return []

    # Calculate total event days
    event_days = (event.end_datetime.date() - event.start_datetime.date()).days + 1

    stmt = (
        select(Members, func.JSON_ARRAYAGG(MembersLogs.date).label("dates"))
        .select_from(Events)
        .join(Logs, Logs.event_id == Events.id)
        .join(MembersLogs, MembersLogs.log_id == Logs.id)
        .join(Members, Members.id == MembersLogs.member_id)
        .where(Events.id == event_id)
    )

    # Filter by specific day if requested
    if day and day != "all" and day != "exclusive_all":
        try:
            day_num = int(day)
            if day_num > 0:
                target_date = event.start_datetime.date()

                target_date = target_date + timedelta(days=day_num - 1)
                stmt = stmt.where(func.DATE(MembersLogs.date) == target_date)
        except (ValueError, TypeError):
            pass

    stmt = stmt.group_by(Members.id).order_by(func.MAX(MembersLogs.date).desc())

    rows = session.execute(stmt).all()
    result = [
        {
            **row._asdict(),
            "dates": sorted(loads(row.dates) if row.dates else [], reverse=True),
        }
        for row in rows
    ]

    # Filter for exclusive_all: only members who attended all days
    if day == "exclusive_all":
        result = [r for r in result if len(r["dates"]) == event_days]

    return result


def delete_n_department_logs(session: Session, log_id: int, count: int):
    stmt = select(DepartmentsLogs).where(DepartmentsLogs.log_id == log_id).limit(count)
    department_logs = session.scalars(stmt).all()
    for dept_log in department_logs:
        session.delete(dept_log)
    session.flush()
    return len(department_logs)


def get_custom_department_points_by_event(session: Session, event_id: int):
    """
    Retrieve all custom department points for a specific event.
    Returns logs with event, action, modification (if any), and department details.
    For predefined actions without modifications, points come from the action itself.
    """
    query = (
        session.query(
            Logs.id.label("log_id"),
            Events.id.label("event_id"),
            Events.name.label("event_name"),
            Events.start_datetime,
            Events.end_datetime,
            Actions.id.label("action_id"),
            Actions.action_name,
            Actions.points.label("action_points"),
            Modifications.type.label("mod_type"),
            Modifications.value.label("mod_value"),
            func.JSON_ARRAYAGG(DepartmentsLogs.department_id).label("department_ids"),
        )
        .join(Events, Logs.event_id == Events.id)
        .join(Actions, Logs.action_id == Actions.id)
        .outerjoin(Modifications, Logs.id == Modifications.log_id)
        .join(DepartmentsLogs, Logs.id == DepartmentsLogs.log_id)
        .filter(Events.id == event_id)
        .group_by(
            Logs.id,
            Events.id,
            Events.name,
            Events.start_datetime,
            Events.end_datetime,
            Actions.id,
            Actions.action_name,
            Actions.points,
            Modifications.type,
            Modifications.value,
        )
    )

    results = query.all()
    return [
        {
            "log_id": row.log_id,
            "event_id": row.event_id,
            "event_name": row.event_name,
            "start_datetime": row.start_datetime,
            "end_datetime": row.end_datetime,
            "action_id": row.action_id,
            "action_name": row.action_name,
            # If no modification, use action's points; otherwise use modification
            "mod_type": row.mod_type
            if row.mod_type
            else ("bonus" if row.action_points >= 0 else "discount"),
            "mod_value": row.mod_value
            if row.mod_value is not None
            else abs(row.action_points),
            "department_ids": loads(row.department_ids) if row.department_ids else [],
        }
        for row in results
    ]


def get_log_by_id(session: Session, log_id: int):
    """Get a specific log by its ID."""
    stmt = select(Logs).where(Logs.id == log_id)
    log = session.scalar(stmt)
    return log


def get_modification_by_log_id(session: Session, log_id: int):
    """Get modification for a specific log."""
    stmt = select(Modifications).where(Modifications.log_id == log_id)
    modification = session.scalar(stmt)
    return modification


def update_modification(
    session: Session,
    modification_id: int,
    mod_type: Literal["bonus", "discount"],
    value: int,
):
    """Update an existing modification."""
    stmt = select(Modifications).where(Modifications.id == modification_id)
    modification = session.scalar(stmt)
    if not modification:
        return None
    modification.type = mod_type
    modification.value = value
    session.flush()
    return modification


def delete_modification(session: Session, modification_id: int):
    """Delete a modification by its ID."""
    stmt = select(Modifications).where(Modifications.id == modification_id)
    modification = session.scalar(stmt)
    if not modification:
        return False
    session.delete(modification)
    session.flush()
    return True


def get_department_ids_by_log_id(session: Session, log_id: int):
    """Get all department IDs associated with a log."""
    stmt = select(DepartmentsLogs.department_id).where(DepartmentsLogs.log_id == log_id)
    department_ids = session.scalars(stmt).all()
    return list(department_ids)
