"""What Design and Logistics hand over, and what each still needs before the team can finish.

A brief is what the requesting team asks a team for; a deliverable is what
that team hands back. Each is a versioned Pydantic model stored as JSON on the
team's task row with ``deliverable_version``, the way briefs are.

- **Logistics confirms the event as it was actually booked.** The request
  holds what the department wanted; the university may give another day,
  time or room. The confirmation opens prefilled from the request and the
  Logistics brief, and Logistics changes what differs. Publish reads it, so
  the confirmed version is the one that goes on the event. Whatever Logistics
  changed from what was asked (``changes``) needs a reason, which goes to
  the requesting department with the change.
- **Design uploads the poster.** It becomes the event's image.

Drafts are saved as they are, however incomplete; ``missing`` says what
finishing still needs, as ``confirm.<field>`` or ``poster.<field>``.
"""

from datetime import date, time
from urllib.parse import urlparse

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError

from app.DB.schema import EventRequests, EventRequestTasks, EventRequestTaskStatus, EventRequestType, PipelineTeam
from app.routers.pipeline_models import DayMode

DELIVERABLE_VERSION = 1


class LogisticsDeliverableV1(BaseModel):
    """The event as Logistics booked it. Everything is optional until Logistics confirms."""

    start_date: date | None = None
    end_date: date | None = None
    # One mode per day, keyed by the day: which days are on-site and which online.
    day_modes: dict[date, DayMode] | None = None
    daily_start_time: time | None = None
    daily_end_time: time | None = None
    # Asked when any day is on-site.
    venue: str | None = Field(default=None, max_length=200)
    room: str | None = Field(default=None, max_length=100)
    # Asked when any day is online. Typed in, or generated on the club's
    # calendar (app/services/google_meet.py), in which case ``meet_event_id``
    # is the calendar event behind it.
    meet_link: str | None = Field(default=None, max_length=500)
    meet_event_id: str | None = Field(default=None, max_length=200)
    event_type: EventRequestType | None = None
    description: str | None = Field(default=None, max_length=5000)
    # Why the confirmed event differs from the one requested. Required once anything in
    # ``CONFIRMED_FIELDS`` differs from the prefill; sent to the requesting department.
    change_reason: str | None = Field(default=None, max_length=1000)


# What the requesting department asked for and Logistics can confirm differently.
# The Meet link is Logistics' own to provide, so it is never a change.
CONFIRMED_FIELDS = (
    "start_date",
    "end_date",
    "day_modes",
    "daily_start_time",
    "daily_end_time",
    "venue",
    "room",
    "event_type",
    "description",
)


class DesignDeliverableV1(BaseModel):
    poster_url: str | None = Field(default=None, max_length=500)


DELIVERABLE_MODELS: dict[PipelineTeam, type[BaseModel]] = {
    PipelineTeam.DESIGN: DesignDeliverableV1,
    PipelineTeam.LOGISTICS: LogisticsDeliverableV1,
}
PREFIX = {PipelineTeam.DESIGN: "poster", PipelineTeam.LOGISTICS: "confirm"}


def days_of(start: date, end: date) -> list[date]:
    return [date.fromordinal(d) for d in range(start.toordinal(), end.toordinal() + 1)]


def is_web_link(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def logistics_prefill(request: EventRequests, brief: dict | None) -> LogisticsDeliverableV1:
    """The confirmation Logistics starts from: the request as asked, and the venue from its brief."""
    brief = brief or {}
    return LogisticsDeliverableV1(
        start_date=request.start_date,
        end_date=request.end_date,
        day_modes={date.fromisoformat(d): m for d, m in (request.day_modes or {}).items()} or None,
        daily_start_time=request.daily_start_time,
        daily_end_time=request.daily_end_time,
        venue=brief.get("venue"),
        room=brief.get("room"),
        event_type=request.event_type,
        description=request.description,
    )


def logistics_confirmation(request: EventRequests, task: EventRequestTasks | None) -> LogisticsDeliverableV1 | None:
    """What Logistics saved, or the prefill while it has saved nothing. ``None`` until the request reaches it."""
    if task is None:
        return None
    if task.deliverable is not None:
        return LogisticsDeliverableV1.model_validate(task.deliverable)
    return logistics_prefill(request, task.brief)


def _comparable(value):
    """A field's value as it is compared: blanks are nothing, text is trimmed, the rest is JSON."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()} or None
    return value.isoformat() if hasattr(value, "isoformat") else value


def changes(baseline: LogisticsDeliverableV1, confirmation: LogisticsDeliverableV1) -> list[dict]:
    """What Logistics confirmed differently from what was asked, as ``{"field", "from", "to"}``.

    A blank on either side is not a change: filling in what the request left
    open, or leaving open what it asked for (a blank that matters is reported
    as missing instead). Neither is the mode of each day once the dates
    themselves moved, since the days are then different ones.
    """
    before = baseline.model_dump(mode="json")
    after = confirmation.model_dump(mode="json")
    dates_moved = (before["start_date"], before["end_date"]) != (after["start_date"], after["end_date"])
    result = []
    for field in CONFIRMED_FIELDS:
        if field == "day_modes" and dates_moved:
            continue
        old, new = _comparable(before[field]), _comparable(after[field])
        if old is not None and new is not None and old != new:
            result.append({"field": field, "from": old, "to": new})
    return result


def logistics_changes(request: EventRequests, task: EventRequestTasks | None) -> list[dict]:
    """What the saved confirmation changes from the request, or nothing while nothing is saved."""
    if task is None or task.deliverable is None:
        return []
    return changes(logistics_prefill(request, task.brief), LogisticsDeliverableV1.model_validate(task.deliverable))


def shown_deliverable(request: EventRequests, task: EventRequestTasks) -> dict | None:
    """The deliverable as the request page shows it: Logistics' starts prefilled."""
    if task.team == PipelineTeam.LOGISTICS and task.status != EventRequestTaskStatus.BRIEF:
        confirmation = logistics_confirmation(request, task)
        return confirmation.model_dump(mode="json") if confirmation else None
    return task.deliverable


def missing(team: PipelineTeam, deliverable: dict | BaseModel | None, changed: list[dict] | None = None) -> list[str]:
    """Everything the team still has to hand over before it can finish.

    ``changed`` is what Logistics' confirmation changes from the request
    (``changes``); any change makes the reason required.
    """
    prefix = PREFIX[team] + "."
    model = DELIVERABLE_MODELS[team]
    try:
        parsed = (
            deliverable if isinstance(deliverable, model) else model.model_validate(deliverable or {})  # type: ignore[arg-type]
        )
    except ValidationError as error:
        return sorted({prefix + str(e["loc"][0]) for e in error.errors() if e["loc"]})

    if isinstance(parsed, DesignDeliverableV1):
        return [] if parsed.poster_url else [prefix + "poster_url"]

    assert isinstance(parsed, LogisticsDeliverableV1)
    result = []
    for field in ("start_date", "end_date", "daily_start_time", "daily_end_time", "event_type"):
        if getattr(parsed, field) is None:
            result.append(prefix + field)
    if not (parsed.description and parsed.description.strip()):
        result.append(prefix + "description")
    if parsed.start_date and parsed.end_date and parsed.end_date < parsed.start_date:
        result.append(prefix + "end_date")
    if parsed.daily_start_time and parsed.daily_end_time and parsed.daily_end_time <= parsed.daily_start_time:
        result.append(prefix + "daily_end_time")
    modes = parsed.day_modes or {}
    if parsed.start_date and parsed.end_date and parsed.end_date >= parsed.start_date:
        if set(days_of(parsed.start_date, parsed.end_date)) - set(modes):
            result.append(prefix + "day_modes")
    if "on_site" in modes.values() and not (parsed.venue and parsed.venue.strip()):
        result.append(prefix + "venue")
    if "online" in modes.values() and not (parsed.meet_link and is_web_link(parsed.meet_link)):
        result.append(prefix + "meet_link")
    if changed and not (parsed.change_reason and parsed.change_reason.strip()):
        result.append(prefix + "change_reason")
    return sorted(set(result), key=result.index)


def parse(team: PipelineTeam, deliverable: dict) -> dict:
    """A draft as it will be stored: only the fields the team's form knows, each of the right type.

    A value of the wrong type is a 422, the way FastAPI reports a bad body; a
    field left empty is fine until the team finishes.
    """
    model = DELIVERABLE_MODELS[team]
    known = {key: value for key, value in deliverable.items() if key in model.model_fields}
    try:
        return model.model_validate(known).model_dump(mode="json")
    except ValidationError as error:
        raise RequestValidationError(
            [
                {**e, "loc": ("body", "deliverable", *e["loc"])}
                for e in error.errors(include_url=False, include_context=False, include_input=False)
            ]
        ) from error
