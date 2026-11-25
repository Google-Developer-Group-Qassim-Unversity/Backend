"""
Helper for resetting the leaderboard (member) app's Next.js data cache.

The leaderboard app exposes ``POST /api/revalidate`` which is secured with a
shared bearer secret. This module centralizes the call so it can be reused by
the cache router and by mutations to events, attendance, or member names.
"""

import logging

import httpx

from app.config import MissingSettingError, config

logger = logging.getLogger(__name__)


def reset_leaderboard_cache(*, tag: str | None = None, timeout: float = 30.0) -> dict:
    """Reset the leaderboard data cache, optionally limited to a tag.

    Returns:
        The JSON response body from the leaderboard app on success.

    Raises:
        httpx.HTTPStatusError: if the leaderboard app returns a non-2xx status.
        httpx.RequestError: on network/connection failures.
    """
    url = f"{config.MEMBER_APP_URL}/api/revalidate"
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            url,
            params={"tag": tag} if tag else None,
            headers={
                "Authorization": f"Bearer {config.MEMBER_APP_REVALIDATE_SECRET}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()


def refresh_member_names_cache() -> None:
    """A name is already committed; cache outages must not report a failed save."""
    try:
        reset_leaderboard_cache()
    except (httpx.HTTPError, ValueError, MissingSettingError):
        logging.getLogger(__name__).warning("Could not refresh public member names cache", exc_info=True)


def reset_leaderboard_cache_best_effort() -> None:
    """A committed change (event created/edited, content link changed, ...)
    must reach the leaderboard app only after its data cache is revalidated -
    but a cache outage must never fail the admin's own write. Every mutation
    site calls this: it logs the full failure with the context that matters."""

    try:
        reset_leaderboard_cache()
    except Exception:
        logger.error("Leaderboard cache reset failed", exc_info=True)


def refresh_attendance_points_cache() -> None:
    """Expire public points before replying, keeping committed attendance if the reset fails."""
    try:
        reset_leaderboard_cache(tag="points", timeout=2.0)
    except Exception:
        logger.exception("Attendance points cache reset failed")
