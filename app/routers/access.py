"""What the signed-in person can do. Both frontends read this; see app/services/permissions/access.py."""

from fastapi import APIRouter, Depends, status

from app.config import config
from app.DB import members as member_queries
from app.DB import permissions as queries
from app.DB.schema import Semesters
from app.dependencies import DB
from app.exceptions import NotFound
from app.helpers import authenticated_guard
from app.routers.access_models import (
    AccessDepartment,
    AccessForEvent,
    AccessMe,
    AccessSemester,
    StagingSignInAnswer,
    StagingSignInCheck,
)
from app.services.permissions.catalogue import CATALOGUE, Perm
from app.services.permissions.departments import event_departments
from app.services.permissions.access import resolve_access
from app.services.permissions.dependencies import CurrentAccess
from app.services.permissions.guards import Staff

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


@router.get(
    "/events/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=AccessForEvent,
    dependencies=[Depends(Staff)],
)
def get_my_access_for_event(event_id: int, session: DB, access: CurrentAccess):
    """What the caller can do to one event, so a page can show only the buttons that will work."""
    departments = event_departments(session, {"event_id": event_id})
    return AccessForEvent(
        event_id=event_id,
        permissions=sorted(
            p for p in Perm if (access.can_any(p, departments) if CATALOGUE[p].scope == "dept" else access.can(p))
        ),
    )


@router.post("/staging-sign-in", status_code=status.HTTP_200_OK, response_model=StagingSignInAnswer)
def check_staging_sign_in(body: StagingSignInCheck, session: DB):
    """Whether an email may use staging's fixed sign-in code: it belongs to current staff.

    Asked by the admin frontend's /api/staging-sign-in before it mints a Clerk
    ticket. Public, because the caller is not signed in yet, so it exists only
    on staging and local dev; everywhere else it is a 404. See
    Backend/docs/STAGING.md.
    """
    if config.ENV not in ("staging", "development"):
        raise NotFound("route", "/access/staging-sign-in")
    member = member_queries.get_member_by_email_or_none(session, body.email)
    return StagingSignInAnswer(is_staff=member is not None and resolve_access(session, member).is_staff)
