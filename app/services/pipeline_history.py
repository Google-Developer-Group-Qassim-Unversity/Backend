"""Who did what in the events pipeline, and when.

Every step writes a ``pipeline_history`` row in the same transaction as the
change itself, so the record can never disagree with what happened: a step
that fails leaves no row, and one that commits always has its row. When
something goes wrong, this is where to look for who did it.

- ``actor`` is ``None`` only for what the sweep does on its own (an expired
  hold, a growing late penalty).
- Forms save as people type, so consecutive edits of the same form by the same
  person within ``EDIT_WINDOW`` are one row, its time the last edit's.
- The request's title and department are copied onto each row, so the history
  still reads after the request is gone.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB.schema import EventRequests, Members, PipelineHistory, PipelineHistoryAction
from app.services import event_pipeline_clock as clock

EDIT_WINDOW = timedelta(minutes=30)
EDITS = {
    PipelineHistoryAction.DETAILS_EDITED,
    PipelineHistoryAction.BRIEF_EDITED,
    PipelineHistoryAction.CONFIRMATION_EDITED,
}


def record(
    session: Session,
    action: PipelineHistoryAction,
    actor: Members | None,
    request: EventRequests | None = None,
    details: dict | None = None,
    at: datetime | None = None,
) -> PipelineHistory:
    """Write what ``actor`` did. ``at`` is for the sweep, which works to its own clock reading."""
    now = at or clock.now()
    actor_id = actor.id if actor else None
    if action in EDITS and request is not None:
        last = session.scalar(
            select(PipelineHistory)
            .where(PipelineHistory.request_id == request.id)
            .order_by(PipelineHistory.at.desc(), PipelineHistory.id.desc())
            .limit(1)
        )
        if (
            last is not None
            and last.action == action
            and last.actor_id == actor_id
            and last.details == details
            and now - last.at <= EDIT_WINDOW
        ):
            last.at = now
            last.request_title = request.title
            session.flush()
            return last
    row = PipelineHistory(
        request_id=request.id if request else None,
        actor_id=actor_id,
        action=action,
        at=now,
        details=details or None,
        request_title=request.title if request else None,
        department_id=request.department_id if request else None,
    )
    session.add(row)
    session.flush()
    return row


def for_request(session: Session, request_id: str) -> list[PipelineHistory]:
    """A request's history, oldest first."""
    return list(
        session.scalars(
            select(PipelineHistory)
            .where(PipelineHistory.request_id == request_id)
            .order_by(PipelineHistory.at, PipelineHistory.id)
        ).all()
    )
