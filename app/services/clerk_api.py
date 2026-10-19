"""Clerk's Backend API, for what a session token cannot do on its own."""

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

# Verification strategies that prove the user receives mail at the address.
PROVEN_STRATEGIES = frozenset({"email_code", "reset_password_email_code", "email_link"})
PROVEN_STRATEGY_PREFIX = "from_oauth_"

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

    @property
    def proven_uni_ids(self) -> frozenset[str]:
        """Every university ID whose <uni_id>@qu.edu.sa mailbox the user proved, primary or not."""
        return frozenset(match.group(1) for email in self.proven_emails if (match := UNIVERSITY_EMAIL.fullmatch(email)))


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
    # Keep the key in a variable named ``secret`` (Sentry scrubs it) and don't chain httpx's exception.
    secret = _secret_key()
    try:
        with httpx.Client(
            base_url=CLERK_API_URL, headers={"Authorization": f"Bearer {secret}"}, timeout=TIMEOUT_SECONDS
        ) as client:
            return client.request(method, path, json=json)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    logger.error("Clerk %s %s failed: %s", method, path, failure)
    raise ClerkUnavailable()


def _raise_for_status(response: httpx.Response, action: str, user_id: str) -> None:
    if response.is_success:
        return
    # The body can carry the user's details, so only the status is logged.
    logger.error("Clerk could not %s for %s: HTTP %s", action, user_id, response.status_code)
    if response.status_code in (401, 403):
        # Clerk refused the secret key itself: a retry cannot help until the setting is fixed.
        raise ClerkNotConfigured()
    raise ClerkUnavailable()


def get_user(user_id: str) -> ClerkUser:
    """The signed-in user as Clerk has them. A user Clerk no longer has is signed out: 401."""
    response = _request("GET", _user_path(user_id))
    if response.status_code == 404:
        raise NotSignedIn("This sign-in no longer exists")
    _raise_for_status(response, "read the user", user_id)
    try:
        return parse_user(response.json())
    except (ValueError, KeyError, TypeError, AttributeError):
        logger.error("Clerk answered with a user object that could not be read, for %s", user_id)
    raise ClerkUnavailable()


def update_public_metadata(user_id: str, metadata: dict[str, Any]) -> bool:
    """Merge ``metadata`` into the user's publicMetadata. A key set to ``None`` is removed."""
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
