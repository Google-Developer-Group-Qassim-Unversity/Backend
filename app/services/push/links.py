"""The app links a push may open. The app keeps the same list (``src/notifications/links.ts``)."""

import re

DEFAULT_URL = "gdgq://notifications"

_ALLOWED = (
    re.compile(r"gdgq://events/[1-9][0-9]{0,9}"),
    re.compile(r"gdgq://points/history"),
    re.compile(r"gdgq://notifications"),
    re.compile(r"gdgq://profile/my-events"),
    re.compile(r"gdgq://wallet/card"),
)


def is_allowed(url: str) -> bool:
    return any(pattern.fullmatch(url) for pattern in _ALLOWED)


def event_url(event_id: int) -> str:
    return f"gdgq://events/{event_id}"


POINTS_URL = "gdgq://points/history"
