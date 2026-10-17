import re

from pydantic import BaseModel, HttpUrl, EmailStr, field_validator, conlist, ConfigDict
from typing import List, Literal, Dict
from datetime import datetime
from pydantic.types import JsonValue
from app.member_names import validate_member_name
from app.config import config
from app.DB.schema import EventsContentLinkKind, EventHistoryAction, EventsLocationType, MembersGender, FormType
from app.routers.club_structure_models import UtcDateTime

# A bare Google Meet code, e.g. "abc-defg-hij" - no scheme, no domain.
_MEET_CODE_RE = re.compile(r"^[a-zA-Z]{2,5}-[a-zA-Z]{2,5}-[a-zA-Z]{2,5}$")


class BaseClassModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaginationMeta_model(BaseClassModel):
    """Shared page metadata for the paginated list endpoints."""

    total: int
    page: int
    page_size: int
    total_pages: int


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
    # On input: file the event under this semester instead of deriving it from
    # the end date. On output: the semester it is filed under.
    semester_id: str | None = None
    created_at: datetime | None = None


class EventsWithContentLinks_model(Events_model):
    """The read payloads that carry an event's published content links -
    ``GET /events`` and ``GET /events/{id}``. Every other payload stays lean:
    a write response without the key cannot be mistaken for a link-less
    event, and nothing accepts ``content_links`` back in a request body."""

    # Published content links (slides/recordings), attached by the read queries
    # in app/DB/events.py right before serialization; drafts never leave the
    # guarded admin payloads.
    content_links: list["EventContentLink_model"] = []


def _require_present(value) -> str:
    """An edit sends a value or omits the field; ``null`` means "clear" here no
    column supports. Field validators only run for present fields, so absent
    (defaults ``None``) never reaches this."""
    if value is None:
        raise ValueError("send the value to set, or omit the field - null is not accepted")
    return value


def _normalize_content_url(value: str) -> str:
    """Validate a pasted content-link URL: http(s) only, at most 500 chars.

    Same style as the meeting_url validator: an admin may paste a bare domain
    and it is normalized into something clickable, but any other scheme
    (e.g. "javascript:", "ftp:") is rejected - the URL is rendered directly as
    a link's href on the leaderboard app.
    """
    value = value.strip()
    # "host:8080" and "javascript:" are indistinguishable this cheaply, and a
    # content link on an odd port is never a real case here - reject both.
    if re.search(r"[\x00-\x1f]", value):
        raise ValueError("url must not contain control characters")
    if value.startswith("//"):
        raise ValueError("url must be an absolute http(s) URL")
    # The scheme is read from the start of the value, not searched for: a
    # redirect target such as "example.com/redirect?to=https://x.com" is a
    # bare domain that gets the prefix, while a leading "something:" that is
    # not http(s) stays rejected.
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", value):
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url must use http:// or https://")
    else:
        value = f"https://{value}"
    # A link that is nothing but a scheme, or whose address holds whitespace
    # ("https://", "https://exa mple .com"), points nowhere and never leaves
    # this validator.
    address = value.split("://", 1)[1]
    if not address or re.search(r"\s", address):
        raise ValueError("url must contain an address after the scheme")
    # Length applies to what will actually be stored, e.g. a 495-char bare
    # domain grows past 500 when the scheme is prepended.
    if len(value) > 500:
        raise ValueError("url must be at most 500 characters")
    return value


def _validate_content_label(value: str) -> str:
    """The display name shown on the website; the Drive filename is not."""
    value = value.strip()
    if not value:
        raise ValueError("label must not be empty")
    if len(value) > 200:
        raise ValueError("label must be at most 200 characters")
    return value


class EventContentLink_model(BaseClassModel):
    id: str | None = None
    label: str
    url: str
    kind: EventsContentLinkKind
    published: bool
    created_at: datetime | None = None
    # Canonical public direct-download URL for Drive-hosted files, derived at
    # read time (app/DB/events.py drive_download_url) - never a column, never
    # settable; a link that is not a recognizable Drive file leaves it null.
    download_url: str | None = None
    drive_file_id: str | None = None


class CreateEventContentLink_model(BaseClassModel):
    label: str

    @field_validator("label")
    def _label(cls, v: str):
        return _validate_content_label(v)

    url: str

    @field_validator("url")
    def _url(cls, v: str):
        return _normalize_content_url(v)

    kind: EventsContentLinkKind
    # Phase 1 has no approval gating: a manually pasted link goes live at once.
    published: bool = True
    drive_file_id: str | None = None


class UpdateEventContentLink_model(BaseClassModel):
    """Everything optional - a row is edited field by field, or deleted
    outright; it is never "cleared". An explicit ``null`` is a validation
    error, not an "unset this field": every column is required.

    The validators only run for fields present in the payload, so an absent
    field stays unchanged while an explicit ``null`` is rejected here - the
    two shapes can never collapse into one.
    """

    @field_validator("label", "url")
    def _present_or_absent(cls, v: str | None):
        return _require_present(v)

    label: str | None = None

    @field_validator("label")
    def _label(cls, v: str | None):
        return _validate_content_label(_require_present(v))

    url: str | None = None

    @field_validator("url")
    def _url(cls, v: str | None):
        return _normalize_content_url(_require_present(v))

    @field_validator("kind", "published")
    def _enum_present_or_absent(cls, v):
        return _require_present(v)

    kind: EventsContentLinkKind | None = None
    published: bool | None = None


class DriveRecordingCandidate_model(BaseClassModel):
    file_id: str
    web_view_link: str
    is_public: bool
    name: str
    created_time: datetime | str | None = None


class DriveRecordingCandidatesResponse(BaseClassModel):
    state: Literal["processing", "ready", "absent"]
    candidates: list[DriveRecordingCandidate_model] = []


class DriveFileMakePublicResponse_model(BaseClassModel):
    file_id: str
    is_public: bool


class DriveStorageQuotaResponse_model(BaseClassModel):
    configured: bool
    usage: int | None = None
    limit: int | None = None
    usage_in_drive: int | None = None
    usage_in_drive_trash: int | None = None
    usage_percentage: float | None = None


class EventListItem_model(Events_model):
    """Extra per-event data for the admin paginated list - department of the
    event and distinct attendees count."""

    department_id: int | None = None
    department_name: str | None = None
    department_ar_name: str | None = None
    attendance_count: int = 0


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


class EventPerson_model(BaseClassModel):
    member_id: int
    name: str


class EventDetailsModel(BaseClassModel):
    event: Events_model
    actions: conlist(event_actions_model, min_length=1)  # pyright: ignore[reportInvalidTypeForm]
    # Null on events created before these were recorded, or if the member was deleted.
    responsible: EventPerson_model | None = None
    created_by: EventPerson_model | None = None
    # Admin view: every content link, published or not. The leaderboard-serving
    # payloads carry published rows only.
    content_links: list[EventContentLink_model] = []


class EventHistoryItem(BaseClassModel):
    """One thing an admin did to the event (app/services/event_history.py)."""

    action: EventHistoryAction
    at: UtcDateTime
    actor: EventPerson_model | None
    details: dict | None


class EventHistoryResponse(BaseClassModel):
    # Oldest first.
    items: list[EventHistoryItem]
    # The pipeline request that published the event, whose history has the steps before.
    pipeline_request_id: str | None


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
    public_name: str | None = None
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


class MemberStats_model(BaseClassModel):
    total: int
    authenticated: int
    manual: int
    male: int
    female: int


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
    public_name: str | None = None

    @field_validator("name", "public_name", mode="before")
    @classmethod
    def validate_names(cls, value, info):
        return validate_member_name(value, max_length=50 if info.field_name == "name" else 150)

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


class ScanAttendanceRequest(BaseModel):
    uuid: str
    day: int | None = None


class ScanAttendanceResponse(BaseClassModel):
    status: Literal["marked", "already_marked"]
    member_id: int
    member_name: str
    uni_id: str | None = None


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


class PaginatedEvents_model(PaginationMeta_model):
    items: list[EventListItem_model]


class PaginatedMembers_model(PaginationMeta_model):
    items: list[MemberWithActivity_model]
