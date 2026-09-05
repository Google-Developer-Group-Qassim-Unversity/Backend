import re

from pydantic import BaseModel, HttpUrl, EmailStr, field_validator, conlist, ConfigDict
from typing import List, Literal, Dict
from datetime import datetime
from pydantic.types import JsonValue
from app.config import config
from app.DB.schema import EventsLocationType, MembersGender, RoleType, FormType

# A bare Google Meet code, e.g. "abc-defg-hij" - no scheme, no domain.
_MEET_CODE_RE = re.compile(r"^[a-zA-Z]{2,5}-[a-zA-Z]{2,5}-[a-zA-Z]{2,5}$")


class BaseClassModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Events_model(BaseClassModel):
    id: int | None = None
    name: str
    description: str | None = None
    location_type: EventsLocationType
    location: str
    start_datetime: datetime
    end_datetime: datetime
    status: Literal["draft", "open", "active", "closed"]
    image_url: str | None = None
    meeting_url: str | None = None
    is_official: int | None = None
    created_at: datetime | None = None


class Form_model(BaseClassModel):
    id: int | None = None
    event_id: int
    form_type: FormType
    google_form_id: str | None = None
    google_watch_id: str | None = None
    google_responders_url: str | None = None
    admin_google_email: str | None = None
    granted_emails: list[str] = []


class AttachFormRequest(BaseModel):
    admin_google_email: EmailStr

    @field_validator("admin_google_email")
    @classmethod
    def _must_be_allowed_domain(cls, value: EmailStr) -> EmailStr:
        domain = value.split("@")[-1].lower()
        if domain not in config.GOOGLE_ALLOWED_EMAIL_DOMAINS:
            allowed = ", ".join(config.GOOGLE_ALLOWED_EMAIL_DOMAINS)
            raise ValueError(f"email domain '{domain}' is not allowed - must be one of: {allowed}")
        return value


class createEvent_model(BaseClassModel):
    event: Events_model
    form_type: Literal["google", "none", "registration"]
    department_action_id: int
    member_action_id: int
    department_id: int


class event_actions_model(BaseClassModel):
    action_id: int
    ar_action_name: str
    department_id: int | None = None
    department_ar_name: str | None = None


class EventDetailsModel(BaseClassModel):
    event: Events_model
    actions: conlist(event_actions_model, min_length=1)  # pyright: ignore[reportInvalidTypeForm]


class UpdateEventModel(BaseClassModel):
    event: Events_model
    actions: conlist(event_actions_model, min_length=1)  # pyright: ignore[reportInvalidTypeForm]


class UpdateEventStatus_model(BaseClassModel):
    status: Literal["draft", "open", "active", "closed"]


class UpdateEventMeetingUrl_model(BaseClassModel):
    """The join link for a remote event. `None` (or blank) clears it.

    Deliberately permissive: an admin may paste a full link, a bare domain,
    or just a Google Meet code (e.g. "abc-defg-hij"), and it is normalized
    into something clickable rather than rejected.
    """

    meeting_url: str | None = None

    @field_validator("meeting_url")
    def normalize(cls, v: str | None):
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if _MEET_CODE_RE.match(v):
            v = f"https://meet.google.com/{v.lower()}"
        elif "://" not in v:
            v = f"https://{v}"
        # Reject non-http(s) schemes (e.g. "javascript://") - this is rendered
        # directly as a link's href on the leaderboard app.
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("meeting_url must use http:// or https://")
        if len(v) > 500:
            raise ValueError("meeting_url must be at most 500 characters")
        return v


class Open_Events_model(Events_model):
    form_type: Literal["google", "none", "registration"]
    form_id: int
    google_responders_url: str | None = None


class submission_exists_model(BaseClassModel):
    submission_status: Literal[False, True, "partial"]
    submission_timestamp: datetime | None = None


class submission_accept_model(BaseClassModel):
    submission_id: int
    is_accepted: bool


def _validate_optional_uni_id(value: str | None) -> str | None:
    if value is None:
        return value
    if len(str(value)) != 9:
        raise ValueError("uni_id must be a 9-digit integer")
    return value


class Member_model(BaseClassModel):
    id: int | None = None
    name: str
    email: EmailStr
    phone_number: str | None
    uni_id: str | None = None
    clerk_user_id: str | None = None
    gender: MembersGender
    uni_level: int | None = None
    uni_college: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_authenticated: bool | None = None

    @field_validator("uni_id")
    def validate_uni_id(cls, value):
        return _validate_optional_uni_id(value)

    # This validator is needed but We have some fucked up data in the database
    # so i'll will comment it out for now.

    # @field_validator("phone_number")
    # def validate_phone_number(cls, value):
    #     if not value:
    #         return value
    #     if len(str(value)) != 10:
    #         raise ValueError("phone_number must contain 10 digits")
    #     return value


class MemberWithActivity_model(Member_model):
    last_activity: datetime | None = None


class CreatedMemberModel(BaseClassModel):
    member: Member_model
    already_exists: bool


class MemberWithRole_model(Member_model):
    role: RoleType


class Get_Submission_model(BaseClassModel):
    member: Member_model
    submission_id: int
    submitted_at: datetime
    form_type: Literal["google", "none", "registration"]
    submission_type: Literal["none", "partial", "google", "registration"]
    is_accepted: bool
    is_invited: bool
    google_submission_value: JsonValue | None = None
    event_id: int
    form_id: int
    google_form_id: str | None = None


class Create_Google_Submission_model(BaseClassModel):
    id: int | None = None
    form_id: int
    member_id: int
    submission_type: Literal["none", "partial", "google", "registration"]
    is_accepted: bool
    submitted_at: datetime | None = None
    google_submission_id: str | None = None
    google_submission_value: JsonValue | None = None


class MemberHistory_model(BaseClassModel):
    name: str
    description: str | None = None
    location: str
    location_type: Literal["online", "on-site"]
    start_datetime: datetime
    end_datetime: datetime
    action_name: str


class Department_model(BaseClassModel):
    id: int | None = None
    name: str
    ar_name: str
    type: Literal["administrative", "practical"]


class Action_model(BaseClassModel):
    id: int
    action_name: str
    ar_action_name: str
    action_type: Literal["composite", "department", "member", "bonus"]
    points: int
    order: int = 0
    is_hidden: bool = False


class ActionWithUsage_model(BaseClassModel):
    id: int
    action_name: str
    ar_action_name: str
    action_type: Literal["composite", "department", "member", "bonus"]
    points: int
    usage_count: int = 0
    order: int = 0
    is_hidden: bool = False


class CreateAction_model(BaseClassModel):
    action_name: str
    ar_action_name: str
    action_type: Literal["composite", "department", "member", "bonus"]
    points: int


class UpdateAction_model(BaseClassModel):
    action_name: str | None = None
    ar_action_name: str | None = None
    action_type: Literal["composite", "department", "member", "bonus"] | None = None
    points: int | None = None
    is_hidden: bool | None = None


class ReorderActions_model(BaseClassModel):
    action_orders: List[Dict[str, int]]


class Categorized_action(BaseClassModel):
    composite_actions: List[conlist(Action_model, min_length=2, max_length=2)]  # pyright: ignore[reportInvalidTypeForm]
    department_actions: List[Action_model]
    member_actions: List[Action_model]
    custom_actions: List[Action_model]


class ManualMemberCreateModel(BaseModel):
    name: str
    email: EmailStr
    phone_number: str | None = None
    uni_id: str | None = None
    gender: MembersGender

    @field_validator("uni_id")
    def validate_uni_id(cls, value):
        return _validate_optional_uni_id(value)


class BatchCreateMemberItem(BaseClassModel):
    name: str
    email: EmailStr
    phone_number: str | None = None
    uni_id: str | None = None
    gender: MembersGender
    uni_level: int | None = None
    uni_college: str | None = None

    @field_validator("uni_id")
    def validate_uni_id(cls, value):
        return _validate_optional_uni_id(value)


class BatchCreateMembersRequest(BaseModel):
    members: list[BatchCreateMemberItem]


class BatchCreateMembersResponse(BaseClassModel):
    created_count: int
    existing_count: int
    failed_count: int
    members: list[Member_model]


class manual_members(BaseClassModel):
    members_sheet: HttpUrl


class ConflictResponse(BaseClassModel):
    detail: str


class NotFoundResponse(BaseClassModel):
    detail: str


class BadRequestResponse(BaseClassModel):
    detail: str


class InternalServerErrorResponse(BaseClassModel):
    detail: str


class Complex_EventData(BaseClassModel):
    event_info: Events_model
    department_discount: int
    department_bonus: int
    member_discount: int
    member_bonus: int


class CompositeEventData(Complex_EventData):
    department_id: int
    members_attendance: str
    department_action_id: int
    member_action_id: int

    @field_validator("members_attendance")
    def file_or_url(cls, v: str):
        if v.startswith("https://"):
            if "docs.google.com/spreadsheets" not in v and not v.endswith("output=csv"):
                raise ValueError("The Url must be a Google Sheets link with 'output=csv' parameter")
            else:
                return HttpUrl(v)
        elif v.endswith(".xlsx") or v.endswith(".csv"):
            return v
        else:
            raise ValueError(
                "members_attendance must be a valid file path ending with .xlsx or .csv, or a Google Sheets URL"
            )


class BaseEventReport(BaseClassModel):
    event: Events_model
    days: int
    department: str
    department_points: int


class CompositeEventReport(BaseEventReport):
    members_count: int
    members_points: int


class DepartmentEventData(BaseClassModel):
    event_info: Events_model
    department_id: int
    action_id: int
    bonus: int


class CardData(BaseClassModel):
    name: str
    url: str


class customeDepartmentsPoints_model(BaseClassModel):
    department_id: int
    points: int
    action_id: int
    events_id: int


class MemberUpdateModel(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone_number: str | None = None
    gender: Literal["Male", "Female"] | None = None
    uni_level: int | None = None
    uni_college: str | None = None


class AttendanceRecord_model(BaseClassModel):
    Member: Member_model
    dates: list[datetime]


class EventAttendanceResponse(BaseClassModel):
    attendance_count: int
    attendance: list[AttendanceRecord_model] | None = None


class ManualAttendanceRequest(BaseModel):
    member_ids: list[int]
    day: int | None = None
    days: list[int] | None = None


class CopyAttendanceRequest(BaseModel):
    source_day: int
    target_days: list[int]


class BackfillAttendanceRequest(BaseModel):
    members: list[Member_model]
    day: int


class BackfillAttendanceResponse(BaseClassModel):
    created_count: int
    existing_count: int
    already_attended_count: int
    marked_count: int
    attendance_date: datetime


class AttendanceDateEntry(BaseClassModel):
    date: datetime
    attended: bool


class EventWithAttendance_model(Events_model):
    attendance_dates: list[AttendanceDateEntry]


class MemberEvents_model(BaseClassModel):
    attended: list[EventWithAttendance_model]
    participated: list[Events_model]
