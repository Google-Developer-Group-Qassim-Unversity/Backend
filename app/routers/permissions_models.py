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
