from fastapi import APIRouter, status, HTTPException, Query
from app.DB import points as points_queries, semesters as semesters_queries

from app.routers.models import BaseClassModel
from datetime import date, datetime
from app.semesters import current_semester, resolve_semester_for_caller
from typing import Annotated
from app.dependencies import DB
from app.services.permissions.dependencies import CurrentAccess

router = APIRouter(prefix="/points", tags=["Points"])

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
    location_type: str
    # true when every action in this history row is a bonus action
    bonus_row: bool


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
    # `id` stays the Hijri code (471): it is what `?semester=` takes and what
    # the leaderboard app links with. The row's UUID is admin-only.
    id: int
    gregorian_code: int
    name: str
    start_date: date
    end_date: date
    is_current: bool


class Semesters_model(BaseClassModel):
    current_semester: int | None
    semesters: list[int]
    details: list[Semester_summary_model]


# ============ routes ============


@router.get("/semesters", status_code=status.HTTP_200_OK, response_model=Semesters_model)
def get_semesters(session: DB):
    """The publicly visible semesters, plus which one is the default."""
    public = semesters_queries.get_semesters(session, public_only=True)
    # A private current semester must not leak here - the default is the current public one.
    current = current_semester(session, public_only=True)
    current_code = current.hijri_code if current is not None else None
    return Semesters_model(
        current_semester=current_code,
        semesters=[semester.hijri_code for semester in public],
        details=[
            Semester_summary_model(
                id=semester.hijri_code,
                gregorian_code=semester.gregorian_code,
                name=semester.name,
                start_date=semester.start_date,
                end_date=semester.end_date,
                is_current=semester.hijri_code == current_code,
            )
            for semester in public
        ],
    )


@router.get("/members/total", status_code=status.HTTP_200_OK, response_model=list[Member_points_model])
def get_all_members_points(session: DB, access: CurrentAccess, semester: Annotated[int | None, Query()] = None):
    resolved = resolve_semester_for_caller(session, semester, access.is_super_admin)
    rows = points_queries.get_members_points_semester(session, resolved.id)
    return [Member_points_model.model_validate(row) for row in rows]


@router.get("/members/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_event_history_model)
def get_member_points(
    member_id: int, session: DB, access: CurrentAccess, semester: Annotated[int | None, Query()] = None
):
    resolved = resolve_semester_for_caller(session, semester, access.is_super_admin)

    member_points = points_queries.get_member_points_by_id_semester(session, resolved.id, member_id)
    if member_points is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} does not exist")
    member_points_history = points_queries.get_member_points_history_semester(session, resolved.id, member_id)

    return Member_event_history_model(
        member=Member_points_model.model_validate(member_points),
        events=[Event_model.model_validate(row) for row in member_points_history],
    )


@router.get("/departments/total", status_code=status.HTTP_200_OK, response_model=Response_department_points_model)
def get_all_departments_points(session: DB, access: CurrentAccess, semester: Annotated[int | None, Query()] = None):
    resolved = resolve_semester_for_caller(session, semester, access.is_super_admin)
    departments_points = points_queries.get_departments_points_semester(session, resolved.id)
    return Response_department_points_model(
        administrative=[
            Department_points_model.model_validate(department)
            for department in departments_points
            if department["department_type"] == "administrative"
        ],
        practical=[
            Department_points_model.model_validate(department)
            for department in departments_points
            if department["department_type"] == "practical"
        ],
    )


@router.get(
    "/departments/{department_id:int}", status_code=status.HTTP_200_OK, response_model=Department_points_history_model
)
def get_department_points(
    department_id: int, session: DB, access: CurrentAccess, semester: Annotated[int | None, Query()] = None
):
    resolved = resolve_semester_for_caller(session, semester, access.is_super_admin)

    department_points = points_queries.get_department_points_by_id_semester(session, resolved.id, department_id)
    if department_points is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Department with id {department_id} does not exist"
        )
    department_points_history = points_queries.get_department_points_history_semester(
        session, resolved.id, department_id
    )

    return Department_points_history_model(
        department=Department_points_model.model_validate(department_points),
        events=[Event_model.model_validate(row) for row in department_points_history],
    )
