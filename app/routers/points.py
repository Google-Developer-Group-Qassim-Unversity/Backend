from fastapi import APIRouter, status, HTTPException, Query, Depends
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import points as points_queries, semesters as semesters_queries

from app.DB.schema import Semesters
from sqlalchemy.orm import Session
from app.routers.models import BaseClassModel
from datetime import date, datetime
from app.helpers import is_super_admin
from app.config import config
from app.semesters import resolve_semester, semester_date_bounds
from typing import Annotated
from app.dependencies import DB

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


class Semester_summary_model(BaseClassModel):
    id: int
    name: str | None = None
    start_date: date
    end_date: date
    is_current: bool


class Semesters_model(BaseClassModel):
    current_semester: int | None
    semesters: list[int]
    details: list[Semester_summary_model]


# ============ helpers ============


def _validate_semester_access(semester: Semesters, credentials: HTTPAuthorizationCredentials | None):
    if not semester.is_public:
        if not credentials or not is_super_admin(credentials):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Semester {semester.id} is not publicly accessible. Super admin credentials required.",
            )


def _resolve_requested_semester(
    session: Session, semester_id: int | None, credentials: HTTPAuthorizationCredentials | None
) -> Semesters:
    """Resolve the semester a request is asking for, or its default, and authorize it.

    An explicit ``?semester`` is honoured as-is. When none is given the default is
    the current semester - but a public caller must not start getting 403s just
    because a super admin made the current semester private, so they fall back to
    the newest public one, matching what ``/points/semesters`` advertises.
    """
    if semester_id is not None:
        semester = resolve_semester(session, semester_id)
    else:
        semester = resolve_semester(session, None)
        if not semester.is_public and not (credentials and is_super_admin(credentials)):
            public = semesters_queries.get_semesters(session, public_only=True)
            if public:
                semester = public[0]

    _validate_semester_access(semester, credentials)
    return semester


# ============ routes ============


@router.get("/semesters", status_code=status.HTTP_200_OK, response_model=Semesters_model)
def get_semesters(session: DB):
    """The publicly visible semesters, plus which one is the default."""
    public = semesters_queries.get_semesters(session, public_only=True)
    current = semesters_queries.get_current_semester(session)
    # A private current semester must not leak here - fall back to the newest public one
    # so callers always get a usable default rather than null.
    if current is not None and current.is_public:
        current_id = current.id
    else:
        current_id = public[0].id if public else None
    return Semesters_model(
        current_semester=current_id,
        semesters=[semester.id for semester in public],
        details=[Semester_summary_model.model_validate(semester) for semester in public],
    )


@router.get("/members/total", status_code=status.HTTP_200_OK, response_model=list[Member_points_model])
def get_all_members_points(
    session: DB,
    semester: Annotated[int | None, Query()] = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    resolved = _resolve_requested_semester(session, semester, credentials)
    start_date, end_date = semester_date_bounds(resolved)
    return points_queries.get_members_points_semester(session, start_date, end_date)


@router.get("/members/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_event_history_model)
def get_member_points(
    member_id: int,
    session: DB,
    semester: Annotated[int | None, Query()] = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    resolved = _resolve_requested_semester(session, semester, credentials)
    start_date, end_date = semester_date_bounds(resolved)

    member_points = points_queries.get_members_points_semester(session, start_date, end_date, member_id)
    if member_points is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} does not exist")
    member_points_history = points_queries.get_member_points_history_semester(session, member_id, start_date, end_date)

    return Member_event_history_model(member=member_points, events=member_points_history)


@router.get("/departments/total", status_code=status.HTTP_200_OK, response_model=Response_department_points_model)
def get_all_departments_points(
    session: DB,
    semester: Annotated[int | None, Query()] = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    resolved = _resolve_requested_semester(session, semester, credentials)
    start_date, end_date = semester_date_bounds(resolved)
    departments_points = points_queries.get_departments_points_semester(session, start_date, end_date)
    return Response_department_points_model(
        administrative=[
            department for department in departments_points if department["department_type"] == "administrative"
        ],
        practical=[department for department in departments_points if department["department_type"] == "practical"],
    )


@router.get(
    "/departments/{department_id:int}", status_code=status.HTTP_200_OK, response_model=Department_points_history_model
)
def get_department_points(
    department_id: int,
    session: DB,
    semester: Annotated[int | None, Query()] = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    resolved = _resolve_requested_semester(session, semester, credentials)
    start_date, end_date = semester_date_bounds(resolved)

    department_points = points_queries.get_departments_points_semester(session, start_date, end_date, department_id)
    if department_points is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} does not exist"
        )
    department_points_history = points_queries.get_department_points_history_semester(
        session, department_id, start_date, end_date
    )

    return Department_points_history_model(department=department_points, events=department_points_history)
