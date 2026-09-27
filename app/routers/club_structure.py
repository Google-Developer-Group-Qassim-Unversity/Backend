"""The club structure per semester: admin reads, super-admin management, and the public roster.

Admin routes take the semester's UUID (``?semester_id=`` on reads, defaulting
to the current semester; a path segment on writes). The public route takes
the Hijri code (``?semester=471``), like ``/points``.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB import club_structure as queries
from app.DB import departments as department_queries
from app.DB import semesters as semesters_queries
from app.DB.schema import ClubMemberships, Departments, Members, Semesters
from app.dependencies import DB
from app.exceptions import MemberNotFound, NoSemestersDefined, NotFound, SemesterNotFound
from app.helpers import admin_guard, optional_clerk_guard, super_admin_guard
from app.leaderboard_cache import reset_leaderboard_cache
from app.routers.club_structure_models import (
    AddMemberRequest,
    AvailableDepartmentResponse,
    ClubChangeResponse,
    ClubChangesResponse,
    ClubDepartmentResponse,
    ClubMemberResponse,
    ClubOverviewResponse,
    ClubRoleResponse,
    ClubSemesterResponse,
    CopySemesterResponse,
    DepartmentCardResponse,
    GrantRoleRequest,
    MembershipResponse,
    PublicClubDepartmentResponse,
    PublicClubSemesterResponse,
    PublicClubStructureResponse,
    RemovedResponse,
    RoleSeatsResponse,
    RosterEntryResponse,
    UpdateDepartmentRequest,
)
from app.semesters import current_semester, resolve_semester_for_caller
from app.services import club_structure as service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/club-structure", tags=["Club structure"])
DatabaseId = Annotated[int, Path(gt=0, le=4294967295)]
RoleKey = Annotated[str, Path(min_length=1, max_length=32, pattern=r"^[a-z_]+$")]
SCOPE = "/semesters/{semester_id}/departments/{department_id:int}"


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


def _get_department(session: Session, department_id: int) -> Departments:
    department = department_queries.get_department_by_id(session, department_id)
    if department is None:
        raise NotFound("Department", department_id)
    return department


def _admin_semester(session: Session, semester_id: UUID | None) -> Semesters:
    """The requested semester, or the current one."""
    if semester_id is None:
        semester = current_semester(session)
        if semester is None:
            raise NoSemestersDefined()
        return semester
    semester = semesters_queries.get_semester_by_id(session, str(semester_id))
    if semester is None:
        raise SemesterNotFound(str(semester_id))
    return semester


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


def _by_department(memberships) -> dict[int, list[ClubMemberships]]:
    grouped: dict[int, list[ClubMemberships]] = {}
    for row in memberships:
        grouped.setdefault(row.department_id, []).append(row)
    return grouped


# ---------- public ----------


@router.get("/public", response_model=PublicClubStructureResponse)
def get_public_club_structure(
    session: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_clerk_guard)],
    semester: int | None = None,
):
    """Display-only roster for a semester (default: the current public one). No admin or audit fields."""
    resolved = resolve_semester_for_caller(session, semester, credentials)
    rosters = _by_department(queries.get_memberships(session, resolved.id))

    presidents: list[str] = []
    departments = []
    for scope in queries.get_semester_departments(session, resolved.id):
        department = scope.department
        roster = rosters.get(department.id, [])
        leaders = [_public_name(row.member.name) for row in roster if row.role.key == "leader"]
        vps = [_public_name(row.member.name) for row in roster if row.role.key == "vp"]
        if department.is_club_leadership:
            # The club's presidents are this department's leaders; it is not listed as a department.
            presidents = leaders
            continue
        officers = {row.member_id for row in roster if row.role.key != service.MEMBER_ROLE}
        departments.append(
            PublicClubDepartmentResponse(
                id=department.id,
                name=scope.name or department.name,
                ar_name=scope.ar_name or department.ar_name,
                type=department.type,
                color=department.color,
                icon=department.icon,
                show_in_leaderboard=bool(department.show_in_leaderboard),
                leadership_enabled=bool(leaders or vps),
                leader=leaders[0] if leaders else None,
                deputy=vps[0] if vps else None,
                members=[
                    _public_name(row.member.name)
                    for row in roster
                    if row.role.key == service.MEMBER_ROLE and row.member_id not in officers
                ],
            )
        )

    return PublicClubStructureResponse(
        semester=PublicClubSemesterResponse(
            code=resolved.hijri_code, gregorian_code=resolved.gregorian_code, name=resolved.name
        ),
        presidents=presidents,
        departments=departments,
    )


# ---------- admin reads ----------


@router.get("", response_model=ClubOverviewResponse, dependencies=[Depends(admin_guard)])
def get_club_overview(session: DB, semester_id: UUID | None = None):
    semester = _admin_semester(session, semester_id)
    roles = queries.get_roles(session)
    officer_roles = [role for role in roles if role.key != service.MEMBER_ROLE]
    limits = queries.get_role_limits(session)
    rosters = _by_department(queries.get_memberships(session, semester.id))
    scopes = queries.get_semester_departments(session, semester.id)

    departments = []
    for scope in scopes:
        roster = rosters.get(scope.department_id, [])
        departments.append(
            DepartmentCardResponse(
                **ClubDepartmentResponse.model_validate(scope.department).model_dump(),
                semester_name=scope.name,
                semester_ar_name=scope.ar_name,
                member_count=sum(1 for row in roster if row.role.key == service.MEMBER_ROLE),
                roles=[
                    RoleSeatsResponse(
                        key=role.key,
                        max_holders=limits.get((scope.department_id, role.id), role.max_holders),
                        holders=[
                            ClubMemberResponse.model_validate(row.member) for row in roster if row.role_id == role.id
                        ],
                    )
                    for role in officer_roles
                ],
            )
        )

    in_semester = {scope.department_id for scope in scopes}
    available = [
        AvailableDepartmentResponse.model_validate(department)
        for department in department_queries.get_departments(session)
        if department.active and department.id not in in_semester
    ]
    return ClubOverviewResponse(
        semester=ClubSemesterResponse.model_validate(semester),
        departments=departments,
        available_departments=available,
        roles=[ClubRoleResponse.model_validate(role) for role in roles],
        total_members=queries.count_semester_people(session, semester.id),
    )


@router.get("/roles", response_model=list[ClubRoleResponse], dependencies=[Depends(admin_guard)])
def get_club_roles(session: DB):
    return queries.get_roles(session)


@router.get(
    "/departments/{department_id:int}", response_model=ClubDepartmentResponse, dependencies=[Depends(admin_guard)]
)
def get_club_department(department_id: DatabaseId, session: DB):
    return _get_department(session, department_id)


@router.get(
    "/departments/{department_id:int}/roster",
    response_model=list[RosterEntryResponse],
    dependencies=[Depends(admin_guard)],
)
def get_club_department_roster(department_id: DatabaseId, session: DB, semester_id: UUID | None = None):
    """Everyone on the department's roster that semester, each with all the roles they hold there."""
    _get_department(session, department_id)
    semester = _admin_semester(session, semester_id)
    entries: dict[int, RosterEntryResponse] = {}
    for row in queries.get_memberships(session, semester.id, department_id):
        entry = entries.get(row.member_id)
        if entry is None:
            entry = entries[row.member_id] = RosterEntryResponse(
                member=ClubMemberResponse.model_validate(row.member), roles=[], added_at=row.created_at
            )
        entry.roles.append(row.role.key)
        if row.role.key == service.MEMBER_ROLE:
            entry.added_at = row.created_at
    return list(entries.values())


@router.get("/history", response_model=ClubChangesResponse, dependencies=[Depends(admin_guard)])
def get_club_history(
    session: DB,
    semester_id: UUID | None = None,
    department_id: Annotated[int | None, Query(gt=0, le=4294967295)] = None,
    member_id: Annotated[int | None, Query(gt=0, le=4294967295)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Who added or removed which role, newest first. Without ``semester_id``, every semester."""
    if semester_id is not None:
        _admin_semester(session, semester_id)
    if department_id is not None:
        _get_department(session, department_id)
    if member_id is not None and session.scalar(select(Members.id).where(Members.id == member_id)) is None:
        raise MemberNotFound(member_id)
    changes = queries.get_changes(
        session,
        semester_id=str(semester_id) if semester_id else None,
        department_id=department_id,
        member_id=member_id,
        limit=limit + 1,
        offset=offset,
    )
    return ClubChangesResponse(
        items=[ClubChangeResponse.model_validate(change) for change in changes[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(changes) > limit,
    )


# ---------- department settings (not per semester) ----------


@router.post(
    "/departments", status_code=201, response_model=ClubDepartmentResponse, dependencies=[Depends(super_admin_guard)]
)
def create_club_department(payload: service.DepartmentSettings, session: DB, semester_id: UUID | None = None):
    """Create a department; it becomes part of ``semester_id`` (default: the current semester)."""
    semester = _admin_semester(session, semester_id)
    department = service.create_department(session, payload, semester.id)
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


# ---------- the structure of one semester ----------


@router.post(SCOPE, status_code=201, response_model=ClubDepartmentResponse, dependencies=[Depends(super_admin_guard)])
def add_club_department_to_semester(semester_id: UUID, department_id: DatabaseId, session: DB):
    scope = service.add_department_to_semester(session, str(semester_id), department_id)
    return _commit_department(session, scope.department)


@router.delete(SCOPE, response_model=RemovedResponse, dependencies=[Depends(super_admin_guard)])
def remove_club_department_from_semester(semester_id: UUID, department_id: DatabaseId, session: DB):
    service.remove_department_from_semester(session, str(semester_id), department_id)
    _commit_and_refresh(session)
    return RemovedResponse(removed=1)


@router.post(SCOPE + "/members", status_code=201, response_model=MembershipResponse)
def add_club_member(
    semester_id: UUID, department_id: DatabaseId, payload: AddMemberRequest, session: DB, actor: ManagementActor
):
    row = service.add_member(session, str(semester_id), department_id, payload.member_id, actor=actor)
    result = MembershipResponse.model_validate(row)
    _commit_and_refresh(session)
    return result


@router.delete(SCOPE + "/members/{member_id:int}", response_model=RemovedResponse)
def remove_club_member(
    semester_id: UUID, department_id: DatabaseId, member_id: DatabaseId, session: DB, actor: ManagementActor
):
    """Take someone off the department's roster, with every role they hold there."""
    removed = service.remove_member(session, str(semester_id), department_id, member_id, actor=actor)
    _commit_and_refresh(session)
    return RemovedResponse(removed=removed)


@router.put(SCOPE + "/members/{member_id:int}/roles/{role_key}", response_model=MembershipResponse)
def grant_club_role(
    semester_id: UUID,
    department_id: DatabaseId,
    member_id: DatabaseId,
    role_key: RoleKey,
    payload: GrantRoleRequest,
    session: DB,
    actor: ManagementActor,
):
    """Make someone leader or VP. They are added as a member too if they were not one."""
    row = service.grant_role(
        session,
        str(semester_id),
        department_id,
        member_id,
        role_key,
        actor=actor,
        replaces_member_id=payload.replaces_member_id,
    )
    result = MembershipResponse.model_validate(row)
    _commit_and_refresh(session)
    return result


@router.delete(SCOPE + "/members/{member_id:int}/roles/{role_key}", response_model=RemovedResponse)
def revoke_club_role(
    semester_id: UUID,
    department_id: DatabaseId,
    member_id: DatabaseId,
    role_key: RoleKey,
    session: DB,
    actor: ManagementActor,
):
    """Take a leader or VP role away; the person stays a member."""
    service.revoke_role(session, str(semester_id), department_id, member_id, role_key, actor=actor)
    _commit_and_refresh(session)
    return RemovedResponse(removed=1)


@router.post("/semesters/{semester_id}/copy-from/{source_semester_id}", response_model=CopySemesterResponse)
def copy_club_structure(semester_id: UUID, source_semester_id: UUID, session: DB, actor: ManagementActor):
    """Start an empty semester from another semester's departments and roster."""
    copied = service.copy_semester(session, str(semester_id), str(source_semester_id), actor=actor)
    _commit_and_refresh(session)
    return CopySemesterResponse(copied=copied)
