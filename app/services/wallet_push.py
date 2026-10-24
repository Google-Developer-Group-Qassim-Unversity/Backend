"""Apple Wallet pass update pushes: APNs with the Pass Type ID certificate.

The member app's pushes (``app/services/push/apns.py``) authenticate with a
``.p8`` key that only covers the app's topic. A pass update push must come from
the pass's own certificate - the same p12 that signs the passes
(``app/wallet_signer.py``) - presented as the TLS client certificate, with the
pass type id as ``apns-topic`` and an empty JSON dictionary as the payload.
Apple delivers these from the production host only. No ``apns-push-type``
(Apple names none for passes) and the default priority, 10.

Python's ``ssl`` module loads a client certificate only from a file, so the
key never sits there in the clear: it is written encrypted with a one-time
password that exists only in memory, to a memfd (Linux: RAM, no path on any
disk) or, where there is none (macOS), a 0600 temporary file deleted as soon
as it is loaded. One HTTP/2 client per worker, built on the first push.

A push that is owed is a row: ``wallet_pass_registrations.push_due_at``
(``app/services/wallet_updates.py`` sets it for the welcome). ``deliver_due``
runs right after the registration that owes it (``BackgroundTasks``) and after
every pipeline sweep, in the three steps of ``app/services/push/delivery.py``:
claim (``FOR UPDATE SKIP LOCKED`` and a 5-minute lease), send, record. A
dropped connection is retried at once; other failures back off 2, 4, 8 and 16
minutes and give up after 5 attempts. A token APNs says is dead deletes its
registration. Lines name the registration row, never the token.
"""

import logging
import os
import secrets
import ssl
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import certifi
import httpx
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, Encoding, PrivateFormat
from starlette.concurrency import run_in_threadpool

from app.config import config
from app.DB import wallet_passes as pass_queries
from app.DB.main import db_session
from app.services.push import apns, clock, delivery
from app.wallet_signer import load_pass_certificate

logger = logging.getLogger(__name__)

HOST = apns.HOSTS["production"]
PAYLOAD = b"{}"
TIMEOUT_SECONDS = 10.0
# How long APNs keeps the push for a device that is offline.
EXPIRATION = timedelta(days=1)
LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 5
BATCH_SIZE = 100
CONCURRENCY = 10


class PassPushNotConfigured(RuntimeError):
    """The Pass Type ID certificate cannot be loaded. Says why, never what it holds."""


@dataclass(frozen=True)
class Due:
    """A registration owed a push, as claimed."""

    id: str
    member_id: int
    # Out of the repr, so a Due in a traceback's locals (Sentry) never shows it.
    token: str = field(repr=False)
    attempts: int


@dataclass
class PassPushResult:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    gave_up: int = 0
    registrations_removed: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "sent": self.sent,
            "retried": self.retried,
            "gave_up": self.gave_up,
            "registrations_removed": self.registrations_removed,
        }


# ---------------------------------------------------------------- client


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _load_identity(context: ssl.SSLContext, pem: bytes, password: bytes) -> None:
    """Load the certificate chain and the (encrypted) key into ``context`` without leaving a file behind."""
    memfd_create = getattr(os, "memfd_create", None)
    if memfd_create is not None:
        fd = memfd_create("gdg-pass-push", getattr(os, "MFD_CLOEXEC", 0))
        try:
            _write_all(fd, pem)
            context.load_cert_chain(f"/proc/self/fd/{fd}", password=password)
        finally:
            os.close(fd)
        return
    # NamedTemporaryFile is created 0600 and deleted when the block ends.
    with tempfile.NamedTemporaryFile(prefix="gdg-pass-push-", suffix=".pem") as handle:
        handle.write(pem)
        handle.flush()
        context.load_cert_chain(handle.name, password=password)


def ssl_context() -> ssl.SSLContext:
    """A TLS context that presents the Pass Type ID certificate to APNs."""
    try:
        private_key, certificate, chain = load_pass_certificate()
    except ValueError as exc:
        raise PassPushNotConfigured(str(exc)) from None
    password = secrets.token_urlsafe(32).encode()
    pem = b"".join(
        [
            certificate.public_bytes(Encoding.PEM),
            *(extra.public_bytes(Encoding.PEM) for extra in chain),
            private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(password)),
        ]
    )
    context = ssl.create_default_context(cafile=certifi.where())
    _load_identity(context, pem, password)
    return context


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """The worker's client, built on first use. Raises ``PassPushNotConfigured`` without a usable p12."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(http2=True, verify=ssl_context(), timeout=TIMEOUT_SECONDS)
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """Tests put an ``httpx.MockTransport`` client here."""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------- sending


def _answer(response: httpx.Response) -> apns.ApnsResponse:
    if response.status_code == 200:
        return apns.ApnsResponse(200)
    try:
        body = response.json()
    except ValueError:
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None
    timestamp = body.get("timestamp") if isinstance(body, dict) else None
    return apns.ApnsResponse(
        response.status_code,
        str(reason)[:100] if reason else None,
        int(timestamp) if isinstance(timestamp, int | float) else None,
    )


async def send(client: httpx.AsyncClient, push_token: str) -> apns.ApnsResponse:
    """One pass update push to one device. A connection that dropped is tried once more at once."""
    url = f"{HOST}/3/device/{push_token}"
    answer = apns.ApnsResponse(0)
    for _ in range(2):
        headers = {
            "apns-topic": config.APPLE_PASS_TYPE_ID,
            "apns-expiration": str(clock.epoch(clock.now() + EXPIRATION)),
            "apns-id": str(uuid.uuid4()),
            "content-type": "application/json",
        }
        try:
            answer = _answer(await client.post(url, content=PAYLOAD, headers=headers))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            answer = apns.ApnsResponse(0, type(exc).__name__)
        if answer.status != 0:
            return answer
    return answer


async def _send_one(client: httpx.AsyncClient, due: Due) -> apns.ApnsResponse:
    try:
        return await send(client, due.token)
    except Exception as exc:
        # Only the type: the traceback's locals hold the token. Retried like a timeout.
        logger.warning("Wallet pass push: registration %s request failed (%s)", due.id, type(exc).__name__)
        return apns.ApnsResponse(0, type(exc).__name__[:100])


async def send_all(due: list[Due]) -> list[tuple[Due, apns.ApnsResponse]]:
    try:
        client = get_client()
    except PassPushNotConfigured as exc:
        return [(item, apns.ApnsResponse(-1, str(exc)[:100])) for item in due]
    answers = await apns.gather_limited([_send_one(client, item) for item in due], CONCURRENCY)
    return list(zip(due, answers))


# ---------------------------------------------------------------- claim and record


def claim(now: datetime, limit: int = BATCH_SIZE) -> list[Due]:
    """Lease the registrations owed a push (``push_due_at`` 5 minutes on, one more attempt) and return them."""
    with db_session() as session:
        rows = pass_queries.lock_due_pushes(session, now, limit)
        if not rows:
            session.rollback()
            return []
        due = []
        for row in rows:
            row.push_due_at = now + LEASE
            row.push_attempts += 1
            due.append(Due(row.id, row.member_id, row.push_token, row.push_attempts))
        session.commit()
        return due


def record(results: list[tuple[Due, apns.ApnsResponse]], now: datetime) -> PassPushResult:
    outcome = PassPushResult(claimed=len(results))
    config_errors: list[str] = []
    with db_session() as session:
        for due, answer in results:
            kind = delivery.classify(answer)
            if kind == "sent":
                pass_queries.set_push_due(session, due.id, None, attempts=0)
                outcome.sent += 1
                logger.info("Wallet pass push sent to registration %s (member %s)", due.id, due.member_id)
                continue
            if kind in ("unregistered", "bad_token"):
                seen_before = clock.from_epoch_ms(answer.timestamp_ms) if answer.timestamp_ms else None
                if pass_queries.forget(session, due.id, push_token=due.token, seen_before=seen_before):
                    outcome.registrations_removed += 1
                logger.warning(
                    "Wallet pass push: registration %s refused (%s %s)", due.id, answer.status, answer.reason
                )
                continue
            if kind == "config":
                config_errors.append(answer.reason or str(answer.status))
            if kind == "permanent" or due.attempts >= MAX_ATTEMPTS:
                pass_queries.set_push_due(session, due.id, None, attempts=due.attempts)
                outcome.gave_up += 1
                logger.warning(
                    "Wallet pass push to registration %s given up after %d attempt(s) (%s %s)",
                    due.id,
                    due.attempts,
                    answer.status,
                    answer.reason,
                )
                continue
            retry_at = now + timedelta(minutes=2**due.attempts)
            pass_queries.set_push_due(session, due.id, retry_at, attempts=due.attempts)
            outcome.retried += 1
            if kind != "config":
                logger.warning(
                    "Wallet pass push: registration %s answered %s %s; retried", due.id, answer.status, answer.reason
                )
        session.commit()
    if config_errors:
        # Once per batch: a bad certificate or topic fails every request the same way.
        logger.error(
            "APNs refused the Pass Type ID certificate or topic (%s) for %d request(s); retried",
            ", ".join(sorted(set(config_errors))),
            len(config_errors),
        )
    return outcome


# ---------------------------------------------------------------- entry points


async def deliver_due(batch_size: int = BATCH_SIZE) -> PassPushResult:
    """Send every pass update push that is owed. Does nothing while pass updates are off."""
    if not config.apple_pass_updates_enabled:
        return PassPushResult()
    due = await run_in_threadpool(claim, clock.now(), batch_size)
    if not due:
        return PassPushResult()
    results = await send_all(due)
    outcome = await run_in_threadpool(record, results, clock.now())
    logger.info("Wallet pass pushes: %s", outcome.counts())
    return outcome


async def kick() -> None:
    """``BackgroundTasks`` and sweep entry: send what is owed, without failing anything else."""
    try:
        await deliver_due()
    except Exception:
        logger.exception("Wallet pass push delivery failed")
