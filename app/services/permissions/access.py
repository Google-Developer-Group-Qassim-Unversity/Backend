"""What one person can do in the admin app, worked out from our database alone.

Clerk only says who someone is. Everything else comes from four places:

1. the **current semester** (``app/semesters.py:current_semester``)
2. its **roster**: who is leader, VP or member of which department
3. ``super_admins``
4. the permission assignments: ``shared_permissions`` (every leader and VP),
   ``department_permissions`` (one department's leaders and VPs) and
   ``permission_grants`` (one member, one department, one semester)
5. the events pipeline's teams, found by department name
   (``app/DB/pipeline_teams.py``)

The rules:

- A **super admin** can do everything, whether or not they are on the roster.
- Anyone on the current roster is **staff**: they can open the admin app and
  have the staff basics.
- A **leader or VP** of department D has, for D, the shared permissions plus
  D's department permissions, plus its team's permissions when D is the
  pipeline's Design, Logistics or Media team (``TEAM_PERMISSIONS``).
- A **member** of D has, for D, what they were granted for D this semester.
  A grant stops counting when the semester ends or they leave D's roster.
- Everyone else is a regular user, with nothing here.
"""

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.DB import permissions as queries
from app.DB import pipeline_teams as team_queries
from app.DB.schema import Members
from app.exceptions import PermissionDenied
from app.semesters import current_semester
from app.services.permissions.catalogue import CATALOGUE, OFFICER_ROLES, STAFF_BASICS, TEAM_PERMISSIONS, Perm, parse


@dataclass(frozen=True)
class Access:
    member_id: int | None = None
    is_super_admin: bool = False
    semester_id: str | None = None
    # {department id: role keys} on the current semester's roster.
    roles: dict[int, frozenset[str]] = field(default_factory=dict)
    # {department id: permissions held for that department}. Basics are not repeated here.
    held: dict[int, frozenset[Perm]] = field(default_factory=dict)

    @property
    def is_staff(self) -> bool:
        return self.is_super_admin or bool(self.roles)

    def can(self, perm: Perm, department_id: int | None = None) -> bool:
        """Whether the caller has ``perm``.

        For a ``dept`` permission, pass the department the thing belongs to;
        without one it asks "in any department". A ``club`` permission ignores
        ``department_id``: holding it through any department is enough.
        """
        if self.is_super_admin:
            return True
        if not self.is_staff:
            return False
        if perm in STAFF_BASICS:
            return True
        if department_id is not None and CATALOGUE[perm].scope == "dept":
            return perm in self.held.get(department_id, frozenset())
        return any(perm in perms for perms in self.held.values())

    def can_any(self, perm: Perm, department_ids: frozenset[int]) -> bool:
        """Whether the caller has ``perm`` for at least one of ``department_ids``.

        For a thing that belongs to departments, such as an event. A thing that
        belongs to none is for super admins only.
        """
        if self.is_super_admin:
            return True
        return any(self.can(perm, department_id) for department_id in department_ids)

    def require(self, perm: Perm, department_id: int | None = None) -> None:
        if not self.can(perm, department_id):
            raise PermissionDenied(perm.value)

    def require_any(self, perm: Perm, department_ids: frozenset[int]) -> None:
        if not self.can_any(perm, department_ids):
            raise PermissionDenied(perm.value)

    def departments_for(self, perm: Perm) -> frozenset[int] | None:
        """The departments the caller has ``perm`` for, to filter a list by. ``None`` means every department."""
        if self.is_super_admin:
            return None
        return frozenset(d for d, perms in self.held.items() if perm in perms)

    def permissions(self) -> frozenset[Perm]:
        """Every permission the caller has somewhere."""
        if self.is_super_admin:
            return frozenset(Perm)
        if not self.is_staff:
            return frozenset()
        return STAFF_BASICS.union(*self.held.values())


# Why a department's leader, VP or member holds a permission there.
Source = Literal["shared", "department", "team", "grant"]


@dataclass(frozen=True)
class Explanation:
    """Everything ``resolve_access`` worked out, with where each permission came from.

    ``Access`` is built from this, so the member-lookup screen and the real
    checks cannot disagree.
    """

    member_id: int | None = None
    is_super_admin: bool = False
    semester_id: str | None = None
    roles: dict[int, frozenset[str]] = field(default_factory=dict)
    # {department id: {permission: why}}. Every roster department is a key, even with nothing held.
    sources: dict[int, dict[Perm, frozenset[Source]]] = field(default_factory=dict)

    def access(self) -> Access:
        return Access(
            member_id=self.member_id,
            is_super_admin=self.is_super_admin,
            semester_id=self.semester_id,
            roles=self.roles,
            held={d: frozenset(perms) for d, perms in self.sources.items()},
        )


def resolve_access(session: Session, member: Members | None) -> Access:
    return explain_access(session, member).access()


def explain_access(session: Session, member: Members | None) -> Explanation:
    if member is None:
        return Explanation()

    super_admin = queries.is_super_admin(session, member.id)
    semester = current_semester(session)
    if semester is None:
        return Explanation(member_id=member.id, is_super_admin=super_admin)

    roles: dict[int, set[str]] = {}
    for department_id, role_key in queries.get_roster_roles(session, semester.id, member.id):
        roles.setdefault(department_id, set()).add(role_key)

    officer_of = {d for d, keys in roles.items() if keys & OFFICER_ROLES}
    shared = parse(queries.get_shared_permissions(session)) if officer_of else frozenset()
    by_department = queries.get_department_permissions(session, officer_of)
    teams = team_queries.get_teams(session) if officer_of else {}
    by_team: dict[int, set[Perm]] = {}
    for team, department in teams.items():
        by_team.setdefault(department.id, set()).update(TEAM_PERMISSIONS[team])
    grants = queries.get_active_grants(session, semester.id, member.id)

    sources: dict[int, dict[Perm, frozenset[Source]]] = {}
    for department_id in roles:
        found: dict[Perm, set[Source]] = {}

        def add(perms, source: Source) -> None:
            for perm in perms:
                found.setdefault(perm, set()).add(source)

        add(parse(grants.get(department_id, ())), "grant")
        if department_id in officer_of:
            add(shared, "shared")
            add(parse(by_department.get(department_id, ())), "department")
            add(by_team.get(department_id, ()), "team")
        sources[department_id] = {perm: frozenset(why) for perm, why in found.items()}

    return Explanation(
        member_id=member.id,
        is_super_admin=super_admin,
        semester_id=semester.id,
        roles={d: frozenset(keys) for d, keys in roles.items()},
        sources=sources,
    )
