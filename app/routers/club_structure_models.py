"""Explicit admin API contracts, separate from the public department payload."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.DB.schema import ClubAssignmentRole, DepartmentsType
from app.services.club_structure import DepartmentSettings


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_utc)]
MemberId = Annotated[int, Field(strict=True, gt=0, le=4294967295)]


class ClubMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ClubAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    member_id: int
    department_id: int | None
    role: ClubAssignmentRole
    president_slot: int | None
    starts_at: UtcDateTime
    ends_at: UtcDateTime | None
    changed_by: str
    ended_by: str | None
    member: ClubMemberResponse


class ClubDepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ar_name: str
    type: DepartmentsType
    active: bool
    color: str
    icon: str
    leadership_enabled: bool
    created_at: UtcDateTime | None
    updated_at: UtcDateTime


class DepartmentCardResponse(ClubDepartmentResponse):
    member_count: int
    leader: ClubAssignmentResponse | None
    deputy: ClubAssignmentResponse | None


class PresidentSeatResponse(BaseModel):
    slot: int = Field(ge=1, le=2)
    assignment: ClubAssignmentResponse | None


class ClubOverviewResponse(BaseModel):
    departments: list[DepartmentCardResponse]
    presidents: list[PresidentSeatResponse]
    total_members: int


class PublicClubDepartmentResponse(BaseModel):
    id: int
    name: str
    ar_name: str
    type: DepartmentsType
    color: str
    icon: str
    leadership_enabled: bool
    leader: str | None
    deputy: str | None
    members: list[str]


class PublicClubStructureResponse(BaseModel):
    presidents: list[str]
    departments: list[PublicClubDepartmentResponse]


class TenureHistoryResponse(BaseModel):
    items: list[ClubAssignmentResponse]
    limit: int
    offset: int
    has_more: bool


class AddDepartmentMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: MemberId


class ReplaceAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Both fields are required, even when null: omission must not silently
    # turn a missing member into a removal or a missing expectation into a fill.
    member_id: MemberId | None
    expected_assignment_id: MemberId | None


class UpdateDepartmentRequest(DepartmentSettings):
    # PUT replaces all editable settings. Never reset customized appearance
    # just because a caller omitted these fields.
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(min_length=1, max_length=32)
