"""The app links a push may open. The app keeps the same list (``src/notifications/links.ts``)."""

import re

DEFAULT_URL = "gdgq://notifications"

_ALLOWED = (
    re.compile(r"gdgq://events/[1-9][0-9]{0,9}"),
    re.compile(r"gdgq://points/history"),
    re.compile(r"gdgq://notifications"),
    re.compile(r"gdgq://profile/my-events"),
    re.compile(r"gdgq://wallet/card"),
    # App 1.0.3: the member's certificates. 1.0.2 does not know it and opens Notifications instead.
    re.compile(r"gdgq://profile/certificates"),
    # App 1.0.3 build 6: the members leaderboard at the member's own row. Older builds open Notifications instead.
    re.compile(r"gdgq://leaderboard"),
)


def is_allowed(url: str) -> bool:
    return any(pattern.fullmatch(url) for pattern in _ALLOWED)


def event_url(event_id: int) -> str:
    return f"gdgq://events/{event_id}"


POINTS_URL = "gdgq://points/history"
CERTIFICATES_URL = "gdgq://profile/certificates"
LEADERBOARD_URL = "gdgq://leaderboard"
