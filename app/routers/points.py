from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.DB import points as points_queries, semesters as semesters_queries
from app.dependencies import DB
from app.routers.models import BaseClassModel
from app.semesters import current_semester, resolve_semester_for_caller
from app.services.permissions.dependencies import CurrentAccess

router = APIRouter(prefix="/points", tags=["Points"])

# ============ models ============


class Member_points_model(BaseClassModel):
    member_id: int
    member_name: str
    total_points: int | None = None


class PointsHistoryItem_model(BaseClassModel):
    """One chronological row in a member's or department's points history.

    ``kind == "event"`` is attendance/organization on a real event.
    ``kind == "bonus"`` is a standalone bonus (``event_id = None``) or an
    event-attached bonus/penalty (``event_id`` set, clickable to the event).
    """

    kind: Literal["event", "bonus"]
    id: str
    title: str
    label_en: str | None = None
    label_ar: str | None = None
    points: int
    occurred_at: datetime
    end_datetime: datetime | None = None
    event_id: int | None = None
    bonus_id: str | None = None
    location_type: Literal["online", "on-site"] | None = None


class Event_model(BaseClassModel):
    """Deprecated compatibility shape for shipped TestFlight iOS builds.

    New callers (Website, next iOS build) should read ``history`` instead.
    """

    event_name: str
    event_id: int
    start_datetime: datetime
    end_datetime: datetime
    points: int
    action_name: str
    ar_action_name: str | None = None
    location_type: str
    bonus_row: bool


class Member_event_history_model(BaseClassModel):
    member: Member_points_model
    history: list[PointsHistoryItem_model]
    # Deprecated compatibility list (events + public bonuses mapped into Event_model)
    # kept until TestFlight iOS builds migrate to `history`.
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
    history: list[PointsHistoryItem_model]
    # Deprecated compatibility list kept until TestFlight iOS builds migrate to `history`.
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


# ============ helpers ============


def _legacy_event_rows(rows: list[dict], *, group_member_events: bool = False) -> list[Event_model]:
    grouped: dict[tuple, Event_model] = {}
    for row in rows:
        legacy = _to_legacy_event_row(row)
        # The old member history grouped every action on one event into
        # one row. Department history grouped by event and action.
        key = (legacy.event_id,) if group_member_events else (legacy.event_id, legacy.action_name)
        first = grouped.get(key)
        if first is None:
            grouped[key] = legacy
        else:
            first.points += legacy.points
            first.bonus_row = first.bonus_row and legacy.bonus_row
            for field in ("action_name", "ar_action_name"):
                labels = dict.fromkeys(
                    label
                    for value in (getattr(first, field), getattr(legacy, field))
                    if value
                    for label in value.split(", ")
                )
                if labels:
                    setattr(first, field, ", ".join(labels))
    return list(grouped.values())


def _to_legacy_event_row(row: dict) -> Event_model:
    is_bonus = row["kind"] == "bonus"
    points = int(row["points"] or 0)
    default_en = "Discount" if points < 0 else "Bonus"
    default_ar = "خصم" if points < 0 else "مكافأة"
    return Event_model(
        event_name=row["title"],
        event_id=int(row["legacy_event_id"]),
        start_datetime=row["occurred_at"],
        end_datetime=row["end_datetime"] or row["occurred_at"],
        points=points,
        action_name=row["label_en"] or (default_en if is_bonus else ""),
        ar_action_name=row["label_ar"] or (default_ar if is_bonus else None),
        location_type=row["location_type"] or "none",
        bonus_row=is_bonus,
    )


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
    rows = points_queries.get_member_points_history_semester(session, resolved.id, member_id)

    return Member_event_history_model(
        member=Member_points_model.model_validate(member_points),
        history=[PointsHistoryItem_model.model_validate(row) for row in rows],
        events=_legacy_event_rows(rows, group_member_events=True),
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
    rows = points_queries.get_department_points_history_semester(session, resolved.id, department_id)

    return Department_points_history_model(
        department=Department_points_model.model_validate(department_points),
        history=[PointsHistoryItem_model.model_validate(row) for row in rows],
        events=_legacy_event_rows(rows),
    )
