"""The Design and Logistics briefs, and what a request needs before it can be submitted.

The briefs replace the two departments' old Google Forms (Notion: Events
pipeline → Brief forms). Each is a versioned Pydantic model; the answers are
stored as JSON on the team's task row with ``brief_version``, so a later
change to a form is a V2 model rather than a migration.

Drafts are saved as they are, however incomplete. ``missing_fields`` says
what submit still needs, so the page can show a checklist all along.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.DB.schema import EventRequestAudience, EventRequestRegistration, EventRequests, PipelineTeam

BRIEF_VERSION = 1

# The venue list from the old Logistics form. Fixed in code for now.
VENUES = (
    "مسرح شطر الطلاب (180)",
    "مسرح شطر الطالبات",
    "التيك فالي (60)",
    "قاعة بكلية البنات",
    "قاعة بكلية العيال",
    "القاعة المتوسطة بالمؤتمرات (700)",
    "القاعة الكبرى بالمؤتمرات (2200)",
    "قاعة المعرفة كلية الطلاب",
    "معمل الامن السيبراني",
    "بيت الثقافة",
)

DesignType = Literal["poster", "slides", "posts", "reports", "prints", "other"]
DesignSize = Literal["square", "landscape", "portrait", "other"]
FileType = Literal["png", "jpeg", "pdf", "powerpoint", "other"]
ContentStatus = Literal["final", "needs_wording"]
Service = Literal["sponsorship", "organizing", "volunteers"]
VenueNeed = Literal["devices", "internet", "audio"]

Links = list[str]


class DesignBriefV1(BaseModel):
    design_type: DesignType
    design_type_other: str | None = Field(default=None, max_length=200)
    size: DesignSize | None = None
    size_other: str | None = Field(default=None, max_length=200)
    idea: str = Field(min_length=1, max_length=5000)
    file_type: FileType | None = None
    file_type_other: str | None = Field(default=None, max_length=200)
    content_status: ContentStatus
    # The text that goes inside the design. No design is made without clear content.
    content: str = Field(min_length=1, max_length=10000)
    instructions: str | None = Field(default=None, max_length=5000)
    # Links to images, logos and reference designs (Drive, etc.) until uploads move to the new API layer.
    image_links: Links = Field(default_factory=list, max_length=20)
    reference_links: Links = Field(default_factory=list, max_length=20)


class LogisticsBriefV1(BaseModel):
    # Asked when any day is online.
    meet_link_by_logistics: bool | None = None
    # Asked when any day is on-site.
    venue: str | None = Field(default=None, max_length=200)
    room: str | None = Field(default=None, max_length=100)
    services: list[Service] = Field(default_factory=list)
    venue_needs: list[VenueNeed] = Field(default_factory=list)
    # Asked when a day is on-site and the audience includes female students.
    buses_needed: bool | None = None
    notes: str | None = Field(default=None, max_length=5000)


BRIEF_MODELS: dict[PipelineTeam, type[BaseModel]] = {
    PipelineTeam.DESIGN: DesignBriefV1,
    PipelineTeam.LOGISTICS: LogisticsBriefV1,
}


def _modes(request: EventRequests) -> set[str]:
    return set((request.day_modes or {}).values())


def _booked_days(request: EventRequests) -> list[str]:
    if request.start_date is None or request.end_date is None:
        return []
    days = []
    day: date = request.start_date
    while day <= request.end_date:
        days.append(day.isoformat())
        day = date.fromordinal(day.toordinal() + 1)
    return days


def details_missing(request: EventRequests) -> list[str]:
    missing = []
    for field in ("title", "description", "event_type", "presenter_name", "location_scope", "audience", "registration"):
        if not getattr(request, field):
            missing.append(f"details.{field}")
    if request.is_official is None:
        missing.append("details.is_official")
    if request.daily_start_time is None:
        missing.append("details.daily_start_time")
    if request.daily_end_time is None:
        missing.append("details.daily_end_time")
    if request.daily_start_time and request.daily_end_time and request.daily_end_time <= request.daily_start_time:
        missing.append("details.daily_end_time")
    days = _booked_days(request)
    if not days:
        missing.append("dates")
    elif set(days) - set((request.day_modes or {}).keys()):
        missing.append("details.day_modes")
    if "online" in _modes(request) and not request.presenter_email:
        missing.append("details.presenter_email")
    if request.registration == EventRequestRegistration.ACCEPTANCE and not request.expected_accepted:
        missing.append("details.expected_accepted")
    return missing


def brief_missing(request: EventRequests, team: PipelineTeam, brief: dict | None) -> list[str]:
    prefix = f"{team.value}."
    try:
        parsed = BRIEF_MODELS[team].model_validate(brief or {})
    except ValidationError as error:
        return sorted({prefix + str(e["loc"][0]) for e in error.errors() if e["loc"]})

    missing = []
    if isinstance(parsed, DesignBriefV1):
        for choice, other in (
            ("design_type", "design_type_other"),
            ("size", "size_other"),
            ("file_type", "file_type_other"),
        ):
            if getattr(parsed, choice) == "other" and not getattr(parsed, other):
                missing.append(prefix + other)
    elif isinstance(parsed, LogisticsBriefV1):
        modes = _modes(request)
        if "online" in modes and parsed.meet_link_by_logistics is None:
            missing.append(prefix + "meet_link_by_logistics")
        if "on_site" in modes:
            if not parsed.venue:
                missing.append(prefix + "venue")
            includes_female = request.audience in (EventRequestAudience.FEMALE, EventRequestAudience.MIXED)
            if includes_female and parsed.buses_needed is None:
                missing.append(prefix + "buses_needed")
    return missing


def missing_fields(request: EventRequests) -> list[str]:
    """Everything submit still needs, as ``details.<field>``, ``design.<field>``, ``logistics.<field>``."""
    briefs = {task.team: task.brief for task in request.tasks}
    missing = details_missing(request)
    for team in (PipelineTeam.DESIGN, PipelineTeam.LOGISTICS):
        missing += brief_missing(request, team, briefs.get(team))
    return missing


def clean_brief(team: PipelineTeam, brief: dict) -> dict:
    """Keep only the fields the team's form knows, so a draft cannot store junk."""
    known = set(BRIEF_MODELS[team].model_fields)
    return {key: value for key, value in brief.items() if key in known}
