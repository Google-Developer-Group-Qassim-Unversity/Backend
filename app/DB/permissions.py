"""Reads and writes behind the access resolver (app/services/permissions/access.py)."""

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

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


# ---------- management (the permissions screens) ----------


def list_super_admins(session: Session) -> Sequence[SuperAdmins]:
    return session.scalars(
        select(SuperAdmins).options(selectinload(SuperAdmins.member)).order_by(SuperAdmins.added_at)
    ).all()


def count_super_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(SuperAdmins)) or 0


def remove_super_admin(session: Session, member_id: int) -> None:
    session.execute(delete(SuperAdmins).where(SuperAdmins.member_id == member_id))
    session.flush()


def set_shared_permissions(session: Session, permissions: set[str], by: int) -> None:
    """Replace the whole set, keeping the rows (and their added_by) of permissions that stay."""
    current = get_shared_permissions(session)
    if gone := current - permissions:
        session.execute(delete(SharedPermissions).where(SharedPermissions.permission.in_(gone)))
    session.add_all(SharedPermissions(permission=p, added_by=by) for p in permissions - current)
    session.flush()


def get_all_department_permissions(session: Session) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for department_id, permission in session.execute(
        select(DepartmentPermissions.department_id, DepartmentPermissions.permission)
    ).all():
        out.setdefault(department_id, set()).add(permission)
    return out


def set_department_permissions(session: Session, department_id: int, permissions: set[str], by: int) -> None:
    current = get_department_permissions(session, {department_id}).get(department_id, set())
    if gone := current - permissions:
        session.execute(
            delete(DepartmentPermissions).where(
                DepartmentPermissions.department_id == department_id, DepartmentPermissions.permission.in_(gone)
            )
        )
    session.add_all(
        DepartmentPermissions(department_id=department_id, permission=p, added_by=by) for p in permissions - current
    )
    session.flush()


def get_active_departments(session: Session) -> Sequence[Departments]:
    return session.scalars(select(Departments).where(Departments.active == 1).order_by(Departments.id)).all()


def list_grants(
    session: Session, semester_id: str, department_id: int, include_revoked: bool
) -> Sequence[PermissionGrants]:
    statement = (
        select(PermissionGrants)
        .where(PermissionGrants.semester_id == semester_id, PermissionGrants.department_id == department_id)
        .options(
            selectinload(PermissionGrants.member),
            selectinload(PermissionGrants.granter),
            selectinload(PermissionGrants.revoker),
        )
        .order_by(PermissionGrants.granted_at.desc(), PermissionGrants.id.desc())
    )
    if not include_revoked:
        statement = statement.where(PermissionGrants.revoked_at.is_(None))
    return session.scalars(statement).all()


def list_member_grants(session: Session, semester_id: str, member_id: int) -> Sequence[PermissionGrants]:
    """The member's grants this semester that still stand, in every department."""
    return session.scalars(
        select(PermissionGrants)
        .where(
            PermissionGrants.semester_id == semester_id,
            PermissionGrants.member_id == member_id,
            PermissionGrants.revoked_at.is_(None),
        )
        .options(selectinload(PermissionGrants.granter))
        .order_by(PermissionGrants.granted_at.desc(), PermissionGrants.id.desc())
    ).all()


def get_active_grant(
    session: Session, semester_id: str, department_id: int, member_id: int, permission: str
) -> PermissionGrants | None:
    return session.scalar(
        select(PermissionGrants).where(
            PermissionGrants.semester_id == semester_id,
            PermissionGrants.department_id == department_id,
            PermissionGrants.member_id == member_id,
            PermissionGrants.permission == permission,
            PermissionGrants.revoked_at.is_(None),
        )
    )


def create_grant(
    session: Session, semester_id: str, department_id: int, member_id: int, permission: str, by: int
) -> PermissionGrants:
    grant = PermissionGrants(
        semester_id=semester_id, department_id=department_id, member_id=member_id, permission=permission, granted_by=by
    )
    session.add(grant)
    session.flush()
    session.refresh(grant)
    return grant
