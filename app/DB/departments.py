from sqlalchemy.orm import Session
from sqlalchemy import select
from .schema import Departments, DepartmentsLogs

def get_departments(session: Session):
	statement = select(Departments)
	departments = session.scalars(statement).all()
	return departments

def get_department_by_id(session: Session, department_id: int):
	statement = select(Departments).where(Departments.id == department_id)
	department = session.scalars(statement).first()
	return department

def create_department_log(session: Session, department_id: int, log_id: int, attendance_number: int | None = None):
	new_log = DepartmentsLogs(
		department_id=department_id,
		log_id=log_id,
		attendance_number=attendance_number
	)
	session.add(new_log)
	session.flush()
	return new_log