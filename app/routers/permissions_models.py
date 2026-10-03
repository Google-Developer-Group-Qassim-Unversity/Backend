from pydantic import BaseModel, Field

from app.routers.club_structure_models import UtcDateTime


class CataloguePermission(BaseModel):
    key: str
    scope: str  # "club" | "dept"
    label: str
    ar_label: str


class PermissionKeys(BaseModel):
    permissions: list[str] = Field(max_length=100)


class DepartmentAssignment(BaseModel):
    department_id: int
    name: str
    ar_name: str
    permissions: list[str]


class AssignmentsResponse(BaseModel):
    shared: list[str]
    departments: list[DepartmentAssignment]


class PersonRef(BaseModel):
    member_id: int
    name: str


class SuperAdminEntry(PersonRef):
    added_at: UtcDateTime
    added_by: PersonRef | None


class AddSuperAdminRequest(BaseModel):
    member_id: int = Field(gt=0)


class GrantEntry(BaseModel):
    id: int
    member: PersonRef
    permission: str
    granted_by: PersonRef
    granted_at: UtcDateTime
    revoked_by: PersonRef | None
    revoked_at: UtcDateTime | None


class DepartmentGrantsResponse(BaseModel):
    department_id: int
    # The permissions the caller can grant here.
    grantable: list[str]
    # Plain members of the department this semester: who can be granted.
    members: list[PersonRef]
    grants: list[GrantEntry]


class GrantRequest(BaseModel):
    member_id: int = Field(gt=0)
    permission: str = Field(min_length=1, max_length=64)


class MemberSemester(BaseModel):
    id: str
    name: str


class HeldPermission(BaseModel):
    permission: str
    # Why: "shared" (every leader and VP), "department" (this department's leaders and VPs),
    # "team" (the department is a pipeline team), "grant" (a leader or VP granted it).
    sources: list[str]
    # Set when one of the sources is "grant".
    granted_by: PersonRef | None
    granted_at: UtcDateTime | None


class MemberDepartmentAccess(BaseModel):
    department_id: int
    name: str
    ar_name: str
    # The department's own colour (#rrggbb) and Lucide icon key, as on the club structure page.
    color: str
    icon: str
    roles: list[str]
    permissions: list[HeldPermission]


class MemberAccessResponse(BaseModel):
    """What one member can do, and why. Super admins only."""

    member: PersonRef
    is_super_admin: bool
    # On the current semester's roster (or a super admin): can open the admin app.
    is_staff: bool
    semester: MemberSemester | None
    # The staff basics: held by everyone on the roster. Empty for anyone else.
    basics: list[str]
    departments: list[MemberDepartmentAccess]
    # Every permission held somewhere. A super admin holds them all.
    permissions: list[str]
