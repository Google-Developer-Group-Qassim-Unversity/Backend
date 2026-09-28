"""Reads and writes for who may act for a department in the events pipeline."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import ClubMemberships, ClubRoles, DepartmentPermissions, Departments, PipelineTeam, PipelineTeams


def get_roster_roles(session: Session, semester_id: str, member_id: int) -> Sequence[tuple[int, str]]:
    """(department id, role key) for every role the member holds in the semester."""
    rows = session.execute(
        select(ClubMemberships.department_id, ClubRoles.key)
        .join(ClubRoles, ClubRoles.id == ClubMemberships.role_id)
        .where(ClubMemberships.semester_id == semester_id, ClubMemberships.member_id == member_id)
    ).all()
    return [(department_id, key) for department_id, key in rows]


def get_department_roster(session: Session, semester_id: str, department_id: int) -> Sequence[ClubMemberships]:
    return session.scalars(
        select(ClubMemberships)
        .where(ClubMemberships.semester_id == semester_id, ClubMemberships.department_id == department_id)
        .options(selectinload(ClubMemberships.member), selectinload(ClubMemberships.role))
        .order_by(ClubMemberships.created_at)
    ).all()


def get_active_grant_department_ids(session: Session, member_id: int) -> set[int]:
    return set(
        session.scalars(
            select(DepartmentPermissions.department_id).where(
                DepartmentPermissions.member_id == member_id, DepartmentPermissions.revoked_at.is_(None)
            )
        ).all()
    )


def get_active_grants(session: Session, department_id: int) -> Sequence[DepartmentPermissions]:
    return session.scalars(
        select(DepartmentPermissions)
        .where(DepartmentPermissions.department_id == department_id, DepartmentPermissions.revoked_at.is_(None))
        .options(selectinload(DepartmentPermissions.member), selectinload(DepartmentPermissions.granter))
        .order_by(DepartmentPermissions.granted_at)
    ).all()


def get_active_grant(session: Session, department_id: int, member_id: int) -> DepartmentPermissions | None:
    return session.scalar(
        select(DepartmentPermissions).where(
            DepartmentPermissions.department_id == department_id,
            DepartmentPermissions.member_id == member_id,
            DepartmentPermissions.revoked_at.is_(None),
        )
    )


def get_grant(session: Session, grant_id: int) -> DepartmentPermissions | None:
    return session.get(DepartmentPermissions, grant_id)


def create_grant(session: Session, department_id: int, member_id: int, granted_by: int) -> DepartmentPermissions:
    grant = DepartmentPermissions(department_id=department_id, member_id=member_id, granted_by=granted_by)
    session.add(grant)
    session.flush()
    session.refresh(grant)
    return grant


def revoke_grant(session: Session, grant: DepartmentPermissions, revoked_by: int, now: datetime) -> None:
    grant.revoked_at = now
    grant.revoked_by = revoked_by
    session.flush()


def get_departments(session: Session, ids: set[int] | None = None) -> Sequence[Departments]:
    """Active departments, or exactly ``ids`` whatever their state."""
    statement = select(Departments).order_by(Departments.id)
    if ids is None:
        statement = statement.where(Departments.active == 1)
    else:
        statement = statement.where(Departments.id.in_(ids))
    return session.scalars(statement).all()


def get_teams(session: Session) -> Sequence[PipelineTeams]:
    return session.scalars(select(PipelineTeams).options(selectinload(PipelineTeams.department))).all()


def get_team_department_id(session: Session, team: PipelineTeam) -> int | None:
    return session.scalar(select(PipelineTeams.department_id).where(PipelineTeams.team == team))


def set_teams(session: Session, mapping: dict[PipelineTeam, int | None]) -> None:
    """Replace the whole map. Deleting first keeps the unique department index happy during a swap."""
    for row in session.scalars(select(PipelineTeams)).all():
        session.delete(row)
    session.flush()
    for team, department_id in mapping.items():
        if department_id is not None:
            session.add(PipelineTeams(team=team, department_id=department_id))
    session.flush()
