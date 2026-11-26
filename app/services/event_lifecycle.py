"""Pure event lifecycle and registration derivation helpers.

Events carry three orthogonal concepts:

1. ``events.status`` (``draft | published | cancelled``): human publication /
   cancellation intent only.
2. ``phase`` (``upcoming | live | ended``): derived purely from Riyadh wall-clock
   ``now`` vs ``start_datetime`` / ``end_datetime`` - never stored.
3. Registration lifecycle on ``forms`` (``registration_status``): independent of
   ``events.status``; effective state is computed here.

All time reads go through ``app.services.push.clock`` (backed by
``event_pipeline_clock.now()``) so frozen clocks in tests work seamlessly.
"""

from datetime import datetime
from typing import Literal

from app.DB.schema import Events, EventsMeetingUrlVisibility, EventsStatus, FormRegistrationStatus, Forms, FormType
from app.services.push import clock

EventPhase = Literal["upcoming", "live", "ended"]
EffectiveRegistrationStatus = Literal["none", "not_open", "open", "closed"]
MeetingUrlVisibility = Literal["public", "accepted", "hidden"]


def utc_now() -> datetime:
    """Naive UTC timestamp from the shared test-freezable clock."""
    return clock.now()


def riyadh_now() -> datetime:
    """Naive Riyadh wall-clock timestamp matching ``events.start_datetime`` / ``end_datetime``."""
    return clock.to_riyadh(clock.now())


def compute_phase(start_datetime: datetime, end_datetime: datetime, local_now: datetime | None = None) -> EventPhase:
    """Derive the schedule phase from Riyadh wall-clock ``local_now``."""
    now = riyadh_now() if local_now is None else local_now
    if now < start_datetime:
        return "upcoming"
    if now <= end_datetime:
        return "live"
    return "ended"


def _as_event_status(status: EventsStatus | str) -> EventsStatus:
    return status if isinstance(status, EventsStatus) else EventsStatus(status)


def _as_form_type(form_type: FormType | str) -> FormType:
    return form_type if isinstance(form_type, FormType) else FormType(form_type)


def _as_reg_status(reg_status: FormRegistrationStatus | str) -> FormRegistrationStatus:
    return reg_status if isinstance(reg_status, FormRegistrationStatus) else FormRegistrationStatus(reg_status)


def effective_registration_status_from_values(
    *,
    event_status: EventsStatus | str,
    end_datetime: datetime,
    form_type: FormType | str | None,
    registration_status: FormRegistrationStatus | str | None,
    local_now: datetime | None = None,
) -> EffectiveRegistrationStatus:
    """Compute effective registration status from raw field values (ORM or view mapping)."""
    if form_type is None or _as_form_type(form_type) == FormType.NONE:
        return "none"
    now = riyadh_now() if local_now is None else local_now
    ev_status = _as_event_status(event_status)
    reg_status = FormRegistrationStatus.NOT_OPEN if registration_status is None else _as_reg_status(registration_status)
    if ev_status == EventsStatus.CANCELLED:
        return "closed"
    if ev_status == EventsStatus.DRAFT:
        return "not_open"
    if now > end_datetime or reg_status == FormRegistrationStatus.CLOSED:
        return "closed"
    if reg_status == FormRegistrationStatus.NOT_OPEN:
        return "not_open"
    return "open"


def effective_registration_status(
    event: Events, form: Forms | None, local_now: datetime | None = None
) -> EffectiveRegistrationStatus:
    """Compute effective registration status for an event and its form."""
    if form is None or form.form_type == FormType.NONE:
        return "none"
    return effective_registration_status_from_values(
        event_status=event.status,
        end_datetime=event.end_datetime,
        form_type=form.form_type,
        registration_status=form.registration_status,
        local_now=local_now,
    )


def is_registration_open(event: Events, form: Forms | None, local_now: datetime | None = None) -> bool:
    """True iff members may currently register or cancel registration on this form."""
    now = riyadh_now() if local_now is None else local_now
    return (
        event.status == EventsStatus.PUBLISHED
        and form is not None
        and form.form_type != FormType.NONE
        and form.registration_status == FormRegistrationStatus.OPEN
        and now <= event.end_datetime
    )


def should_google_form_be_published(event: Events, form: Forms | None) -> bool:
    """True iff the attached Google Form should be set to accepting responses."""
    return (
        event.status == EventsStatus.PUBLISHED
        and form is not None
        and form.form_type == FormType.GOOGLE
        and bool(form.google_form_id)
        and form.registration_status == FormRegistrationStatus.OPEN
    )


def attach_event_phase(event: Events, local_now: datetime | None = None) -> Events:
    """Attach derived ``phase`` to an ``Events`` ORM instance before serialization."""
    event.phase = compute_phase(event.start_datetime, event.end_datetime, local_now)
    return event


def attach_form_lifecycle(form: Forms, event: Events | None = None, local_now: datetime | None = None) -> Forms:
    """Attach derived ``effective_registration_status`` and ``is_registration_open`` to a ``Forms`` instance."""
    owner = event if event is not None else form.event
    eff = effective_registration_status(owner, form, local_now)
    form.effective_registration_status = eff
    form.is_registration_open = eff == "open"
    return form


def attach_event_form_lifecycle(event: Events, form: Forms | None, local_now: datetime | None = None) -> Events:
    """Attach ``phase`` and lean registration metadata onto an ``Events`` instance before serialization."""
    now = riyadh_now() if local_now is None else local_now
    event.phase = compute_phase(event.start_datetime, event.end_datetime, now)
    if form is None:
        event.form_id = None
        event.form_type = None
        event.registration_status = None
        event.effective_registration_status = "none"
        event.is_registration_open = False
        event.google_responders_url = None
    else:
        eff = effective_registration_status(event, form, now)
        event.form_id = form.id
        event.form_type = form.form_type
        event.registration_status = form.registration_status
        event.effective_registration_status = eff
        event.is_registration_open = eff == "open"
        event.google_responders_url = form.google_responders_url
    return event


def _as_meeting_visibility(visibility: EventsMeetingUrlVisibility | str | None) -> EventsMeetingUrlVisibility:
    if visibility is None:
        return EventsMeetingUrlVisibility.PUBLIC
    return visibility if isinstance(visibility, EventsMeetingUrlVisibility) else EventsMeetingUrlVisibility(visibility)


def visible_meeting_url(
    meeting_url: str | None,
    visibility: EventsMeetingUrlVisibility | str | None,
    *,
    is_staff: bool = False,
    is_accepted: bool = False,
) -> str | None:
    """Return ``meeting_url`` iff the caller is allowed to see it under ``visibility``."""
    if not meeting_url:
        return None
    if is_staff:
        return meeting_url
    vis = _as_meeting_visibility(visibility)
    if vis == EventsMeetingUrlVisibility.PUBLIC:
        return meeting_url
    if vis == EventsMeetingUrlVisibility.ACCEPTED:
        return meeting_url if is_accepted else None
    return None


def accepted_submission_meeting_url(
    event: Events, *, is_accepted: bool, local_now: datetime | None = None
) -> str | None:
    """Meeting URL included on a member's submission payload when they are accepted and the event is active/upcoming."""
    if not is_accepted or not event.meeting_url:
        return None
    if _as_event_status(event.status) != EventsStatus.PUBLISHED:
        return None
    if compute_phase(event.start_datetime, event.end_datetime, local_now) == "ended":
        return None
    return visible_meeting_url(event.meeting_url, event.meeting_url_visibility, is_accepted=True)
