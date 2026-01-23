from sqlalchemy.orm import Session
from app.DB.schema import Events, Actions, DepartmentsLogs, Logs, Members, MembersLogs, Modifications
from typing import Literal
from sqlalchemy import select, func, case
from sqlalchemy.orm import aliased, Session
from json import loads
from datetime import datetime
def create_department_log(session: Session, department_id: int, log_id: int, attendance_number: int | None = None):
	new_department_log = DepartmentsLogs(
		department_id=department_id,
		log_id=log_id,
		attendants_number=attendance_number
	)
	session.add(new_department_log)
	session.flush()
	return new_department_log

def create_member_log(session: Session, member_id: int, log_id: int):
	new_member_log = MembersLogs(
		member_id=member_id,
		log_id=log_id,
        date=datetime.now()
	)
	session.add(new_member_log)
	session.flush()
	return new_member_log

def get_member_logs(session: Session, member_id: int, log_id: int):
	stmt = select(MembersLogs).where(
		MembersLogs.member_id == member_id,
		MembersLogs.log_id == log_id
	)
	member_logs = session.scalars(stmt).all()
	if not member_logs:
		return None
	return member_logs


def create_log(session: Session, event_id: int, action_id: int):
	new_log = Logs(
		event_id=event_id,
		action_id=action_id
	)
	session.add(new_log)
	session.flush()
	return new_log

def create_modification(session: Session, log_id: int, type: Literal['bonus', 'discount'], value: int):
	new_modification = Modifications(
		log_id=log_id,
		type=type,
		value=value
	)
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
            Logs.id.label('log_id'),
			Events.id.label('event_id'),
            Events.name.label('event_name'),
            Events.start_datetime,
            Events.end_datetime,
            Events.location_type,
            Events.location,
            Events.description,
            Actions.action_name,
            Actions.action_type,
            func.sum(
                case(
                    (Modifications.type == 'bonus', Modifications.value),
                    else_=0
                )
            ).label('bonus'),
            func.sum(
                case(
                    (Modifications.type == 'discount', Modifications.value),
                    else_=0
                )
            ).label('discount'),
            func.JSON_ARRAYAGG(
                func.JSON_OBJECT(
                    'id', Members.id,
                    'name', Members.name,
                    'uni_id', Members.uni_id,
                    # 'absence_date', Abs.date
                )
            ).label('members')
        )
        .join(Events, Logs.event_id == Events.id)
        .join(Actions, Logs.action_id == Actions.id)
        .join(MembersLogs, Logs.id == MembersLogs.log_id)
        .join(Members, MembersLogs.member_id == Members.id)
        # .outerjoin(Abs, MembersLogs.id == Abs.member_log_id)
        .outerjoin(Modifications, Logs.id == Modifications.log_id)
        .filter(Actions.action_type.in_(['member', 'composite']))
        .group_by(Logs.id)
    )

    return [
        {**row._asdict(), 'members': loads(row.members), 'bonus': row.bonus/len(loads(row.members)) if row.bonus else 0, 'discount': row.discount/len(loads(row.members)) if row.discount else 0}
        for row in stmt.all()
    ]

def get_expanded_department_logs(session: Session):
	query = (
    session.query(
        Logs.id,
        Departments.name
    )
    .join(Actions, Actions.id == Logs.action_id)
    .join(DepartmentsLogs, DepartmentsLogs.log_id == Logs.id)
    .join(Departments, Departments.id == DepartmentsLogs.department_id)
    .filter(Actions.action_type == 'composite')
	)
	
	return query.all()

def get_attendable_logs(session: Session, event_id: int):
    ATTENDABLE_ACTION_IDS = [76, 77, 78, 79]
    stmt = select(Logs).where(
        Logs.event_id == event_id,
        Logs.action_id.in_(ATTENDABLE_ACTION_IDS)
    )
    log = session.scalar(stmt)  
    if not log:
        return None
    return log