"""In-app OAuth reconnect and encrypted database storage for Google Forms credentials.

The club's Google Forms integration runs on one club-owned Google account
(see ``docs/GOOGLE_FORMS.md``). Previously, rotating its refresh token meant
running ``scripts/setup_google_oauth.py`` on a laptop, pasting the token into
Infisical, and restarting every backend worker.

This module stores the refresh token in ``google_oauth_credentials`` encrypted
at rest with Fernet (keyed via HKDF-SHA256 from ``GOOGLE_TOKEN_ENCRYPTION_KEY``),
and drives a server-side OAuth authorization-code exchange initiated from the
Admin Settings page:

1. ``build_authorization_url(actor_member_id)`` mints a short-lived signed
   ``state`` JWT bound to the acting admin and returns Google's consent URL.
2. Google redirects the browser back to ``{ADMIN_APP_URL}/settings/google-forms/callback``.
3. The Admin app posts ``{code, state}`` to ``POST /forms/google-auth/exchange``
   using the admin's normal Clerk session.
4. ``exchange_authorization_code(...)`` verifies the signed ``state`` matches
   the caller, exchanges ``code`` server-to-server, confirms the signed-in
   Google account owns ``TEMPLATE_FORM_FILE_ID`` (so an admin cannot link their
   personal Gmail by mistake), encrypts and saves the refresh token in MySQL,
   and invalidates the health cache.

Every worker reads the database row first and falls back to
``GOOGLE_REFRESH_TOKEN`` in the environment when no row is stored (or when
``GOOGLE_TOKEN_ENCRYPTION_KEY`` is unset), so existing deployments keep working
unchanged and reconnects take effect immediately without restarting PM2.
"""

import base64
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.DB import google_oauth as oauth_queries
from app.DB.main import db_session
from app.DB.schema import GoogleOAuthCredentials
from app.config import config, get_settings
from app.exceptions import (
    GoogleFormsOAuthExchangeFailed,
    GoogleFormsOAuthMissingRefreshToken,
    GoogleFormsOAuthNotConfigured,
    GoogleFormsOAuthStateInvalid,
    GoogleFormsOAuthWrongAccount,
)

logger = logging.getLogger(__name__)

FORMS_SERVICE = "forms"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
TIMEOUT = 20.0
STATE_TTL_MINUTES = 10
STATE_PURPOSE = "google_forms_oauth"
HKDF_SALT = b"gdg-qassim-google-oauth-v1"

# Same three scopes as scripts/setup_google_oauth.py (full `drive` is needed so
# `files.copy()` can copy the pre-existing template form).
FORMS_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
)


# --------------------------------------------------------------------------- crypto


def _hkdf(secret: str, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT, info=info).derive(secret.encode("utf-8"))


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_hkdf(secret, b"fernet-token-encryption")))


def _state_signing_key(secret: str) -> bytes:
    return _hkdf(secret, b"oauth-state-signing")


def encrypt_refresh_token(refresh_token: str) -> str:
    secret = config.GOOGLE_TOKEN_ENCRYPTION_KEY
    if not secret:
        raise GoogleFormsOAuthNotConfigured("missing_encryption_key")
    return _fernet(secret).encrypt(refresh_token.encode("utf-8")).decode("ascii")


def decrypt_refresh_token(ciphertext: str) -> str:
    secret = config.GOOGLE_TOKEN_ENCRYPTION_KEY
    if not secret:
        raise GoogleFormsOAuthNotConfigured("missing_encryption_key")
    return _fernet(secret).decrypt(ciphertext.encode("ascii")).decode("utf-8")


# --------------------------------------------------------------------------- resolution


def reconnect_availability() -> tuple[bool, str | None]:
    """Whether in-app Google Forms OAuth reconnect can run on this server."""
    settings = get_settings()
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        return False, "missing_google_client"
    if not config.GOOGLE_TOKEN_ENCRYPTION_KEY:
        return False, "missing_encryption_key"
    return True, None


def _load_db_credential(session: Session | None) -> GoogleOAuthCredentials | None:
    if session is not None:
        return oauth_queries.get_oauth_credential(session, FORMS_SERVICE)
    if not get_settings().DATABASE_URL:
        return None
    try:
        with db_session() as standalone:
            return oauth_queries.get_oauth_credential(standalone, FORMS_SERVICE)
    except SQLAlchemyError as exc:
        logger.warning("Could not read stored Google OAuth credential from DB; falling back to env (%s)", exc)
        return None


def resolve_forms_refresh_token(
    session: Session | None = None,
) -> tuple[str | None, Literal["database", "env", "none"]]:
    """Return ``(refresh_token, source)``, preferring the encrypted DB row over the env var."""
    if config.GOOGLE_TOKEN_ENCRYPTION_KEY:
        row = _load_db_credential(session)
        if row is not None:
            try:
                return decrypt_refresh_token(row.encrypted_refresh_token), "database"
            except InvalidToken:
                logger.error(
                    "Stored Google Forms refresh token could not be decrypted with GOOGLE_TOKEN_ENCRYPTION_KEY; "
                    "falling back to GOOGLE_REFRESH_TOKEN if set."
                )

    env_token = (get_settings().GOOGLE_REFRESH_TOKEN or "").strip()
    if env_token:
        return env_token, "env"
    return None, "none"


# --------------------------------------------------------------------------- OAuth flow


def build_authorization_url(actor_member_id: int) -> str:
    """Create the Google OAuth consent URL with a short-lived signed state bound to ``actor_member_id``."""
    available, reason = reconnect_availability()
    if not available:
        raise GoogleFormsOAuthNotConfigured(reason or "unavailable")

    secret = config.GOOGLE_TOKEN_ENCRYPTION_KEY
    assert secret is not None
    redirect_uri = config.GOOGLE_FORMS_OAUTH_REDIRECT_URI
    now = datetime.now(timezone.utc)
    state = jwt.encode(
        {
            "purpose": STATE_PURPOSE,
            "sub": str(actor_member_id),
            "redirect_uri": redirect_uri,
            "nonce": secrets.token_urlsafe(16),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=STATE_TTL_MINUTES)).timestamp()),
        },
        _state_signing_key(secret),
        algorithm="HS256",
    )

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(FORMS_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _verify_state(state: str, actor_member_id: int) -> str:
    """Verify the signed OAuth state and return its bound ``redirect_uri``."""
    secret = config.GOOGLE_TOKEN_ENCRYPTION_KEY
    if not secret:
        raise GoogleFormsOAuthNotConfigured("missing_encryption_key")

    try:
        payload = jwt.decode(state, _state_signing_key(secret), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise GoogleFormsOAuthStateInvalid() from None

    if payload.get("purpose") != STATE_PURPOSE or payload.get("sub") != str(actor_member_id):
        raise GoogleFormsOAuthStateInvalid()

    redirect_uri = payload.get("redirect_uri")
    if not isinstance(redirect_uri, str) or not redirect_uri:
        raise GoogleFormsOAuthStateInvalid()
    return redirect_uri


def _json_dict(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _exchange_code_with_google(code: str, redirect_uri: str) -> tuple[str, str]:
    """Exchange the authorization code for ``(access_token, refresh_token)`` without logging tokens."""
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": config.GOOGLE_CLIENT_ID,
                    "client_secret": config.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError:
        raise GoogleFormsOAuthExchangeFailed("Could not reach Google's OAuth token endpoint") from None

    payload = _json_dict(response)
    if response.status_code >= 400:
        err = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
        logger.warning("Google OAuth code exchange failed: %s", err)
        raise GoogleFormsOAuthExchangeFailed(f"Google rejected the authorization code ({err})")

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleFormsOAuthExchangeFailed("Google did not return an access token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise GoogleFormsOAuthMissingRefreshToken()

    return access_token, refresh_token


def _verify_google_account(access_token: str) -> str:
    """Resolve the signed-in Google account email and verify it owns ``TEMPLATE_FORM_FILE_ID``."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            about_resp = client.get(DRIVE_ABOUT_URL, params={"fields": "user(emailAddress)"}, headers=headers)
            if about_resp.status_code >= 400:
                raise GoogleFormsOAuthExchangeFailed("Could not identify the signed-in Google account")
            about_data = _json_dict(about_resp)
            user = about_data.get("user")
            email = user.get("emailAddress") if isinstance(user, dict) else None
            if not isinstance(email, str) or not email.strip():
                raise GoogleFormsOAuthExchangeFailed("Google Drive did not return an account email")
            email = email.strip()

            template_id = (get_settings().TEMPLATE_FORM_FILE_ID or "").strip()
            if template_id:
                file_resp = client.get(
                    f"{DRIVE_FILES_URL}/{template_id}", params={"fields": "id,ownedByMe"}, headers=headers
                )
                if file_resp.status_code in (403, 404):
                    raise GoogleFormsOAuthWrongAccount(email)
                if file_resp.status_code >= 400:
                    raise GoogleFormsOAuthExchangeFailed("Could not verify access to the template Google Form")
                file_data = _json_dict(file_resp)
                if not file_data.get("ownedByMe"):
                    raise GoogleFormsOAuthWrongAccount(email)
    except httpx.HTTPError:
        raise GoogleFormsOAuthExchangeFailed("Could not reach Google Drive to verify the account") from None

    return email


def exchange_authorization_code(
    session: Session, *, code: str, state: str, actor_member_id: int
) -> GoogleOAuthCredentials:
    """Validate state, exchange code with Google, verify account ownership, and persist encrypted token."""
    available, reason = reconnect_availability()
    if not available:
        raise GoogleFormsOAuthNotConfigured(reason or "unavailable")

    redirect_uri = _verify_state(state, actor_member_id)
    access_token, refresh_token = _exchange_code_with_google(code.strip(), redirect_uri)
    account_email = _verify_google_account(access_token)

    encrypted = encrypt_refresh_token(refresh_token)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = oauth_queries.upsert_oauth_credential(
        session,
        service=FORMS_SERVICE,
        encrypted_refresh_token=encrypted,
        account_email=account_email,
        connected_by=actor_member_id,
        now=now,
    )
    session.commit()

    from app.services.google_health import invalidate_credential_cache

    invalidate_credential_cache()
    logger.info("Connected Google Forms OAuth account %s (member_id=%s)", account_email, actor_member_id)
    return row


def get_forms_auth_status(session: Session, *, force_check: bool = False) -> dict[str, Any]:
    """Current health and metadata for the club Google Forms OAuth connection."""
    from app.services.google_health import credential_status

    row = oauth_queries.get_oauth_credential(session, FORMS_SERVICE)
    can_reconnect, unavailable_reason = reconnect_availability()

    _, source = resolve_forms_refresh_token(session=session)
    has_db_token = source == "database" and row is not None

    health = credential_status(force=force_check)
    connected_by_name = row.connector.name if has_db_token and row and row.connector else None

    return {
        "valid": bool(health["valid"]),
        "reason": health["reason"],
        "detail": health["detail"],
        "checked_at": health["checked_at"],
        "source": source,
        "account_email": row.account_email if has_db_token and row else None,
        "connected_at": row.connected_at if has_db_token and row else None,
        "connected_by_name": connected_by_name,
        "can_reconnect": can_reconnect,
        "reconnect_unavailable_reason": unavailable_reason,
    }
