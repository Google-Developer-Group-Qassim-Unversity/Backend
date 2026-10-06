from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from app.DB import departments as departments_queries

from app.routers.models import Department_model, NotFoundResponse
from app.dependencies import DB
from app.semesters import semester_for_event

router = APIRouter(prefix="/departments", tags=["departments"])


@router.get("", status_code=status.HTTP_200_OK, response_model=list[Department_model])
def get_all_departments(session: DB, end_date: Annotated[date | None, Query()] = None):
    """Every department, or - with ``end_date`` - the departments enrolled in the
    semester that had started by that date, minus the ones hidden from the
    leaderboard. The date is the event's end date, matching where saving an
    event files it."""
    if end_date is None:
        departments = departments_queries.get_departments(session)
        return departments

    semester = semester_for_event(session, datetime.combine(end_date, datetime.min.time()))
    departments = departments_queries.get_semester_departments(session, semester.id)
    return departments


@router.get(
    "/{department_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Department_model,
    responses={404: {"model": NotFoundResponse, "description": "Department not found"}},
)
def get_department_by_id(department_id: int, session: DB):
    department = departments_queries.get_department_by_id(session, department_id)
    if not department:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} not found"
        )
    return department
