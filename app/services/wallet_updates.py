"""Apple Wallet pass updates: the rules behind Apple's PassKit web service.

A member's Apple Wallet pass can talk back to this server. While
``APPLE_PASS_WEB_SERVICE_URL`` and ``APPLE_PASS_AUTH_SECRET`` are set, every
member pass carries:

- ``webServiceURL``: that URL, which is this backend's ``/wallet/apple``.
  Wallet registers the pass there (``/v1/devices/...``), asks which passes
  changed and fetches the latest one (``/v1/passes/...``) after an update push.
- ``authenticationToken``: ``base64url(HMAC-SHA256(APPLE_PASS_AUTH_SECRET,
  "pass:v1:" + uuid))``, 43 characters. Wallet sends it back as
  ``Authorization: ApplePass <token>``. Nothing is stored: the server makes the
  token again from the card's uuid and compares in constant time, so a copy
  of the database (staging is one) holds no working token, and each member's
  token differs even when two serial numbers collide. Never logged.
- the back field «رسالة النادي» with ``changeMessage`` ``%@``: a placeholder
  until the member is welcomed, then the welcome line.

The welcome: the first time one of a member's cards registers - ever, across
devices and re-adds - ``member_profiles.pass_welcomed_at`` and the pass's
update tag (``pass_updated_at``) are set and a pass update push goes to that
device right away, quiet hours or not (``app/services/wallet_push.py``). Wallet
fetches the pass, sees the field change, and shows the line as a notification
from the card. Every pass issued after that already carries the line, so a
second device, a re-added card or a new iPhone has nothing to announce.

Serial numbers stay ``GDGQ-`` + the uuid's first 8 characters
(``app/wallet_signer.py``): a new format would give every holder a second card.
A serial alone never names a member - the token decides which of the (rare)
colliding cards it is. Guest passes carry none of this. See docs/WALLET.md.
"""

import base64
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import config
from app.DB import wallet_passes as pass_queries
from app.DB.schema import MemberProfiles
from app.services.push import clock, redact
from app.services.push.messages import isolate
from app.wallet_signer import pass_serial_number

logger = logging.getLogger(__name__)

# Bumped if the scheme ever changes, so an old token never verifies under a new one.
TOKEN_CONTEXT = "pass:v1:"
WELCOME = f"أهلًا بك في {isolate('GDG Qassim')}! متحمسين نشوفك قريب في فعالياتنا"
# On the back of the card until the welcome replaces it: neutral, never announced.
PLACEHOLDER = "رسائل النادي تظهر هنا"

_SERIAL = re.compile(r"GDGQ-[0-9A-Za-z]{8}")
LOG_LINES = 10
LOG_LINE_LENGTH = 500


@dataclass(frozen=True)
class Registered:
    created: bool
    # This registration welcomed the member: a pass update push is owed to the device.
    welcomed: bool


def authentication_token(uuid: str) -> str | None:
    """The pass's authenticationToken, or None while this server has no ``APPLE_PASS_AUTH_SECRET``."""
    secret = config.APPLE_PASS_AUTH_SECRET
    if not secret:
        return None
    digest = hmac.new(secret.encode(), f"{TOKEN_CONTEXT}{uuid}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def card_fields(profile: MemberProfiles) -> dict[str, Any]:
    """What a member pass adds for the web service; nothing while it is off."""
    if not config.apple_pass_updates_enabled:
        return {}
    return {
        "webServiceURL": config.APPLE_PASS_WEB_SERVICE_URL,
        "authenticationToken": authentication_token(profile.uuid),
        "clubMessage": WELCOME if profile.pass_welcomed_at is not None else PLACEHOLDER,
    }


def token_from_header(authorization: str | None) -> str | None:
    """``ApplePass <token>`` -> the token."""
    scheme, _, token = (authorization or "").strip().partition(" ")
    if scheme.lower() != "applepass" or not token.strip():
        return None
    return token.strip()


def find_holder(session: Session, pass_type_id: str, serial_number: str, token: str | None) -> MemberProfiles | None:
    """The member profile whose pass this is, if ``token`` is that pass's own. None otherwise.

    The serial narrows it to the profiles whose uuid starts with its 8
    characters; the token, made again for each and compared in constant time,
    picks the one.
    """
    if not config.apple_pass_updates_enabled or not token:
        return None
    if pass_type_id != config.APPLE_PASS_TYPE_ID or not _SERIAL.fullmatch(serial_number):
        return None
    for profile in pass_queries.profiles_for_serial_prefix(session, serial_number.removeprefix("GDGQ-")):
        expected = authentication_token(profile.uuid)
        if (
            expected is not None
            and pass_serial_number(profile.uuid) == serial_number
            and hmac.compare_digest(expected.encode(), token.encode())
        ):
            return profile
    return None


def register(
    session: Session, holder: MemberProfiles, *, device_library_id: str, serial_number: str, push_token: str
) -> Registered:
    """Store the device's registration; the member's first one ever welcomes them. The caller commits."""
    now = clock.now().replace(microsecond=0)
    registration, created = pass_queries.register(
        session,
        holder.member_id,
        device_library_id=device_library_id,
        serial_number=serial_number,
        push_token=push_token,
        now=now,
    )
    welcomed = holder.pass_welcomed_at is None and pass_queries.mark_welcomed(session, holder.id, now)
    if welcomed:
        pass_queries.owe_push(session, registration.id, now)
        logger.info("Member %s: first Wallet pass registration, welcome push owed", holder.member_id)
    return Registered(created=created, welcomed=welcomed)


def unregister(session: Session, holder: MemberProfiles, *, device_library_id: str, serial_number: str) -> bool:
    return pass_queries.unregister(
        session, holder.member_id, device_library_id=device_library_id, serial_number=serial_number
    )


def update_tag(updated_at: datetime | None) -> int:
    """The pass's update tag as Wallet sees it (``lastUpdated``): whole seconds since the epoch, 0 for never."""
    return clock.epoch(updated_at) if updated_at is not None else 0


def updated_serials(
    session: Session, device_library_id: str, pass_type_id: str, passes_updated_since: str | None
) -> tuple[list[str], str] | None:
    """The device's passes updated after ``passes_updated_since`` (all of them without one), and the new tag.

    None when there are none: Wallet is answered 204. A tag that is not ours
    (not a number) counts as no tag.
    """
    if pass_type_id != config.APPLE_PASS_TYPE_ID:
        return None
    passes = pass_queries.device_passes(session, device_library_id)
    if not passes:
        return None
    tag = (passes_updated_since or "").strip()
    since = int(tag) if tag.isdigit() else None
    changed = [serial for serial, updated_at in passes if since is None or update_tag(updated_at) > since]
    if not changed:
        return None
    return changed, str(max(update_tag(updated_at) for _, updated_at in passes))


def not_modified_since(holder: MemberProfiles, if_modified_since: str | None) -> bool:
    """Whether Wallet's copy (``If-Modified-Since``) is already the latest pass."""
    if holder.pass_updated_at is None or not if_modified_since:
        return False
    try:
        since = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False
    if since.tzinfo is None:
        return False
    return update_tag(holder.pass_updated_at) <= int(since.timestamp())


def log_lines(payload: Any) -> list[str]:
    """What Wallet reported to ``/v1/log``: at most 10 lines of 500 characters, device ids and tokens hidden."""
    logs = payload.get("logs") if isinstance(payload, dict) else None
    if not isinstance(logs, list):
        return []
    return [redact.scrub(str(line))[:LOG_LINE_LENGTH] for line in logs[:LOG_LINES]]
