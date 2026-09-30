"""The permissions screens: who holds what (super admins), and grants (leaders and VPs).

See app/services/permissions/management.py for the rules.
"""

import logging

from fastapi import APIRouter, Depends, status

from app.DB import permissions as queries
from app.DB.schema import Members, PermissionGrants
from app.dependencies import DB
from app.routers.permissions_models import (
    AddSuperAdminRequest,
    AssignmentsResponse,
    CataloguePermission,
    DepartmentAssignment,
    DepartmentGrantsResponse,
    GrantEntry,
    GrantRequest,
    PermissionKeys,
    PersonRef,
    SuperAdminEntry,
)
from app.routers.responses import DetailResponse
from app.services.permissions import management
from app.services.permissions.catalogue import CATALOGUE, Perm
from app.services.permissions.dependencies import CurrentCaller
from app.services.permissions.departments import path_departments
from app.services.permissions.guards import Require, Staff, SuperAdmin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/permissions", tags=["permissions"])

Manage = Depends(Require(Perm.PERMISSIONS_MANAGE))
GrantHere = Depends(Require(Perm.PERMISSIONS_GRANT, path_departments))


def _person(member: Members | None) -> PersonRef | None:
    return PersonRef(member_id=member.id, name=member.name) if member else None


def _grant(row: PermissionGrants) -> GrantEntry:
    return GrantEntry(
        id=row.id,
        member=PersonRef(member_id=row.member.id, name=row.member.name),
        permission=row.permission,
        granted_by=PersonRef(member_id=row.granter.id, name=row.granter.name),
        granted_at=row.granted_at,
        revoked_by=_person(row.revoker),
        revoked_at=row.revoked_at,
    )


# ---------- catalogue and assignments ----------


@router.get(
    "/catalogue",
    status_code=status.HTTP_200_OK,
    response_model=list[CataloguePermission],
    dependencies=[Depends(Staff)],
)
def get_permission_catalogue():
    return [
        CataloguePermission(key=perm.value, scope=info.scope, label=info.label, ar_label=info.ar_label)
        for perm, info in CATALOGUE.items()
    ]


@router.get("/assignments", status_code=status.HTTP_200_OK, response_model=AssignmentsResponse, dependencies=[Manage])
def get_permission_assignments(session: DB):
    """The shared permissions, and every active department's own."""
    by_department = queries.get_all_department_permissions(session)
    return AssignmentsResponse(
        shared=sorted(queries.get_shared_permissions(session)),
        departments=[
            DepartmentAssignment(
                department_id=d.id, name=d.name, ar_name=d.ar_name, permissions=sorted(by_department.get(d.id, set()))
            )
            for d in queries.get_active_departments(session)
        ],
    )


@router.put("/shared", status_code=status.HTTP_200_OK, response_model=AssignmentsResponse, dependencies=[Manage])
def set_shared_permissions(body: PermissionKeys, session: DB, caller: CurrentCaller):
    management.set_shared(session, body.permissions, caller.member.id)
    session.commit()
    logger.info("Shared permissions set to %s", sorted(body.permissions))
    return get_permission_assignments(session)


@router.put(
    "/departments/{department_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=AssignmentsResponse,
    dependencies=[Manage],
)
def set_department_permissions(department_id: int, body: PermissionKeys, session: DB, caller: CurrentCaller):
    management.set_department(session, department_id, body.permissions, caller.member.id)
    session.commit()
    logger.info("Department %s permissions set to %s", department_id, sorted(body.permissions))
    return get_permission_assignments(session)


# ---------- super admins ----------


@router.get(
    "/super-admins",
    status_code=status.HTTP_200_OK,
    response_model=list[SuperAdminEntry],
    dependencies=[Depends(SuperAdmin)],
)
def list_super_admins(session: DB):
    return [
        SuperAdminEntry(
            member_id=row.member.id,
            name=row.member.name,
            added_at=row.added_at,
            added_by=_person(session.get(Members, row.added_by) if row.added_by else None),
        )
        for row in queries.list_super_admins(session)
    ]


@router.post(
    "/super-admins",
    status_code=status.HTTP_201_CREATED,
    response_model=list[SuperAdminEntry],
    dependencies=[Depends(SuperAdmin)],
)
def add_super_admin(body: AddSuperAdminRequest, session: DB, caller: CurrentCaller):
    management.add_super_admin(session, body.member_id, caller.member.id)
    session.commit()
    logger.info("Member %s is now a super admin, added by %s", body.member_id, caller.member.id)
    return list_super_admins(session)


@router.delete(
    "/super-admins/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=list[SuperAdminEntry],
    dependencies=[Depends(SuperAdmin)],
)
def remove_super_admin(member_id: int, session: DB, caller: CurrentCaller):
    management.remove_super_admin(session, member_id, caller.member.id)
    session.commit()
    logger.info("Member %s is no longer a super admin, removed by %s", member_id, caller.member.id)
    return list_super_admins(session)


# ---------- grants ----------


@router.get(
    "/departments/{department_id:int}/grants",
    status_code=status.HTTP_200_OK,
    response_model=DepartmentGrantsResponse,
    dependencies=[GrantHere],
)
def get_department_grants(department_id: int, session: DB, caller: CurrentCaller, history: bool = False):
    """This semester's grants in the department (with ``history``, the revoked ones too)."""
    semester = management.current_semester_or_conflict(session)
    members = management.plain_members(session, semester.id, department_id)
    return DepartmentGrantsResponse(
        department_id=department_id,
        grantable=sorted(management.grantable(session, caller.access, department_id)),
        members=[PersonRef(member_id=m, name=name) for m, name in sorted(members.items(), key=lambda i: i[1])],
        grants=[_grant(row) for row in queries.list_grants(session, semester.id, department_id, history)],
    )


@router.post(
    "/departments/{department_id:int}/grants",
    status_code=status.HTTP_201_CREATED,
    response_model=GrantEntry,
    dependencies=[GrantHere],
)
def grant_permission(department_id: int, body: GrantRequest, session: DB, caller: CurrentCaller):
    row = management.grant(session, caller.access, caller.member.id, department_id, body.member_id, body.permission)
    session.commit()
    session.refresh(row)
    logger.info("Granted %s to member %s in department %s", body.permission, body.member_id, department_id)
    return _grant(row)


@router.delete(
    "/departments/{department_id:int}/grants/{grant_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=DetailResponse,
    dependencies=[GrantHere],
)
def revoke_permission(department_id: int, grant_id: int, session: DB, caller: CurrentCaller):
    management.revoke(session, caller.member.id, department_id, grant_id)
    session.commit()
    logger.info("Grant %s revoked in department %s", grant_id, department_id)
    return DetailResponse(detail="Grant revoked")
