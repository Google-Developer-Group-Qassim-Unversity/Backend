"""The events pipeline's one idea of "now".

Every time rule in the pipeline - the booking lockout, the 24-hour hold, the
return deadlines - reads the time through here, so a test can freeze it by
patching ``now`` in this module.

The database session runs in UTC (``app/DB/main.py``), so ``now`` is a naive
UTC datetime, comparable with the columns it is stored in. Booking dates are
calendar days where the club is, so ``today`` is the date in Riyadh.
"""

from datetime import UTC, date, datetime

from app.semesters import CLUB_TIMEZONE


def now() -> datetime:
    """The current time as a naive UTC datetime, like the DATETIME columns store it."""
    return datetime.now(UTC).replace(tzinfo=None)


def today() -> date:
    """Today's date in Riyadh, derived from ``now`` so freezing one freezes both."""
    return now().replace(tzinfo=UTC).astimezone(CLUB_TIMEZONE).date()
