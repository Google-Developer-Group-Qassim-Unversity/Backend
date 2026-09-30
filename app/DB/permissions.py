"""Reads and writes behind the access resolver (app/services/permissions/access.py)."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB.schema import (
    ClubMemberships,
    ClubRoles,
    DepartmentPermissions,
    Departments,
    PermissionGrants,
    SharedPermissions,
    SuperAdmins,
)


def get_roster_roles(session: Session, semester_id: str, member_id: int) -> Sequence[tuple[int, str]]:
    """(department id, role key) for every role the member holds in the semester."""
    rows = session.execute(
        select(ClubMemberships.department_id, ClubRoles.key)
        .join(ClubRoles, ClubRoles.id == ClubMemberships.role_id)
        .where(ClubMemberships.semester_id == semester_id, ClubMemberships.member_id == member_id)
    ).all()
    return [(department_id, key) for department_id, key in rows]


def is_super_admin(session: Session, member_id: int) -> bool:
    return session.get(SuperAdmins, member_id) is not None


def add_super_admin(session: Session, member_id: int, added_by: int | None) -> SuperAdmins:
    row = SuperAdmins(member_id=member_id, added_by=added_by)
    session.add(row)
    session.flush()
    return row


def get_shared_permissions(session: Session) -> set[str]:
    return set(session.scalars(select(SharedPermissions.permission)).all())


def get_department_permissions(session: Session, department_ids: set[int]) -> dict[int, set[str]]:
    if not department_ids:
        return {}
    rows = session.execute(
        select(DepartmentPermissions.department_id, DepartmentPermissions.permission).where(
            DepartmentPermissions.department_id.in_(department_ids)
        )
    ).all()
    out: dict[int, set[str]] = {}
    for department_id, permission in rows:
        out.setdefault(department_id, set()).add(permission)
    return out


def get_active_grants(session: Session, semester_id: str, member_id: int) -> dict[int, set[str]]:
    """{department id: permissions} the member was granted this semester and still has."""
    rows = session.execute(
        select(PermissionGrants.department_id, PermissionGrants.permission).where(
            PermissionGrants.semester_id == semester_id,
            PermissionGrants.member_id == member_id,
            PermissionGrants.revoked_at.is_(None),
        )
    ).all()
    out: dict[int, set[str]] = {}
    for department_id, permission in rows:
        out.setdefault(department_id, set()).add(permission)
    return out


def get_departments(session: Session, ids: set[int]) -> Sequence[Departments]:
    if not ids:
        return []
    return session.scalars(select(Departments).where(Departments.id.in_(ids)).order_by(Departments.id)).all()
