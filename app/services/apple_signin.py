"""Sign in with Apple, server side: revoking the app's tokens when a member deletes their account.

App Store guideline 5.1.1(v) asks an app that offers Sign in with Apple to
revoke the user's tokens when they delete their account. The member app asks
Apple for a fresh authorization code right before calling ``DELETE /members/me``
and sends it in the body; this module trades the code for tokens and revokes
them (Apple: "Revoke tokens", TN3194).

The client secret is an ES256 JWT signed with a Sign in with Apple key of the
club's developer account (``APPLE_SIGNIN_KEY_ID`` / ``APPLE_SIGNIN_PRIVATE_KEY``,
team ``APPLE_TEAM_ID``) for the app's App ID (``APPLE_SIGNIN_CLIENT_ID``).

Nothing here can fail a deletion: every problem is a ``False`` and a WARNING.
The code, the tokens and the key are never logged.
"""

import logging
import time
from typing import Any

import httpx
import jwt

from app.config import config

logger = logging.getLogger(__name__)

APPLE_ID_URL = "https://appleid.apple.com"
TOKEN_PATH = "/auth/token"
REVOKE_PATH = "/auth/revoke"
TIMEOUT_SECONDS = 10.0
# Apple accepts up to six months; a secret is made for each deletion and used at once.
CLIENT_SECRET_LIFETIME_SECONDS = 300
MAX_CODE_LENGTH = 1000


def is_configured() -> bool:
    return bool(config.APPLE_SIGNIN_KEY_ID and config.APPLE_SIGNIN_PRIVATE_KEY)


def client_secret(now: int | None = None) -> str:
    """The client secret Apple's token endpoints authenticate the app with."""
    issued_at = int(time.time()) if now is None else now
    claims = {
        "iss": config.APPLE_TEAM_ID,
        "iat": issued_at,
        "exp": issued_at + CLIENT_SECRET_LIFETIME_SECONDS,
        "aud": APPLE_ID_URL,
        "sub": config.APPLE_SIGNIN_CLIENT_ID,
    }
    # Keep the key in a variable named ``secret`` (Sentry scrubs it).
    secret = config.APPLE_SIGNIN_PRIVATE_KEY
    if not secret:
        raise ValueError("APPLE_SIGNIN_PRIVATE_KEY is not set")
    return jwt.encode(claims, secret, algorithm="ES256", headers={"kid": config.APPLE_SIGNIN_KEY_ID})


def _post(client: httpx.Client, path: str, form: dict[str, str]) -> httpx.Response:
    return client.post(path, data=form, headers={"Accept": "application/json"})


def _apple_error(response: httpx.Response) -> str:
    """Apple's ``error`` code (``invalid_grant``, ``invalid_client``...): safe to log, unlike the body's rest."""
    try:
        body = response.json()
    except ValueError:
        return "-"
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and len(error) <= 64 else "-"


def _apple_user_id(id_token: Any) -> str | None:
    """The ``sub`` of the id token in Apple's token answer, after checking it was issued to this app.

    The token came straight from Apple over TLS in answer to our own request, so
    its claims are read without fetching Apple's keys; ``iss`` and ``aud`` are
    still checked so a token meant for another client is never trusted.
    """
    if not isinstance(id_token, str):
        return None
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    if claims.get("iss") != APPLE_ID_URL:
        return None
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if config.APPLE_SIGNIN_CLIENT_ID not in audiences:
        return None
    subject = claims.get("sub")
    return subject if isinstance(subject, str) and subject else None


def revoke_tokens(authorization_code: str, apple_user_id: str | None, member_ref: str) -> bool:
    """Trade the member app's authorization code for tokens and revoke them. ``True`` only when Apple confirmed.

    ``apple_user_id`` is the Apple account the Clerk user signed in with; the
    code must belong to it, so a code from some other Apple ID revokes nothing.
    ``member_ref`` only labels the log lines. Never raises: the account is
    already deleted when this runs.
    """
    try:
        return _revoke_tokens(authorization_code, apple_user_id, member_ref)
    except Exception as exc:
        # ERROR (Sentry hears of it) but without the traceback: its frames' locals hold the
        # authorization code and Apple's refresh and access tokens, under names Sentry's
        # scrubber does not match.
        logger.error("Apple token revocation failed unexpectedly for %s: %s", member_ref, type(exc).__name__)
        return False


def _revoke_tokens(authorization_code: str, apple_user_id: str | None, member_ref: str) -> bool:
    if not is_configured():
        logger.warning("Apple token revocation skipped for %s: APPLE_SIGNIN_KEY_ID/PRIVATE_KEY not set", member_ref)
        return False
    if not authorization_code or len(authorization_code) > MAX_CODE_LENGTH:
        logger.warning("Apple token revocation skipped for %s: no usable authorization code", member_ref)
        return False
    if not apple_user_id:
        logger.warning("Apple token revocation skipped for %s: the sign-in has no Apple account", member_ref)
        return False

    try:
        secret = client_secret()
    except Exception as exc:
        # A key that does not load: the exception text can quote it, so only its type is logged.
        logger.warning(
            "Apple token revocation failed for %s: the key could not sign (%s)", member_ref, type(exc).__name__
        )
        return False

    client_id = config.APPLE_SIGNIN_CLIENT_ID
    try:
        with httpx.Client(base_url=APPLE_ID_URL, timeout=TIMEOUT_SECONDS) as client:
            exchange = _post(
                client,
                TOKEN_PATH,
                {
                    "client_id": client_id,
                    "client_secret": secret,
                    "code": authorization_code,
                    "grant_type": "authorization_code",
                },
            )
            if exchange.status_code != 200:
                logger.warning(
                    "Apple refused the authorization code for %s: HTTP %s %s",
                    member_ref,
                    exchange.status_code,
                    _apple_error(exchange),
                )
                return False
            tokens = exchange.json()
            if not isinstance(tokens, dict):
                raise ValueError("token answer is not an object")
            if _apple_user_id(tokens.get("id_token")) != apple_user_id:
                logger.warning(
                    "Apple token revocation skipped for %s: the code belongs to a different Apple account", member_ref
                )
                return False
            refresh_token = tokens.get("refresh_token")
            access_token = tokens.get("access_token")
            if isinstance(refresh_token, str) and refresh_token:
                token, hint = refresh_token, "refresh_token"
            elif isinstance(access_token, str) and access_token:
                token, hint = access_token, "access_token"
            else:
                logger.warning("Apple's token answer for %s carried no token to revoke", member_ref)
                return False
            revoke = _post(
                client,
                REVOKE_PATH,
                {"client_id": client_id, "client_secret": secret, "token": token, "token_type_hint": hint},
            )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Apple token revocation failed for %s: %s", member_ref, type(exc).__name__)
        return False

    if revoke.status_code != 200:
        logger.warning(
            "Apple refused to revoke the tokens for %s: HTTP %s %s",
            member_ref,
            revoke.status_code,
            _apple_error(revoke),
        )
        return False
    logger.info("Apple tokens revoked for %s", member_ref)
    return True
