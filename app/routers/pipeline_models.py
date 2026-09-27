"""Events pipeline API contracts."""

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.DB.schema import (
    EventRequestAudience,
    EventRequestLocationScope,
    EventRequestRegistration,
    EventRequestStage,
    EventRequestTaskStatus,
    EventRequestType,
    EventRequestUndatedReason,
    PipelineTeam,
)
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


class CalendarDayRequest(BaseModel):
    id: int
    department: PipelineDepartment
    title: str | None
    stage: EventRequestStage


class CalendarDayResponse(BaseModel):
    date: date
    # locked | banned | open | held | booked | published
    status: str
    reason: str | None = None
    requests: list[CalendarDayRequest] = []


class CalendarResponse(BaseModel):
    today: date
    first_bookable_date: date
    days: list[CalendarDayResponse]


class BanDaysRequest(BaseModel):
    dates: list[date] = Field(min_length=1, max_length=366)
    reason: str | None = Field(default=None, max_length=200)


class UnbanDaysRequest(BaseModel):
    dates: list[date] = Field(min_length=1, max_length=366)


class BanResult(BaseModel):
    count: int


DayMode = Literal["on_site", "online"]


class BookRequest(BaseModel):
    department_id: int = Field(gt=0)
    start_date: date
    end_date: date


class RedateRequest(BaseModel):
    start_date: date
    end_date: date


class EventDetails(BaseModel):
    """The event itself, filled in once by the requesting team. Everything is optional until submit."""

    title: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    event_type: EventRequestType | None = None
    presenter_name: str | None = Field(default=None, max_length=100)
    presenter_email: EmailStr | None = None
    # One mode per booked day, keyed by the day.
    day_modes: dict[date, DayMode] | None = None
    daily_start_time: time | None = None
    daily_end_time: time | None = None
    is_official: bool | None = None
    location_scope: EventRequestLocationScope | None = None
    audience: EventRequestAudience | None = None
    registration: EventRequestRegistration | None = None
    expected_accepted: int | None = Field(default=None, ge=1, le=100000)
    help_needed: str | None = Field(default=None, max_length=5000)


class UpdateDetailsRequest(EventDetails):
    """Only the fields sent are changed; send ``null`` to clear one."""

    partner_department_ids: list[int] | None = None


class PersonRef(BaseModel):
    member_id: int
    name: str


class EventRequestSummary(BaseModel):
    id: int
    department: PipelineDepartment
    stage: EventRequestStage
    title: str | None
    start_date: date | None
    end_date: date | None
    hold_expires_at: UtcDateTime | None
    undated_reason: EventRequestUndatedReason | None
    created_at: UtcDateTime


class TaskResponse(BaseModel):
    team: PipelineTeam
    status: EventRequestTaskStatus
    brief: dict | None
    brief_version: int | None
    opened_at: UtcDateTime | None
    completed_at: UtcDateTime | None
    completed_by: PersonRef | None


class EventRequestDetail(EventRequestSummary):
    created_by: PersonRef
    details: EventDetails
    partners: list[PipelineDepartment]
    # Worked out from the dates and times: Sun-Thu, 08:00-15:00.
    within_official_hours: bool | None
    submitted_at: UtcDateTime | None
    updated_at: UtcDateTime
    event_id: int | None
    can_edit: bool
    tasks: list[TaskResponse]
    # What submit still needs: "details.title", "design.idea", "logistics.venue", ...
    missing: list[str]
    now: UtcDateTime


class PaginatedEventRequests(BaseModel):
    items: list[EventRequestSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class SaveBriefRequest(BaseModel):
    """A draft brief, saved as it is. Its fields are the team's form (app/services/event_briefs.py)."""

    brief: dict
