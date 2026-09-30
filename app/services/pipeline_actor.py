"""Who may act for a department in the events pipeline, until it moves onto app/services/permissions.

- A department's **leader and VPs** in the current semester (``club_memberships``) act for it.
- **Super admins** act for every department.
"""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.DB import permissions as queries
from app.DB.schema import Members
from app.dependencies import DB
from app.exceptions import DepartmentForbidden
from app.helpers import CurrentMember, authenticated_guard, is_super_admin
from app.semesters import current_semester

OFFICER_ROLES = ("leader", "vp")


@dataclass
class PipelineActor:
    """The caller, with every department they can act for worked out once per request."""

    member: Members
    is_super_admin: bool
    officer_of: set[int] = field(default_factory=set)
    member_of: set[int] = field(default_factory=set)

    @property
    def acting_department_ids(self) -> set[int]:
        return self.officer_of

    def can_act_for(self, department_id: int) -> bool:
        return self.is_super_admin or department_id in self.acting_department_ids

    def require_act_for(self, department_id: int) -> None:
        if not self.can_act_for(department_id):
            raise DepartmentForbidden(department_id)

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
    return actor


def get_pipeline_actor(session: DB, member: CurrentMember, credentials=Depends(authenticated_guard)) -> PipelineActor:
    return build_actor(session, member, is_super_admin(credentials))


Actor = Annotated[PipelineActor, Depends(get_pipeline_actor)]
