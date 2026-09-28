"""Reads and writes for the events pipeline: bans, and later the requests themselves."""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import BookingBans, EventRequestPartners, EventRequests, EventRequestStage


def get_bans(session: Session, start: date, end: date, lock: bool = False) -> Sequence[BookingBans]:
    statement = select(BookingBans).where(BookingBans.date >= start, BookingBans.date <= end)
    if lock:
        statement = statement.with_for_update()
    return session.scalars(statement.order_by(BookingBans.date)).all()


def ban_days(session: Session, days: list[date], reason: str | None, banned_by: int) -> None:
    """Ban ``days``; a day already banned just takes the new reason."""
    existing = {ban.date: ban for ban in get_bans(session, min(days), max(days), lock=True)}
    for day in days:
        if day in existing:
            existing[day].reason = reason
        else:
            session.add(BookingBans(date=day, reason=reason, banned_by=banned_by))
    session.flush()


def unban_days(session: Session, days: list[date]) -> int:
    result = session.execute(delete(BookingBans).where(BookingBans.date.in_(days)))
    session.flush()
    return result.rowcount  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- requests

ACTIVE_STAGES_EXCLUDED = (EventRequestStage.CANCELLED,)


def get_request(session: Session, request_id: int, lock: bool = False) -> EventRequests | None:
    statement = (
        select(EventRequests)
        .where(EventRequests.id == request_id)
        .options(
            selectinload(EventRequests.department),
            selectinload(EventRequests.creator),
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


def list_requests(
    session: Session, department_ids: set[int] | None, stage: EventRequestStage | None, limit: int, offset: int
) -> tuple[int, Sequence[EventRequests]]:
    """Newest first. ``department_ids=None`` means every department."""
    statement = select(EventRequests)
    if department_ids is not None:
        statement = statement.where(EventRequests.department_id.in_(department_ids))
    if stage is not None:
        statement = statement.where(EventRequests.stage == stage)
    else:
        statement = statement.where(EventRequests.stage != EventRequestStage.CANCELLED)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.scalars(
        statement.options(selectinload(EventRequests.department))
        .order_by(EventRequests.created_at.desc(), EventRequests.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return total, rows


def set_partners(session: Session, request: EventRequests, department_ids: list[int]) -> None:
    request.partners = [EventRequestPartners(department_id=d) for d in dict.fromkeys(department_ids)]
    session.flush()


def acquire_booking_lock(session: Session) -> None:
    """Serialize every change to who holds which day, across the four workers.

    A named MySQL lock rather than row locks: two teams booking the same free
    day have no row in common to lock. It belongs to the connection, so the
    caller releases it after committing.
    """
    got = session.scalar(text("SELECT GET_LOCK('pipeline_booking', 10)"))
    if got != 1:
        raise TimeoutError("Timed out waiting for the booking lock")


def release_booking_lock(session: Session) -> None:
    session.execute(text("SELECT RELEASE_LOCK('pipeline_booking')"))
