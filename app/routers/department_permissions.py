"""Leaders and VPs grant members of their department access to the events pipeline."""

import logging

from fastapi import APIRouter, status

from app.DB import department_permissions as queries
from app.DB.schema import DepartmentPermissions, Departments
from app.dependencies import DB
from app.exceptions import NotFound
from app.routers.pipeline_models import (
    DepartmentPermissionsResponse,
    GrantPermissionRequest,
    PermissionGrant,
    PermissionOfficer,
    PermissionPerson,
    PipelineDepartment,
)
from app.routers.responses import DetailResponse
from app.semesters import current_semester
from app.services import department_permissions as service
from app.services.department_permissions import OFFICER_ROLES, Actor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/departments/{department_id:int}/permissions", tags=["events pipeline"])


def _grant_response(grant: DepartmentPermissions) -> PermissionGrant:
    return PermissionGrant(
        id=grant.id,
        member_id=grant.member_id,
        name=grant.member.name,
        granted_by=PermissionPerson(member_id=grant.granted_by, name=grant.granter.name),
        granted_at=grant.granted_at,
    )


@router.get("", status_code=status.HTTP_200_OK, response_model=DepartmentPermissionsResponse)
def get_department_permissions(department_id: int, session: DB, actor: Actor):
    actor.require_act_for(department_id)
    department = session.get(Departments, department_id)
    if department is None:
        raise NotFound("Department", department_id)

    semester = current_semester(session)
    roster = queries.get_department_roster(session, semester.id, department_id) if semester else []
    grants = queries.get_active_grants(session, department_id)

    officers = [
        PermissionOfficer(member_id=row.member_id, name=row.member.name, role=row.role.key)
        for row in roster
        if row.role.key in OFFICER_ROLES
    ]
    excluded = {o.member_id for o in officers} | {g.member_id for g in grants}
    candidates: dict[int, PermissionPerson] = {}
    for row in roster:
        if row.member_id not in excluded:
            candidates[row.member_id] = PermissionPerson(member_id=row.member_id, name=row.member.name)

    return DepartmentPermissionsResponse(
        department=PipelineDepartment.model_validate(department),
        can_grant=actor.can_grant(department_id),
        officers=officers,
        grants=[_grant_response(g) for g in grants],
        candidates=list(candidates.values()),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PermissionGrant)
def grant_department_permission(department_id: int, body: GrantPermissionRequest, session: DB, actor: Actor):
    grant = service.grant(session, actor, department_id, body.member_id)
    session.commit()
    logger.info("Member %s granted pipeline access to department %s", body.member_id, department_id)
    return _grant_response(grant)


@router.delete("/{grant_id:int}", status_code=status.HTTP_200_OK, response_model=DetailResponse)
def revoke_department_permission(department_id: int, grant_id: int, session: DB, actor: Actor):
    service.revoke(session, actor, department_id, grant_id)
    session.commit()
    logger.info("Pipeline grant %s revoked in department %s", grant_id, department_id)
    return DetailResponse(detail="Access revoked")
