"""Real rosters, departments and sign-in for tests of permissions (no Access doubles)."""

from datetime import datetime

from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import delete

from app.config import config
from app.DB.club_structure import get_role_by_key
from app.DB.schema import (
    ClubMemberships,
    DepartmentPermissions,
    Departments,
    DepartmentsType,
    Members,
    MembersGender,
    PermissionGrants,
    SemesterDepartments,
    SharedPermissions,
    SuperAdmins,
)
from app.DB.semesters import get_semester_by_hijri_code
from app.main import app
from app.routers.models import createEvent_model
from app.services.events import create_full_event
from app.services.permissions.access import resolve_access
from app.services.permissions.catalogue import Perm
from tests.factories import make_create_event_payload

SHARED = {Perm.EVENTS_EDIT, Perm.PIPELINE_REQUEST, Perm.PERMISSIONS_GRANT}


class Club:
    """Builds departments, people and rosters, and signs in as any of them."""

    def __init__(self, session, client):
        self.session = session
        self.client = client
        current, previous = get_semester_by_hijri_code(session, 475), get_semester_by_hijri_code(session, 472)
        assert current and previous, "the migrations seed Summer 2026 (475) and Spring 2026 (472)"
        self.current, self.previous = current, previous
        self._counter = 0
        # Every test states the shared permissions it relies on; the migration's seeds are tested on their own.
        session.execute(delete(SharedPermissions))
        session.add_all(SharedPermissions(permission=p.value) for p in SHARED)
        session.flush()

    def department(self, name: str) -> Departments:
        department = Departments(name=name, ar_name=f"قسم {name}", type=DepartmentsType.PRACTICAL)
        self.session.add(department)
        self.session.flush()
        for semester in (self.current, self.previous):
            self.session.add(SemesterDepartments(semester_id=semester.id, department_id=department.id))
        self.session.flush()
        return department

    def person(self) -> Members:
        self._counter += 1
        member = Members(
            name=f"Person {self._counter}",
            email=f"access{self._counter}@example.com",
            uni_id=f"66{self._counter:07d}",
            clerk_user_id=f"clerk_access_{self._counter}",
            gender=MembersGender.MALE,
        )
        self.session.add(member)
        self.session.flush()
        return member

    def join(self, member: Members, department: Departments, *roles: str, semester=None) -> Members:
        """Put ``member`` on ``department``'s roster, always as a member plus any officer roles."""
        semester = semester or self.current
        for key in ("member", *roles):
            self.session.add(
                ClubMemberships(
                    semester_id=semester.id,
                    department_id=department.id,
                    member_id=member.id,
                    role_id=self._role_id(key),
                    created_by="test",
                )
            )
        self.session.flush()
        return member

    def _role_id(self, key: str) -> str:
        role = get_role_by_key(self.session, key)
        assert role is not None, key
        return role.id

    def department_permission(self, department: Departments, perm: Perm) -> None:
        self.session.add(DepartmentPermissions(department_id=department.id, permission=perm.value))
        self.session.flush()

    def grant(self, member, department, perm: Perm, by: Members, semester=None, revoked=False) -> None:
        semester = semester or self.current
        self.session.add(
            PermissionGrants(
                semester_id=semester.id,
                department_id=department.id,
                member_id=member.id,
                permission=perm.value,
                granted_by=by.id,
                revoked_at=datetime(2026, 7, 1) if revoked else None,
                revoked_by=by.id if revoked else None,
            )
        )
        self.session.flush()

    def super_admin(self, member: Members) -> Members:
        self.session.add(SuperAdmins(member_id=member.id))
        self.session.flush()
        return member

    def access(self, member: Members | None):
        return resolve_access(self.session, member)

    def sign_in(self, member: Members | None, metadata: dict | None = None):
        """Sign in as ``member`` (``None``: a Clerk user with no member row); only JWT verification is replaced."""
        subject = member.clerk_user_id if member else "clerk_no_row"
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="test-token", decoded={"sub": subject, "metadata": metadata or {}}
        )
        app.dependency_overrides[config.CLERK_GUARD] = lambda: credentials
        app.dependency_overrides[config.CLERK_GUARD_optional] = lambda: credentials
        self.session.commit()
        return self.client

    def event(self, department: Departments, seed_refs) -> int:
        payload = createEvent_model.model_validate(make_create_event_payload(seed_refs, department_id=department.id))
        creator = self.person()
        event, _log = create_full_event(self.session, payload, responsible_member_id=creator.id, created_by=creator.id)
        self.session.flush()
        return event.id

    def me(self, member: Members | None, metadata: dict | None = None) -> dict:
        """``GET /access/me`` signed in as ``member``."""
        response = self.sign_in(member, metadata).get("/access/me")
        assert response.status_code == 200, response.text
        return response.json()
