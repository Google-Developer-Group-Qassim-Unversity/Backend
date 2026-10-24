"""The member card's QR: the member's public profile link, signed.

Every card the club issues - the member app's card, the Website's card and the
Apple and Google Wallet passes - shows ``https://gdg-q.com/p/{uuid}?s={sig}``.
Staff scanners send the ``s`` they read along with the uuid, so a QR someone
made from a member's public ``/p/{uuid}`` link (which is shared on purpose)
can be told apart from the member's own card. See docs/CARD_QR.md.

``sig`` is the first 16 characters of base64url(HMAC-SHA256(CARD_QR_SECRET,
"card:v1:" + uuid)): 96 bits. It is static - a screenshot of a member's own
card still works - because a Wallet pass changes only when the backend pushes
it an update (docs/WALLET.md), and none changes the QR.

With no ``CARD_QR_SECRET`` the value is the unsigned link every card carried
before, and a signature can be neither made nor checked.
"""

import base64
import hashlib
import hmac
from typing import Literal

from app.config import config

PROFILE_URL = "https://gdg-q.com/p/"
SIGNATURE_LENGTH = 16
# Bumped if the scheme ever changes, so an old signature never verifies under a new one.
SIGNATURE_CONTEXT = "card:v1:"

SignatureCheck = Literal["valid", "invalid", "unknown"]


def profile_url(uuid: str) -> str:
    """The public profile link, unsigned: what sharing a profile uses."""
    return f"{PROFILE_URL}{uuid}"


def card_signature(uuid: str) -> str | None:
    """The card's signature, or None when this server has no ``CARD_QR_SECRET``."""
    secret = config.CARD_QR_SECRET
    if not secret:
        return None
    digest = hmac.new(secret.encode(), f"{SIGNATURE_CONTEXT}{uuid}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:SIGNATURE_LENGTH]


def card_qr_value(uuid: str) -> str:
    """What a member's card QR encodes: the profile link, plus ``?s=`` when a secret is set."""
    signature = card_signature(uuid)
    return profile_url(uuid) if signature is None else f"{profile_url(uuid)}?s={signature}"


def verify_card_signature(uuid: str, signature: str) -> SignatureCheck:
    """Whether ``signature`` is this card's. ``unknown`` when this server cannot tell (no secret)."""
    expected = card_signature(uuid)
    if expected is None:
        return "unknown"
    return "valid" if hmac.compare_digest(expected.encode(), signature.encode()) else "invalid"
