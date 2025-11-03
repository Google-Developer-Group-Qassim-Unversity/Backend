from fastapi import APIRouter, HTTPException, status
from app.DB import departments as departments_queries
from ..DB.main import SessionLocal
from app.routers.models import Department_model, NotFoundResponse
router = APIRouter()

@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Department_model])
def get_all_departments():
	with SessionLocal() as session:
		departments = departments_queries.get_departments(session)
	return departments

@router.get("/{department_id}", status_code=status.HTTP_200_OK, response_model=Department_model, responses={404: {"model": NotFoundResponse, "description": "Department not found"}})
def get_department_by_id(department_id: int):
	with SessionLocal() as session:
		department = departments_queries.get_department_by_id(session, department_id)
		if not department:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found")
	return department