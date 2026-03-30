from fastapi import APIRouter, status, HTTPException, Query, Depends
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import points as points_queries
from app.DB.main import SessionLocal
from app.routers.models import BaseClassModel
from datetime import datetime
from app.helpers import is_super_admin
from app.config import config
router = APIRouter()

# ============ models ============

class Member_points_model(BaseClassModel):
    member_id: int
    member_name: str
    total_points: int | None = None

class Event_model(BaseClassModel):
    event_name: str
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    points: int
    action_name: str
    ar_action_name: str | None = None

class Member_event_history_model(BaseClassModel):
    member: Member_points_model
    events: list[Event_model]

class Department_points_model(BaseClassModel):
    department_id: int
    department_name: str
    ar_department_name: str
    department_type: str
    total_points: int

class Response_department_points_model(BaseClassModel):
    administrative: list[Department_points_model]
    practical: list[Department_points_model]

class Department_points_history_model(BaseClassModel):
    department: Department_points_model
    events: list[Event_model]

# ============ helpers ============

def _validate_semester_access(semester: int, credentials: HTTPAuthorizationCredentials | None):
    if semester not in config.PUBLIC_SEMESTERS:
        if not credentials or not is_super_admin(credentials):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Semester {semester} is not publicly accessible. Super admin credentials required."
            )

# ============ routes ============

@router.get("/members/total", status_code=status.HTTP_200_OK, response_model=list[Member_points_model])
def get_all_members_points(
    semester: int = Query(config.CURRENT_SEMESTER),
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    _validate_semester_access(semester, credentials)
    start_date, end_date = config.get_semester_dates(semester)
    with SessionLocal() as session:
        return points_queries.get_members_points_semester(session, start_date, end_date)

@router.get("/members/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_event_history_model)
def get_member_points(
    member_id: int,
    semester: int = Query(config.CURRENT_SEMESTER),
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    _validate_semester_access(semester, credentials)
    start_date, end_date = config.get_semester_dates(semester)

    with SessionLocal() as session:
        member_points = points_queries.get_members_points_semester(session, start_date, end_date, member_id)
        if member_points is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} does not exist")
        member_points_history = points_queries.get_member_points_history_semester(session, member_id, start_date, end_date)

    return Member_event_history_model(
        member=member_points,
        events=member_points_history
    )

@router.get("/departments/total", status_code=status.HTTP_200_OK, response_model=Response_department_points_model)
def get_all_departments_points(
    semester: int = Query(config.CURRENT_SEMESTER),
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    _validate_semester_access(semester, credentials)
    start_date, end_date = config.get_semester_dates(semester)

    with SessionLocal() as session:
        departments_points = points_queries.get_departments_points_semester(session, start_date, end_date)
    return Response_department_points_model(
        administrative=[department for department in departments_points if department['department_type'] == 'administrative'],
        practical=[department for department in departments_points if department['department_type'] == 'practical']
    )

@router.get("/departments/{department_id:int}", status_code=status.HTTP_200_OK, response_model=Department_points_history_model)
def get_department_points(
    department_id: int,
    semester: int = Query(config.CURRENT_SEMESTER),
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    _validate_semester_access(semester, credentials)
    start_date, end_date = config.get_semester_dates(semester)

    with SessionLocal() as session:
        department_points = points_queries.get_departments_points_semester(session, start_date, end_date, department_id)
        if department_points is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} does not exist")
        department_points_history = points_queries.get_department_points_history_semester(session, department_id, start_date, end_date)

    return Department_points_history_model(
        department=department_points,
        events=department_points_history
    )
