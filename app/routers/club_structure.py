"""Admin reads and super-admin management of the explicit club structure."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB import club_assignments as queries
from app.DB import departments as department_queries
from app.DB.schema import ClubAssignmentRole, Departments, Members
from app.dependencies import DB
from app.exceptions import MemberNotFound, NotFound
from app.helpers import admin_guard, super_admin_guard
from app.leaderboard_cache import reset_leaderboard_cache
from app.routers.club_structure_models import (
    AddDepartmentMemberRequest,
    ClubAssignmentResponse,
    ClubDepartmentResponse,
    ClubOverviewResponse,
    DepartmentCardResponse,
    PresidentSeatResponse,
    PublicClubDepartmentResponse,
    PublicClubStructureResponse,
    ReplaceAssignmentRequest,
    TenureHistoryResponse,
    UpdateDepartmentRequest,
)
from app.services import club_structure as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/club-structure", tags=["Club structure"])
DatabaseId = Annotated[int, Path(gt=0, le=4294967295)]


def _management_actor(credentials: Annotated[HTTPAuthorizationCredentials, Depends(super_admin_guard)]) -> str:
    subject = (credentials.decoded or {}).get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
        raise HTTPException(status_code=401, detail="A valid Clerk subject is required.")
    return subject


ManagementActor = Annotated[str, Depends(_management_actor)]


def _public_name(full_name: str) -> str:
    """Limit public names to the first and final whitespace-delimited parts."""
    parts = full_name.split()
    if len(parts) <= 2:
        return " ".join(parts)
    family_name = parts[-2:] if parts[-1] == "الله" else parts[-1:]
    return " ".join([parts[0], *family_name])


def _show_public_members(department: Departments) -> bool:
    """The Operations roster is intentionally private on the member app."""
    return "operation" not in department.name.casefold() and "التشغيل" not in department.ar_name


def _get_department(session: Session, department_id: int) -> Departments:
    department = department_queries.get_department_by_id(session, department_id)
    if department is None:
        raise NotFound("Department", department_id)
    return department


def _commit_and_refresh(session: Session) -> None:
    session.commit()
    # Cache failure must not report an already committed write as unsuccessful.
    try:
        reset_leaderboard_cache()
    except Exception:
        logger.warning("Could not refresh leaderboard cache after the club structure changed", exc_info=True)


def _commit_department(session: Session, department: Departments) -> ClubDepartmentResponse:
    session.refresh(department)
    result = ClubDepartmentResponse.model_validate(department)
    _commit_and_refresh(session)
    return result


@router.get("/public", response_model=PublicClubStructureResponse)
def get_public_club_structure(session: DB):
    """Return display-only active structure data without admin or audit fields."""
    rosters: dict[int, list] = {}
    for assignment in queries.get_active_department_rosters(session):
        rosters.setdefault(assignment.department_id, []).append(assignment)

    departments = []
    for department in department_queries.get_departments(session):
        if not department.active:
            continue
        roster = rosters.get(department.id, [])
        leader = next((_public_name(a.member.name) for a in roster if a.role == ClubAssignmentRole.LEADER), None)
        deputy = next((_public_name(a.member.name) for a in roster if a.role == ClubAssignmentRole.DEPUTY), None)
        members = (
            [_public_name(a.member.name) for a in roster if a.role == ClubAssignmentRole.MEMBER]
            if _show_public_members(department)
            else []
        )
        departments.append(
            PublicClubDepartmentResponse(
                id=department.id,
                name=department.name,
                ar_name=department.ar_name,
                type=department.type,
                color=department.color,
                icon=department.icon,
                leadership_enabled=bool(department.leadership_enabled),
                leader=leader,
                deputy=deputy,
                members=members,
            )
        )

    return PublicClubStructureResponse(
        presidents=[_public_name(assignment.member.name) for assignment in queries.get_presidents(session)],
        departments=sorted(departments, key=lambda department: department.id),
    )


@router.get("", response_model=ClubOverviewResponse, dependencies=[Depends(admin_guard)])
def get_club_overview(session: DB, include_archived: bool = False):
    counts = queries.get_department_member_counts(session, include_archived=include_archived)
    leadership = {
        (a.department_id, a.role): ClubAssignmentResponse.model_validate(a)
        for a in queries.get_current_leadership(session, include_archived=include_archived)
    }
    presidents = {a.president_slot: ClubAssignmentResponse.model_validate(a) for a in queries.get_presidents(session)}
    departments = [
        DepartmentCardResponse(
            **ClubDepartmentResponse.model_validate(department).model_dump(),
            member_count=counts[department.id],
            leader=leadership.get((department.id, ClubAssignmentRole.LEADER)),
            deputy=leadership.get((department.id, ClubAssignmentRole.DEPUTY)),
        )
        for department in department_queries.get_departments(session)
        if department.id in counts
    ]
    return ClubOverviewResponse(
        departments=sorted(departments, key=lambda d: d.id),
        presidents=[PresidentSeatResponse(slot=slot, assignment=presidents.get(slot)) for slot in (1, 2)],
        total_members=queries.count_current_members(session, include_archived=include_archived),
    )


@router.get(
    "/departments/{department_id:int}", response_model=ClubDepartmentResponse, dependencies=[Depends(admin_guard)]
)
def get_club_department(department_id: DatabaseId, session: DB):
    return _get_department(session, department_id)


@router.get(
    "/departments/{department_id:int}/roster",
    response_model=list[ClubAssignmentResponse],
    dependencies=[Depends(admin_guard)],
)
def get_club_department_roster(department_id: DatabaseId, session: DB):
    _get_department(session, department_id)
    return queries.get_department_roster(session, department_id)


@router.get("/history", response_model=TenureHistoryResponse, dependencies=[Depends(admin_guard)])
def get_club_tenure_history(
    session: DB,
    department_id: Annotated[int | None, Query(gt=0, le=4294967295)] = None,
    member_id: Annotated[int | None, Query(gt=0, le=4294967295)] = None,
    role: ClubAssignmentRole | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if department_id is not None:
        _get_department(session, department_id)
    if member_id is not None and session.scalar(select(Members.id).where(Members.id == member_id)) is None:
        raise MemberNotFound(member_id)
    assignments = queries.get_tenure_history(
        session, department_id=department_id, member_id=member_id, role=role, limit=limit + 1, offset=offset
    )
    return TenureHistoryResponse(
        items=[ClubAssignmentResponse.model_validate(a) for a in assignments[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(assignments) > limit,
    )


@router.post(
    "/departments", status_code=201, response_model=ClubDepartmentResponse, dependencies=[Depends(super_admin_guard)]
)
def create_club_department(payload: service.DepartmentSettings, session: DB):
    department = service.create_department(session, payload)
    return _commit_department(session, department)


@router.put(
    "/departments/{department_id:int}", response_model=ClubDepartmentResponse, dependencies=[Depends(super_admin_guard)]
)
def update_club_department(department_id: DatabaseId, payload: UpdateDepartmentRequest, session: DB):
    department = service.update_department_settings(session, department_id, payload)
    return _commit_department(session, department)


@router.post(
    "/departments/{department_id:int}/archive",
    response_model=ClubDepartmentResponse,
    dependencies=[Depends(super_admin_guard)],
)
def archive_club_department(department_id: DatabaseId, session: DB):
    department = service.set_department_active(session, department_id, active=False)
    return _commit_department(session, department)


@router.post(
    "/departments/{department_id:int}/restore",
    response_model=ClubDepartmentResponse,
    dependencies=[Depends(super_admin_guard)],
)
def restore_club_department(department_id: DatabaseId, session: DB):
    department = service.set_department_active(session, department_id, active=True)
    return _commit_department(session, department)


@router.post("/departments/{department_id:int}/members", status_code=201, response_model=ClubAssignmentResponse)
def add_club_department_member(
    department_id: DatabaseId, payload: AddDepartmentMemberRequest, session: DB, actor: ManagementActor
):
    assignment = service.add_department_member(session, department_id, payload.member_id, changed_by=actor)
    result = ClubAssignmentResponse.model_validate(assignment)
    _commit_and_refresh(session)
    return result


@router.delete("/departments/{department_id:int}/members/{member_id:int}", response_model=ClubAssignmentResponse)
def remove_club_department_member(
    department_id: DatabaseId,
    member_id: DatabaseId,
    session: DB,
    actor: ManagementActor,
    expected_assignment_id: Annotated[int, Query(gt=0, le=4294967295)],
):
    assignment = service.remove_department_member(
        session, department_id, member_id, expected_assignment_id=expected_assignment_id, changed_by=actor
    )
    result = ClubAssignmentResponse.model_validate(assignment)
    _commit_and_refresh(session)
    return result


@router.put("/departments/{department_id:int}/leadership/{role}", response_model=ClubAssignmentResponse | None)
def replace_club_department_leadership(
    department_id: DatabaseId,
    role: Literal["leader", "deputy"],
    payload: ReplaceAssignmentRequest,
    session: DB,
    actor: ManagementActor,
):
    assignment = service.replace_department_leadership(
        session,
        department_id,
        ClubAssignmentRole(role),
        payload.member_id,
        expected_assignment_id=payload.expected_assignment_id,
        changed_by=actor,
    )
    result = ClubAssignmentResponse.model_validate(assignment) if assignment is not None else None
    _commit_and_refresh(session)
    return result


@router.put("/presidents/{slot:int}", response_model=ClubAssignmentResponse | None)
def replace_club_president(
    slot: Annotated[int, Path(ge=1, le=2)], payload: ReplaceAssignmentRequest, session: DB, actor: ManagementActor
):
    assignment = service.replace_president(
        session, slot, payload.member_id, expected_assignment_id=payload.expected_assignment_id, changed_by=actor
    )
    result = ClubAssignmentResponse.model_validate(assignment) if assignment is not None else None
    _commit_and_refresh(session)
    return result
