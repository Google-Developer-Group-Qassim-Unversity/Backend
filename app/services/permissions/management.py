"""Changing who holds what: shared and department permissions, grants, super admins.

The rules for grants:

- A grant is for one member, one department and one permission, for the
  current semester only.
- Only permissions that department's leaders and VPs have can be granted there
  (shared ∪ the department's own), and a leader or VP can grant only what they
  hold for that department. Super admins can grant any of them.
- The member must be on that department's roster this semester, as a plain
  member: leaders and VPs already have everything grantable.
- Granting and managing permissions are never grantable, so a granted member
  cannot pass anything on.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.DB import club_structure as club_queries
from app.DB import members as member_queries
from app.DB import permissions as queries
from app.DB.schema import Departments, PermissionGrants, Semesters
from app.exceptions import NotFound, PermissionConflict, PermissionDenied
from app.semesters import current_semester
from app.services.permissions.access import Access
from app.services.permissions.catalogue import OFFICER_ROLES, Perm, parse

NEVER_GRANTED = frozenset({Perm.PERMISSIONS_GRANT, Perm.PERMISSIONS_MANAGE})


def _parse_all(keys: list[str]) -> set[str]:
    unknown = sorted(set(keys) - {p.value for p in Perm})
    if unknown:
        raise PermissionConflict("unknown_permission", f"Unknown permission(s): {', '.join(unknown)}", 422)
    return set(keys)


def current_semester_or_conflict(session: Session) -> Semesters:
    semester = current_semester(session)
    if semester is None:
        raise PermissionConflict("no_semester", "There is no current semester to grant in")
    return semester


# ---------- assignments ----------


def set_shared(session: Session, keys: list[str], by: int) -> None:
    queries.set_shared_permissions(session, _parse_all(keys), by)


def set_department(session: Session, department_id: int, keys: list[str], by: int) -> None:
    if session.get(Departments, department_id) is None:
        raise NotFound("Department", department_id)
    queries.set_department_permissions(session, department_id, _parse_all(keys), by)


# ---------- grants ----------


def grantable(session: Session, access: Access, department_id: int) -> frozenset[Perm]:
    """What the caller can grant in this department."""
    officers_have = parse(queries.get_shared_permissions(session)) | parse(
        queries.get_department_permissions(session, {department_id}).get(department_id, set())
    )
    candidates = officers_have - NEVER_GRANTED
    if access.is_super_admin:
        return candidates
    return frozenset(p for p in candidates if access.can(p, department_id))


def plain_members(session: Session, semester_id: str, department_id: int) -> dict[int, str]:
    """{member id: name} of the department's roster this semester who are not a leader or VP."""
    roles: dict[int, set[str]] = {}
    names: dict[int, str] = {}
    for row in club_queries.get_memberships(session, semester_id, department_id):
        roles.setdefault(row.member_id, set()).add(row.role.key)
        names[row.member_id] = row.member.name
    return {m: names[m] for m, keys in roles.items() if not keys & OFFICER_ROLES}


def grant(session: Session, access: Access, by: int, department_id: int, member_id: int, key: str) -> PermissionGrants:
    semester = current_semester_or_conflict(session)
    perm = Perm(next(iter(_parse_all([key]))))
    if perm not in grantable(session, access, department_id):
        raise PermissionDenied(perm.value)
    if member_id not in plain_members(session, semester.id, department_id):
        member_queries.get_member_by_id(session, member_id)  # 404 when there is no such member
        raise PermissionConflict(
            "not_a_plain_member",
            f"Member {member_id} is not a plain member of department {department_id} this semester",
        )
    if queries.get_active_grant(session, semester.id, department_id, member_id, perm.value) is not None:
        raise PermissionConflict("already_granted", f"Member {member_id} already has {perm.value} here")
    return queries.create_grant(session, semester.id, department_id, member_id, perm.value, by)


def revoke(session: Session, by: int, department_id: int, grant_id: int) -> None:
    row = session.get(PermissionGrants, grant_id)
    if row is None or row.department_id != department_id or row.revoked_at is not None:
        raise PermissionConflict("grant_not_found", f"No active grant {grant_id} in department {department_id}", 404)
    row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.revoked_by = by
    session.flush()


# ---------- super admins ----------


def add_super_admin(session: Session, member_id: int, by: int | None) -> None:
    member_queries.get_member_by_id(session, member_id)  # 404 when there is no such member
    if queries.is_super_admin(session, member_id):
        raise PermissionConflict("already_super_admin", f"Member {member_id} is already a super admin")
    queries.add_super_admin(session, member_id, by)


def remove_super_admin(session: Session, member_id: int, by: int | None) -> None:
    if not queries.is_super_admin(session, member_id):
        raise PermissionConflict("not_super_admin", f"Member {member_id} is not a super admin", 404)
    if member_id == by:
        raise PermissionConflict("cannot_remove_self", "Super admins cannot remove themselves")
    if queries.count_super_admins(session) <= 1:
        raise PermissionConflict("last_super_admin", "The last super admin cannot be removed")
    queries.remove_super_admin(session, member_id)
