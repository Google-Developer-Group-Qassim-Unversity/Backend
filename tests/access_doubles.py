"""Stand-ins for ``Access`` in tests that are about a route, not about who holds which permission.

Tests of the permission model itself use real rosters and tables
(tests/routers/test_access.py). These doubles let the rest of the suite say
"an admin" or "a super admin" without building a department and roster first.
"""

from dataclasses import dataclass, field

from app.services.permissions.access import Access
from app.services.permissions.catalogue import STAFF_BASICS, Perm

# What the old Clerk "admin" flag allowed: everything except what was super-admin only.
SUPER_ADMIN_ONLY_BEFORE = frozenset(
    {
        Perm.CLUB_STRUCTURE_MANAGE,
        Perm.CLUB_STRUCTURE_MANAGE_ROSTER,
        Perm.SEMESTERS_MANAGE,
        Perm.MEMBERS_CREATE,
        Perm.PERMISSIONS_MANAGE,
        Perm.POINTS_CATALOGUE,
        Perm.ATTENDANCE_COPY,
    }
)
ADMIN_PERMS = frozenset(Perm) - SUPER_ADMIN_ONLY_BEFORE


@dataclass(frozen=True)
class StaffEverywhere(Access):
    """A staff member (not a super admin) holding ``granted`` in every department."""

    granted: frozenset[Perm] = frozenset()
    roles: dict[int, frozenset[str]] = field(default_factory=lambda: {0: frozenset({"leader", "member"})})

    def can(self, perm: Perm, department_id: int | None = None) -> bool:
        return perm in STAFF_BASICS or perm in self.granted

    def can_any(self, perm: Perm, department_ids: frozenset[int]) -> bool:
        return self.can(perm)

    def departments_for(self, perm: Perm) -> frozenset[int] | None:
        return None if self.can(perm) else frozenset()

    def permissions(self) -> frozenset[Perm]:
        return STAFF_BASICS | self.granted


SUPER_ADMIN = Access(is_super_admin=True)
ADMIN = StaffEverywhere(granted=ADMIN_PERMS)
POINTS_ADMIN = StaffEverywhere(granted=ADMIN_PERMS | {Perm.POINTS_CATALOGUE})
