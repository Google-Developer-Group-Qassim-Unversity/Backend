"""Clerk's Backend API, for what a session token cannot do on its own.

The session token says who the caller is. Reading which email addresses they
proved, writing the publicMetadata the Website reads, and deleting their
sign-in need the instance's secret key (``CLERK_SECRET_KEY``), so the member
app's account routes do those through here.
https://clerk.com/docs/reference/backend-api

A fresh ``httpx.Client`` per call, like ``app/leaderboard_cache.py``: these run
from sync handlers, and it is what ``tests/outbound.py`` intercepts.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import MissingSettingError, config
from app.exceptions import ClerkNotConfigured, ClerkUnavailable, NotSignedIn

logger = logging.getLogger(__name__)

CLERK_API_URL = "https://api.clerk.com/v1"
TIMEOUT_SECONDS = 10.0

# An address is the user's own when they proved they receive mail there: Clerk
# sent it a code or a link, or an OAuth provider (Google, Apple) vouched for it.
# The verification strategies are in the Backend API's EmailAddress schema.
PROVEN_STRATEGIES = frozenset({"email_code", "reset_password_email_code", "email_link"})
PROVEN_STRATEGY_PREFIX = "from_oauth_"

# A university-ID account signs up as <9-digit uni_id>@qu.edu.sa and proves it
# with a code sent to that mailbox.
UNIVERSITY_EMAIL = re.compile(r"([0-9]{9})@qu\.edu\.sa", re.IGNORECASE)


@dataclass(frozen=True)
class ClerkUser:
    """The parts of a Clerk user the account routes rely on. Emails are lowercased."""

    id: str
    # The primary address, when it is verified.
    primary_email: str | None
    # Every address the user proved, the primary first when it is one of them.
    proven_emails: tuple[str, ...]
    # From the primary email, when it is a proven <uni_id>@qu.edu.sa.
    uni_id: str | None

    @property
    def is_university_id_account(self) -> bool:
        """Signs in as <uni_id>@qu.edu.sa, the way the Website's university-ID sign-up works."""
        return bool(self.primary_email and UNIVERSITY_EMAIL.fullmatch(self.primary_email))

    def owns_email(self, email: str) -> bool:
        return email.strip().lower() in self.proven_emails


def parse_user(data: dict[str, Any]) -> ClerkUser:
    """A ``ClerkUser`` from the Backend API's user object."""
    primary_id = data.get("primary_email_address_id")
    primary: str | None = None
    proven: list[str] = []
    for address in data.get("email_addresses") or []:
        email = str(address.get("email_address") or "").strip().lower()
        verification = address.get("verification") or {}
        if not email or verification.get("status") != "verified":
            continue
        is_primary = address.get("id") == primary_id
        if is_primary:
            primary = email
        strategy = str(verification.get("strategy") or "")
        if (strategy in PROVEN_STRATEGIES or strategy.startswith(PROVEN_STRATEGY_PREFIX)) and email not in proven:
            if is_primary:
                proven.insert(0, email)
            else:
                proven.append(email)

    match = UNIVERSITY_EMAIL.fullmatch(primary) if primary and primary in proven else None
    return ClerkUser(
        id=str(data["id"]), primary_email=primary, proven_emails=tuple(proven), uni_id=match.group(1) if match else None
    )


def _secret_key() -> str:
    try:
        return config.CLERK_SECRET_KEY
    except MissingSettingError as exc:
        logger.error("CLERK_SECRET_KEY is not set; the member app's account routes are off")
        raise ClerkNotConfigured() from exc


def _user_path(user_id: str) -> str:
    return f"/users/{quote(user_id, safe='')}"


def _request(method: str, path: str, *, json: Any = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_secret_key()}"}
    try:
        with httpx.Client(base_url=CLERK_API_URL, headers=headers, timeout=TIMEOUT_SECONDS) as client:
            return client.request(method, path, json=json)
    except httpx.HTTPError as exc:
        logger.error("Clerk %s %s failed: %s", method, path, type(exc).__name__)
        raise ClerkUnavailable() from exc


def _raise_for_status(response: httpx.Response, action: str, user_id: str) -> None:
    if response.is_success:
        return
    # The body can carry the user's details, so only the status is logged.
    logger.error("Clerk could not %s for %s: HTTP %s", action, user_id, response.status_code)
    raise ClerkUnavailable()


def get_user(user_id: str) -> ClerkUser:
    """The signed-in user as Clerk has them. A user Clerk no longer has is signed out: 401."""
    response = _request("GET", _user_path(user_id))
    if response.status_code == 404:
        raise NotSignedIn("This sign-in no longer exists")
    _raise_for_status(response, "read the user", user_id)
    return parse_user(response.json())


def update_public_metadata(user_id: str, metadata: dict[str, Any]) -> bool:
    """Merge ``metadata`` into the user's publicMetadata. A key set to ``None`` is removed.

    ``False`` when Clerk no longer has the user: the sign-in was deleted since it was read.
    """
    response = _request("PATCH", f"{_user_path(user_id)}/metadata", json={"public_metadata": metadata})
    if response.status_code == 404:
        return False
    _raise_for_status(response, "update publicMetadata", user_id)
    return True


def delete_user(user_id: str) -> bool:
    """Delete the sign-in. ``False`` when Clerk no longer has it, which is the state a retry is after."""
    response = _request("DELETE", _user_path(user_id))
    if response.status_code == 404:
        return False
    _raise_for_status(response, "delete the user", user_id)
    return True
