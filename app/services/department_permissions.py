"""Who may act for a department in the events pipeline.

- A department's **leader and VPs** in the current semester (``club_memberships``)
  act for it, and are the only ones who can grant that to other members of it.
- A member they **granted** it to acts for the department too, but cannot pass it on.
- **Super admins** act for, and grant in, every department.

Club roles still never grant admin-app access; this is a separate permission
that only the pipeline routes read.
"""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.DB import department_permissions as queries
from app.DB.schema import DepartmentPermissions, Members
from app.dependencies import DB
from app.exceptions import DepartmentForbidden, PipelineConflict
from app.helpers import CurrentMember, authenticated_guard, is_super_admin
from app.semesters import current_semester
from app.services import event_pipeline_clock as clock

OFFICER_ROLES = ("leader", "vp")


@dataclass
class PipelineActor:
    """The caller, with every department they can act for worked out once per request."""

    member: Members
    is_super_admin: bool
    officer_of: set[int] = field(default_factory=set)
    member_of: set[int] = field(default_factory=set)
    granted: set[int] = field(default_factory=set)

    @property
    def acting_department_ids(self) -> set[int]:
        return self.officer_of | self.granted

    def can_act_for(self, department_id: int) -> bool:
        return self.is_super_admin or department_id in self.acting_department_ids

    def can_grant(self, department_id: int) -> bool:
        return self.is_super_admin or department_id in self.officer_of

    def require_act_for(self, department_id: int) -> None:
        if not self.can_act_for(department_id):
            raise DepartmentForbidden(department_id)

    def require_grant(self, department_id: int) -> None:
        if not self.can_grant(department_id):
            raise DepartmentForbidden(department_id, "grant access in")

    @property
    def has_pipeline_access(self) -> bool:
        return self.is_super_admin or bool(self.acting_department_ids)


def build_actor(session: Session, member: Members, super_admin: bool) -> PipelineActor:
    actor = PipelineActor(member=member, is_super_admin=super_admin)
    semester = current_semester(session)
    if semester is not None:
        for department_id, role_key in queries.get_roster_roles(session, semester.id, member.id):
            actor.member_of.add(department_id)
            if role_key in OFFICER_ROLES:
                actor.officer_of.add(department_id)
    actor.granted = queries.get_active_grant_department_ids(session, member.id)
    return actor


def get_pipeline_actor(session: DB, member: CurrentMember, credentials=Depends(authenticated_guard)) -> PipelineActor:
    return build_actor(session, member, is_super_admin(credentials))


Actor = Annotated[PipelineActor, Depends(get_pipeline_actor)]


def grant(session: Session, actor: PipelineActor, department_id: int, member_id: int) -> DepartmentPermissions:
    actor.require_grant(department_id)
    semester = current_semester(session)
    roster = queries.get_department_roster(session, semester.id, department_id) if semester else []
    roles = {row.role.key for row in roster if row.member_id == member_id}
    if not roles:
        raise PipelineConflict(
            "not_a_department_member", f"Member {member_id} is not in department {department_id} this semester"
        )
    if roles & set(OFFICER_ROLES):
        raise PipelineConflict("already_officer", f"Member {member_id} already acts for the department as an officer")
    if queries.get_active_grant(session, department_id, member_id) is not None:
        raise PipelineConflict(
            "already_granted", f"Member {member_id} already has access to department {department_id}"
        )
    return queries.create_grant(session, department_id, member_id, actor.member.id)


def revoke(session: Session, actor: PipelineActor, department_id: int, grant_id: int) -> None:
    actor.require_grant(department_id)
    row = queries.get_grant(session, grant_id)
    if row is None or row.department_id != department_id or row.revoked_at is not None:
        raise PipelineConflict("grant_not_found", f"No active grant {grant_id} in department {department_id}", 404)
    queries.revoke_grant(session, row, actor.member.id, clock.now())
