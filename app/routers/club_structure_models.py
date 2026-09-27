"""Club structure API contracts. Admin shapes are separate from the public payload."""

from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.DB.schema import ClubMembershipAction, DepartmentsType
from app.services.club_structure import DepartmentSettings


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_utc)]
MemberId = Annotated[int, Field(strict=True, gt=0, le=4294967295)]


class ClubMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ClubRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    name: str
    ar_name: str
    max_holders: int | None
    sort_order: int


class ClubSemesterResponse(BaseModel):
    """The semester a club structure response describes."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    hijri_code: int
    gregorian_code: int
    name: str
    start_date: date
    end_date: date


class ClubDepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ar_name: str
    type: DepartmentsType
    active: bool
    color: str
    icon: str
    show_in_leaderboard: bool
    is_club_leadership: bool
    created_at: UtcDateTime | None
    updated_at: UtcDateTime


class RoleSeatsResponse(BaseModel):
    """Who holds one role (leader, VP) in a department, and how many seats it has."""

    key: str
    max_holders: int | None
    holders: list[ClubMemberResponse]


class DepartmentCardResponse(ClubDepartmentResponse):
    # The name the department had in this semester, when it differed from today's.
    semester_name: str | None
    semester_ar_name: str | None
    member_count: int
    roles: list[RoleSeatsResponse]


class AvailableDepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ar_name: str


class ClubOverviewResponse(BaseModel):
    semester: ClubSemesterResponse
    departments: list[DepartmentCardResponse]
    # Active departments not part of this semester yet, for "add to semester".
    available_departments: list[AvailableDepartmentResponse]
    roles: list[ClubRoleResponse]
    total_members: int


class RosterEntryResponse(BaseModel):
    """One person on a department's roster, with every role they hold there."""

    member: ClubMemberResponse
    roles: list[str]
    added_at: UtcDateTime


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    semester_id: str
    department_id: int
    member_id: int
    role_id: str
    created_by: str
    created_at: UtcDateTime


class ClubChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    semester_id: str
    department_id: int
    action: ClubMembershipAction
    actor: str
    created_at: UtcDateTime
    member: ClubMemberResponse
    role: ClubRoleResponse
    department: AvailableDepartmentResponse


class ClubChangesResponse(BaseModel):
    items: list[ClubChangeResponse]
    limit: int
    offset: int
    has_more: bool


class CopySemesterResponse(BaseModel):
    copied: int


class RemovedResponse(BaseModel):
    removed: int


class PublicClubSemesterResponse(BaseModel):
    code: int
    gregorian_code: int
    name: str


class PublicClubDepartmentResponse(BaseModel):
    id: int
    name: str
    ar_name: str
    type: DepartmentsType
    color: str
    icon: str
    show_in_leaderboard: bool
    # Kept for the leaderboard app until it moves off it: true when the
    # department has a leader or VP this semester.
    leadership_enabled: bool
    leader: str | None
    # The VP. Named `deputy` because the leaderboard app reads that key.
    deputy: str | None
    # Everyone else on the roster; leaders and VPs are listed above, not here.
    members: list[str]


class PublicClubStructureResponse(BaseModel):
    semester: PublicClubSemesterResponse
    # The leaders of the club leadership department.
    presidents: list[str]
    departments: list[PublicClubDepartmentResponse]


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: MemberId


class GrantRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The current holder to take the role from when every seat is taken.
    replaces_member_id: MemberId | None = None


class UpdateDepartmentRequest(DepartmentSettings):
    # PUT replaces all editable settings. Never reset customized appearance or
    # the ranking flag just because a caller omitted these fields.
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(min_length=1, max_length=32)
    show_in_leaderboard: bool
