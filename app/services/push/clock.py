"""Time for push notifications: UTC for our own columns, Riyadh wall clock for events and quiet hours.

``now`` reads the events pipeline's clock (``app/services/event_pipeline_clock.py``),
so a test that freezes the pipeline's time freezes this too. Event
``start_datetime``/``end_datetime`` are naive Riyadh wall-clock times; the
``push_*`` and ``member_*`` DATETIME columns are naive UTC.
"""

from datetime import UTC, date, datetime, time, timedelta

from app.semesters import CLUB_TIMEZONE
from app.services import event_pipeline_clock

# 22:00-08:00 in Riyadh: nothing but reminders goes out then.
QUIET_FROM = time(22, 0)
QUIET_UNTIL = time(8, 0)


def now() -> datetime:
    """Naive UTC."""
    return event_pipeline_clock.now()


def to_riyadh(utc: datetime) -> datetime:
    """Naive UTC to naive Riyadh wall clock."""
    return utc.replace(tzinfo=UTC).astimezone(CLUB_TIMEZONE).replace(tzinfo=None)


def to_utc(riyadh: datetime) -> datetime:
    """Naive Riyadh wall clock to naive UTC."""
    return riyadh.replace(tzinfo=CLUB_TIMEZONE).astimezone(UTC).replace(tzinfo=None)


def riyadh_day_bounds(utc: datetime) -> tuple[datetime, datetime]:
    """The Riyadh calendar day ``utc`` falls in, as a [start, end) pair of naive UTC times."""
    day = to_riyadh(utc).date()
    start = to_utc(datetime.combine(day, time(0, 0)))
    return start, start + timedelta(days=1)


def after_quiet_hours(utc: datetime) -> datetime:
    """``utc``, or the next 08:00 in Riyadh when ``utc`` falls in quiet hours."""
    local = to_riyadh(utc)
    if local.time() >= QUIET_FROM:
        return to_utc(datetime.combine(local.date() + timedelta(days=1), QUIET_UNTIL))
    if local.time() < QUIET_UNTIL:
        return to_utc(datetime.combine(local.date(), QUIET_UNTIL))
    return utc


def epoch(utc: datetime) -> int:
    return int(utc.replace(tzinfo=UTC).timestamp())


def from_epoch_ms(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).replace(tzinfo=None)


def day_key(day: date) -> str:
    return day.isoformat()
