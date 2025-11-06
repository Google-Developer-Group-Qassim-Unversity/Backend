from sqlalchemy.orm import Session
from .schema import Actions
from app.DB.schema import DepartmentsLogs, Logs, MembersLogs, Modifications, Absence
from typing import Literal

def create_department_log(session: Session, department_id: int, log_id: int, attendance_number: int | None = None):
	new_department_log = DepartmentsLogs(
		department_id=department_id,
		log_id=log_id,
		attendance_number=attendance_number
	)
	session.add(new_department_log)
	session.flush()
	return new_department_log

def create_member_log(session: Session, member_id: int, log_id: int):
	new_member_log = MembersLogs(
		member_id=member_id,
		log_id=log_id
	)
	session.add(new_member_log)
	session.flush()
	return new_member_log

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

def create_absence(session: Session, member_log_id: int, date):
	new_absence = Absence(
		member_log_id=member_log_id,
		date=date
	)
	session.add(new_absence)
	session.flush()
	return new_absence