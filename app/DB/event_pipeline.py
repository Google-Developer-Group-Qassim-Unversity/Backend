"""Reads and writes for the events pipeline: bans, and later the requests themselves."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.DB.schema import BookingBans


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
