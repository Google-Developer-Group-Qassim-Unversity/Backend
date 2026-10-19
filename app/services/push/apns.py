"""Apple Push Notification service: the HTTP/2 client and the provider token.

APNs takes HTTP/2 over TLS only. We authenticate with a token-based auth key
(the ``.p8``), not a certificate: every request carries a short ES256 JWT,
the provider token, signed with that key. Apple wants it refreshed no more
than once every 20 minutes and at least once an hour, so ``iat`` is the
current time rounded down to a 50-minute boundary: the four uvicorn workers
sign the same ``iat``, each rebuilds once per boundary, and no token is older
than 50 minutes.

The device token travels in the request path, so it is kept out of every log
line and Sentry event (``app/services/push/redact.py``). Nothing here logs the
token, the JWT or the key.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.config import config
from app.services.push import redact

logger = logging.getLogger(__name__)

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
TIMEOUT_SECONDS = 10.0
TOKEN_BOUNDARY_SECONDS = 50 * 60
MAX_PAYLOAD_BYTES = 4096

redact.install_log_filter()


class ApnsNotConfigured(RuntimeError):
    """The key id or the key is missing, or the key cannot be read. Says which, never what the key is."""


@dataclass(frozen=True)
class ApnsResponse:
    """What APNs answered for one device. ``status`` 0 means no answer: a timeout or a connection error."""

    status: int
    reason: str | None = None
    # 410 only: when APNs learned the token stopped being valid, in milliseconds since the epoch.
    timestamp_ms: int | None = None


# ---------------------------------------------------------------- client

_client: httpx.AsyncClient | None = None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(http2=True, timeout=TIMEOUT_SECONDS)


async def open_client() -> None:
    """Created in the app lifespan, next to the shared HTTP client: one HTTP/2 connection per worker."""
    global _client
    if _client is None:
        _client = _new_client()


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _new_client()
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """Tests put an ``httpx.MockTransport`` client here."""
    global _client
    _client = client


# ---------------------------------------------------------------- provider token

_token_cache: tuple[int, str, str] | None = None  # (boundary, key id, jwt)


def _signing_key() -> str:
    key_id = config.APNS_KEY_ID
    key = config.APNS_PRIVATE_KEY
    if not key_id:
        raise ApnsNotConfigured("APNS_KEY_ID is not set")
    if not key:
        raise ApnsNotConfigured("APNS_PRIVATE_KEY (or APNS_PRIVATE_KEY_PATH) is not set or cannot be read")
    try:
        load_pem_private_key(key.encode(), password=None)
    except (ValueError, TypeError):
        # cryptography's message says nothing about the key's contents, but stay generic anyway.
        raise ApnsNotConfigured("the APNs private key is not a readable .p8 (PEM) key") from None
    return key


def provider_token(*, now: float | None = None, force: bool = False) -> str:
    """The cached JWT for the current 50-minute boundary. ``force``: sign a new one now (Apple said it expired)."""
    global _token_cache
    now = time.time() if now is None else now
    boundary = int(now // TOKEN_BOUNDARY_SECONDS) * TOKEN_BOUNDARY_SECONDS
    key_id = config.APNS_KEY_ID or ""
    cached = _token_cache
    if not force and cached is not None and cached[0] == boundary and cached[1] == key_id:
        return cached[2]
    issued_at = int(now) if force else boundary
    token = jwt.encode(
        {"iss": config.APPLE_TEAM_ID, "iat": issued_at}, _signing_key(), algorithm="ES256", headers={"kid": key_id}
    )
    _token_cache = (boundary, key_id, token)
    return token


def reset_provider_token() -> None:
    global _token_cache
    _token_cache = None


# ---------------------------------------------------------------- sending


def device_payload(*, title: str, body: str, url: str, kind: str, notification_id: str, thread_id: str) -> bytes:
    """The APNs JSON. The app reads ``body`` (expo-notifications' ``content.data``) and falls back to the top level."""
    data = {"url": url, "kind": kind, "id": notification_id}
    payload = {
        "aps": {"alert": {"title": title, "body": body}, "sound": "default", "thread-id": thread_id},
        **data,
        "body": data,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


async def send(
    device_token: str,
    payload: bytes,
    *,
    priority: int,
    expiration: int,
    collapse_id: str,
    push_type: str = "alert",
    client: httpx.AsyncClient | None = None,
) -> ApnsResponse:
    """POST one notification to one device. Rebuilds the provider token once if Apple says it expired."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        return ApnsResponse(413, "PayloadTooLarge")
    client = client or get_client()
    url = f"{HOSTS[config.APNS_ENVIRONMENT]}/3/device/{device_token}"
    answer = await _post(client, url, payload, priority, expiration, collapse_id, push_type, force=False)
    if answer.status == 403 and answer.reason == "ExpiredProviderToken":
        answer = await _post(client, url, payload, priority, expiration, collapse_id, push_type, force=True)
    return answer


async def _post(
    client: httpx.AsyncClient,
    url: str,
    payload: bytes,
    priority: int,
    expiration: int,
    collapse_id: str,
    push_type: str,
    *,
    force: bool,
) -> ApnsResponse:
    headers = {
        "authorization": f"bearer {provider_token(force=force)}",
        "apns-topic": config.APNS_TOPIC,
        "apns-push-type": push_type,
        "apns-priority": str(priority),
        "apns-expiration": str(expiration),
        "apns-collapse-id": collapse_id,
        "apns-id": str(uuid.uuid4()),
        "content-type": "application/json",
    }
    try:
        response = await client.post(url, content=payload, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return ApnsResponse(0, type(exc).__name__)
    if response.status_code == 200:
        return ApnsResponse(200)
    return _parse(response)


def _parse(response: httpx.Response) -> ApnsResponse:
    try:
        body = response.json()
    except ValueError:
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None
    timestamp = body.get("timestamp") if isinstance(body, dict) else None
    return ApnsResponse(
        response.status_code,
        str(reason)[:100] if reason else None,
        int(timestamp) if isinstance(timestamp, int | float) else None,
    )


async def gather_limited(coroutines, limit: int):
    """Run ``coroutines`` with at most ``limit`` in flight (APNs is one connection, many streams)."""
    semaphore = asyncio.Semaphore(limit)

    async def run(coroutine):
        async with semaphore:
            return await coroutine

    return await asyncio.gather(*(run(c) for c in coroutines))
