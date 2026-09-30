"""What the signed-in person can do. Both frontends read this; see app/services/permissions/access.py."""

from fastapi import APIRouter, Depends, status

from app.DB import permissions as queries
from app.DB.schema import Semesters
from app.dependencies import DB
from app.helpers import authenticated_guard
from app.routers.access_models import AccessDepartment, AccessMe, AccessSemester
from app.services.permissions.catalogue import CATALOGUE
from app.services.permissions.dependencies import CurrentAccess

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/me", status_code=status.HTTP_200_OK, response_model=AccessMe, dependencies=[Depends(authenticated_guard)])
def get_my_access(session: DB, access: CurrentAccess):
    semester = session.get(Semesters, access.semester_id) if access.semester_id else None
    departments = queries.get_departments(session, set(access.roles))
    return AccessMe(
        member_id=access.member_id,
        is_staff=access.is_staff,
        is_super_admin=access.is_super_admin,
        semester=AccessSemester(id=semester.id, name=semester.name, hijri_code=semester.hijri_code)
        if semester
        else None,
        departments=[
            AccessDepartment(
                id=department.id,
                name=department.name,
                ar_name=department.ar_name,
                roles=sorted(access.roles[department.id]),
                permissions=sorted(p for p in access.held.get(department.id, ()) if CATALOGUE[p].scope == "dept"),
            )
            for department in departments
        ],
        permissions=sorted(access.permissions()),
    )
