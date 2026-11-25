"""Moving an event from one status to another, with everything that comes with it.

Two callers: an organizer (``PUT /events/{id}/status``) and the sweep, which
moves an open event to active once it starts (``pipeline_sweep.start_events``).
Both get the same side effects; the caller commits, kicks push delivery when
``pushed`` is non-zero, and resets the leaderboard cache (``touches_open``).
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.DB import forms as form_queries
from app.DB.schema import EventHistory, EventHistoryAction, Events, EventsStatus, FormType, Members
from app.services import event_history
from app.services.google_client import set_form_publish_state
from app.services.push import cases as push_cases

logger = logging.getLogger(__name__)


@dataclass
class StatusChange:
    recorded: EventHistory | None
    pushed: int
    # Entered or left "open": the leaderboard's open-events view is gated on it.
    touches_open: bool


def change_status(session: Session, event: Events, new: EventsStatus, actor: Members | None) -> StatusChange:
    """Set ``event.status`` to ``new``. ``actor`` None means the system did it."""
    old = event.status
    push_before = push_cases.event_state(event)

    # Publishing/unpublishing the event also publishes/unpublishes its Google
    # Form, if it has one - a copied form does not inherit the template's
    # accepting-responses state, so without this members hit an "unpublished
    # form" wall the admin has no way to see coming. Best-effort: the event's
    # own status is the source of truth, so a Google API failure here must not
    # block it.
    entering_open = new == EventsStatus.OPEN and old != EventsStatus.OPEN
    leaving_open = old == EventsStatus.OPEN and new != EventsStatus.OPEN
    if entering_open or leaving_open:
        form = form_queries.get_form_by_event_id(session, event.id)
        if form.form_type == FormType.GOOGLE and form.google_form_id:
            try:
                set_form_publish_state(form.google_form_id, is_published=entering_open)
            except Exception:
                logger.exception(
                    "Failed to %s Google Form %s for event %s",
                    "publish" if entering_open else "unpublish",
                    form.google_form_id,
                    event.id,
                )

    event.status = new
    recorded = None
    if new != old:
        recorded = event_history.record(
            session, event, EventHistoryAction.STATUS_CHANGED, actor, {"status": [old.value, new.value]}
        )
    pushed = push_cases.event_changed(session, event, push_before, recorded.id if recorded else None)
    return StatusChange(
        recorded=recorded, pushed=pushed, touches_open=new == EventsStatus.OPEN or old == EventsStatus.OPEN
    )
