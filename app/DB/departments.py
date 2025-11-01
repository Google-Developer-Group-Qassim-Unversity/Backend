from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Departments
from ..routers.models import Department_model

def get_departments(session: Session):
	statement = select(Departments)
	departments = session.scalars(statement).all()
	return departments

def get_department_by_id(session: Session, department_id: int):
	statement = select(Departments).where(Departments.id == department_id)
	department = session.scalars(statement).first()
	return department


