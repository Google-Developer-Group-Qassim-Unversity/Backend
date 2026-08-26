"""Which address to send from, and how much of today's quota is left."""

import logging

from app.config import config
from app.DB import emails as email_queries
from app.DB.main import db_session
from app.DB.schema import EmailLogsFromAddress, Events
from app.helpers import get_effective_date

logger = logging.getLogger(__name__)


def _personalize(text: str, name: str, event_name: str) -> str:
    return text.replace("[Name]", name).replace("[Event Name]", event_name)


def format_event_date(event: Events) -> str:
    start_effective = get_effective_date(event.start_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    end_effective = get_effective_date(event.end_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    days = (end_effective - start_effective).days
    if days == 0:
        return start_effective.strftime("%Y-%m-%d")
    return f"{start_effective.strftime('%Y-%m-%d')} - {end_effective.strftime('%Y-%m-%d')}"


def get_from_address() -> EmailLogsFromAddress:
    """returns the address to be used based on last 24h usage of the club address."""
    with db_session() as session:
        club_usage = email_queries.get_email_address_usage(session, 1, EmailLogsFromAddress.GDG_QASSIM.value)
        if club_usage < config.CLUB_EMAIL_THRESHOLD:
            return EmailLogsFromAddress.GDG_QASSIM
        return EmailLogsFromAddress.INFO_KERNELTICS


def get_send_capacity(from_address: EmailLogsFromAddress) -> int:
    """returns how many more emails can be sent today via the given address, measured against its real daily
    threshold (the same numbers shown on the usage dashboard) -- not `CLUB_EMAIL_THRESHOLD`, which is a
    conservative early-switch buffer used only by `get_from_address` for many small reactive calls."""
    with db_session() as session:
        usage = email_queries.get_email_address_usage(session, 1, from_address.value)
    threshold = config.EMAIL_THRESHOLDS.get(from_address.value, config.CLUB_EMAIL_THRESHOLD)
    return max(0, threshold - usage)


def get_total_remaining_send_capacity() -> int:
    """returns the combined remaining daily send capacity across both addresses. Unlike `get_from_address`
    (which picks a single address per call, for many small independent sends), a blast can split across both
    addresses within one send, so its ceiling is the sum of what's left on each."""
    return sum(get_send_capacity(addr) for addr in EmailLogsFromAddress)
