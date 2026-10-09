"""Who did what to an event from /events, and when.

Each admin change writes an ``event_history`` row in the same transaction as
the change, so the record never disagrees with what happened. Edits record
each changed field as ``{"field": [before, after]}``. A pipeline event's
steps before it was published are in ``pipeline_history``.
"""

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB.schema import EventHistory, EventHistoryAction, Events, Members

# What an edit compares. The edit form also sends the status, so a status
# changed through it shows here; ``meeting_url`` has its own route and action.
EDITED_FIELDS = (
    "name",
    "status",
    "description",
    "location_type",
    "location",
    "start_datetime",
    "end_datetime",
    "image_url",
    "is_official",
    "semester_id",
)


def _plain(value):
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def snapshot(event: Events, fields=EDITED_FIELDS) -> dict:
    return {field: _plain(getattr(event, field)) for field in fields}


def changes(before: dict, after: dict) -> dict:
    """``{"field": [before, after]}`` for each field that changed."""
    return {key: [before[key], after.get(key)] for key in before if before[key] != after.get(key)}


def record(
    session: Session,
    event: Events | int,
    action: EventHistoryAction,
    actor: Members | None,
    details: dict | None = None,
) -> EventHistory:
    event_id = event if isinstance(event, int) else event.id
    name = None if isinstance(event, int) else event.name
    if name is None:
        name = session.scalar(select(Events.name).where(Events.id == event_id))
    row = EventHistory(
        event_id=event_id,
        actor_id=actor.id if actor else None,
        action=action,
        at=datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        details=details or None,
        event_name=name,
    )
    session.add(row)
    session.flush()
    return row


def for_event(session: Session, event_id: int) -> list[EventHistory]:
    """An event's history, oldest first."""
    return list(
        session.scalars(
            select(EventHistory).where(EventHistory.event_id == event_id).order_by(EventHistory.at, EventHistory.id)
        ).all()
    )
