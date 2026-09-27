"""Events pipeline API contracts."""

from pydantic import BaseModel, ConfigDict, Field

from app.DB.schema import PipelineTeam
from app.routers.club_structure_models import UtcDateTime

MemberId = int


class PipelineDepartment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ar_name: str
    color: str
    icon: str


class PipelineTeamEntry(BaseModel):
    team: PipelineTeam
    department: PipelineDepartment


class ActingDepartment(PipelineDepartment):
    """A department the caller can act for, and how."""

    is_officer: bool
    can_grant: bool
    teams: list[PipelineTeam]


class PipelineMeResponse(BaseModel):
    member_id: int
    name: str
    is_super_admin: bool
    has_access: bool
    departments: list[ActingDepartment]
    teams: list[PipelineTeamEntry]


class SetPipelineTeamsRequest(BaseModel):
    design: int | None = None
    logistics: int | None = None
    media: int | None = None


class PermissionPerson(BaseModel):
    member_id: int
    name: str


class PermissionOfficer(PermissionPerson):
    role: str


class PermissionGrant(PermissionPerson):
    id: int
    granted_by: PermissionPerson
    granted_at: UtcDateTime


class DepartmentPermissionsResponse(BaseModel):
    department: PipelineDepartment
    can_grant: bool
    officers: list[PermissionOfficer]
    grants: list[PermissionGrant]
    # Current members of the department who could be granted access.
    candidates: list[PermissionPerson]


class GrantPermissionRequest(BaseModel):
    member_id: int = Field(gt=0)
