"""
Helper for resetting the leaderboard (member) app's Next.js data cache.

The leaderboard app exposes ``POST /api/revalidate`` which is secured with a
shared bearer secret. This module centralizes the call so it can be reused by
the cache router (user-triggered) and by event mutations (auto-trigger).
"""

import logging

import httpx

from app.config import MissingSettingError, config


def reset_leaderboard_cache() -> dict:
    """Trigger a full data-cache reset on the leaderboard app.

    Returns:
        The JSON response body from the leaderboard app on success.

    Raises:
        httpx.HTTPStatusError: if the leaderboard app returns a non-2xx status.
        httpx.RequestError: on network/connection failures.
    """
    url = f"{config.MEMBER_APP_URL}/api/revalidate"
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            url,
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
