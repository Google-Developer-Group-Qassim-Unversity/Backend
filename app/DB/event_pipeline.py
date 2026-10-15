"""Reads and writes for the events pipeline: closed days (blocks), and the requests themselves."""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import delete, func, select
from typing import Literal

from sqlalchemy.orm import Session, aliased, selectinload

from app.DB.schema import (
    BookingBans,
    BookingBlocks,
    EventRequestPartners,
    EventRequests,
    EventRequestStage,
    EventRequestTasks,
    EventRequestTaskStatus,
    PipelineLocks,
    PipelineTeam,
)


def get_bans(session: Session, start: date, end: date, lock: bool = False) -> Sequence[BookingBans]:
    """The closed days in [start, end], each with its block and who closed it."""
    statement = (
        select(BookingBans)
        .where(BookingBans.date >= start, BookingBans.date <= end)
        .options(selectinload(BookingBans.block).selectinload(BookingBlocks.blocker))
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalars(statement.order_by(BookingBans.date)).all()


def get_block(session: Session, block_id: str, lock: bool = False) -> BookingBlocks | None:
    statement = (
        select(BookingBlocks)
        .where(BookingBlocks.id == block_id)
        .options(selectinload(BookingBlocks.days), selectinload(BookingBlocks.blocker))
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def block_days(session: Session, days: list[date], reason: str, blocked_by: int) -> BookingBlocks:
    """Close ``days`` as one new block. A day already closed moves to it; a block left with no days goes."""
    existing = {ban.date: ban for ban in get_bans(session, min(days), max(days), lock=True)}
    block = BookingBlocks(reason=reason, blocked_by=blocked_by)
    session.add(block)
    session.flush()
    left_behind = set()
    for day in days:
        if day in existing:
            left_behind.add(existing[day].block_id)
            existing[day].block_id = block.id
        else:
            session.add(BookingBans(date=day, block_id=block.id))
    session.flush()
    delete_empty_blocks(session, left_behind)
    return block


def unban_days(session: Session, days: list[date]) -> int:
    blocks = set(session.scalars(select(BookingBans.block_id).where(BookingBans.date.in_(days))).all())
    result = session.execute(delete(BookingBans).where(BookingBans.date.in_(days)))
    session.flush()
    delete_empty_blocks(session, blocks)
    return result.rowcount  # type: ignore[attr-defined]


def delete_block(session: Session, block: BookingBlocks) -> None:
    """Reopen every day of ``block``; its day rows go with it."""
    session.delete(block)
    session.flush()


def delete_empty_blocks(session: Session, block_ids: set[str]) -> None:
    if not block_ids:
        return
    still_used = select(BookingBans.block_id).where(BookingBans.block_id.in_(block_ids))
    session.execute(delete(BookingBlocks).where(BookingBlocks.id.in_(block_ids), BookingBlocks.id.not_in(still_used)))
    session.flush()


# --------------------------------------------------------------------------- requests

ACTIVE_STAGES_EXCLUDED = (EventRequestStage.CANCELLED,)


def get_request(session: Session, request_id: str, lock: bool = False) -> EventRequests | None:
    statement = (
        select(EventRequests)
        .where(EventRequests.id == request_id)
        .options(
            selectinload(EventRequests.department),
            selectinload(EventRequests.requester),
            selectinload(EventRequests.partners).selectinload(EventRequestPartners.department),
        )
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.scalar(statement)


def get_dated_requests(session: Session, start: date, end: date, lock: bool = False) -> Sequence[EventRequests]:
    """Requests with dates overlapping [start, end], in any stage but cancelled.

    Whether each one still takes its days depends on the time (a draft's hold
    can run out), so the caller decides that.
    """
    statement = (
        select(EventRequests)
        .where(
            EventRequests.start_date.is_not(None),
            EventRequests.start_date <= end,
            EventRequests.end_date >= start,
            EventRequests.stage.not_in(ACTIVE_STAGES_EXCLUDED),
        )
        .options(selectinload(EventRequests.department))
        .order_by(EventRequests.start_date, EventRequests.id)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.scalars(statement).all()


def get_department_drafts_with_hold(session: Session, department_id: int, now: datetime) -> Sequence[EventRequests]:
    return session.scalars(
        select(EventRequests)
        .where(
            EventRequests.department_id == department_id,
            EventRequests.stage == EventRequestStage.DRAFT,
            EventRequests.hold_expires_at > now,
        )
        .with_for_update()
    ).all()


# The stages a request ends in. Everything else is still moving.
PAST_STAGES = (EventRequestStage.PUBLISHED, EventRequestStage.CANCELLED)


def list_requests(
    session: Session,
    department_ids: set[int] | None,
    stage: EventRequestStage | None,
    limit: int,
    offset: int,
    *,
    state: Literal["active", "past"] | None = None,
    team: PipelineTeam | None = None,
    team_statuses: Sequence[EventRequestTaskStatus] | None = None,
) -> tuple[int, Sequence[EventRequests]]:
    """``department_ids=None`` means every department.

    Cancelled requests show only when asked for, by ``stage`` or ``state="past"``.
    ``team`` keeps the requests whose ``team`` part is in ``team_statuses``.

    Newest first; past requests by when they ended, a team's finished parts by
    when the team finished them.
    """
    statement = select(EventRequests)
    if department_ids is not None:
        statement = statement.where(EventRequests.department_id.in_(department_ids))
    if stage is not None:
        statement = statement.where(EventRequests.stage == stage)
    elif state == "past":
        statement = statement.where(EventRequests.stage.in_(PAST_STAGES))
    elif state == "active":
        statement = statement.where(EventRequests.stage.not_in(PAST_STAGES))
    else:
        statement = statement.where(EventRequests.stage != EventRequestStage.CANCELLED)
    task = None
    if team is not None:
        task = aliased(EventRequestTasks)
        statement = statement.join(task, (task.request_id == EventRequests.id) & (task.team == team))
        if team_statuses:
            statement = statement.where(task.status.in_(team_statuses))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    if task is not None and team_statuses and set(team_statuses) == {EventRequestTaskStatus.DONE}:
        order = (task.done_at.desc(), EventRequests.id.desc())
    elif state == "past":
        order = (func.coalesce(EventRequests.published_at, EventRequests.updated_at).desc(), EventRequests.id.desc())
    else:
        order = (EventRequests.requested_at.desc(), EventRequests.id.desc())
    rows = session.scalars(
        statement.options(selectinload(EventRequests.department), selectinload(EventRequests.tasks))
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    ).all()
    return total, rows


def set_partners(session: Session, request: EventRequests, department_ids: list[int]) -> None:
    request.partners = [EventRequestPartners(department_id=d) for d in dict.fromkeys(department_ids)]
    session.flush()


def lock_pipeline(session: Session, name: str, wait: bool = True) -> bool:
    """Lock the ``pipeline_locks`` row ``name`` until this transaction ends.

    Serializes work across the four workers, such as every change to who holds
    which day: two teams booking the same free day have no other row in common
    to lock. The commit or rollback releases it, whatever connection the pool
    hands out next. With ``wait=False`` it returns False at once if another
    transaction holds it.
    """
    statement = select(PipelineLocks.name).where(PipelineLocks.name == name)
    statement = statement.with_for_update() if wait else statement.with_for_update(skip_locked=True)
    return session.scalar(statement) is not None


def delete_request(session: Session, request: EventRequests) -> None:
    """Delete a request for good; its partners, tasks, notifications and penalty go with it."""
    session.delete(request)
    session.flush()


def get_request_for_event(session: Session, event_id: int) -> EventRequests | None:
    """The pipeline request that published ``event_id``, if one did."""
    return session.scalar(select(EventRequests).where(EventRequests.event_id == event_id))
