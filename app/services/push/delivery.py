"""Sending what the outbox holds.

``deliver_pending`` runs right after a route commits a push (``kick``, from
``BackgroundTasks``) and after every pipeline sweep, for anything left:
deferred by quiet hours, retried, or queued by the sweep itself.

Three short steps, so no transaction or row lock is open while Apple is called:

1. **Claim** due rows in a transaction of their own: ``SELECT ... FOR UPDATE
   SKIP LOCKED``, so two workers (or a kick and the sweep) never take the same
   row. Each claimed row gets a lease - ``not_before`` five minutes ahead,
   ``attempts`` + 1 - and the transaction commits. A worker that dies while
   sending leaves rows that come back when the lease ends. Rows whose push no
   longer makes sense (expired, switch turned off, cause gone, no phone) are
   marked ``skipped`` here instead.
2. **Send** to every phone of each member, at most 20 requests at a time.
3. **Record** each row's outcome in a new short transaction.

Logging follows ``app/main.py``'s rule: WARNING for what is expected (a dead
token, an expired push), ERROR only when something is broken (a bad key) and
once per batch, so Sentry's quota is not spent on dead tokens. Lines name the
outbox row and the device row, never the token.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import config
from app.DB import push as push_queries
from app.DB.main import db_session
from app.DB.schema import MemberNotificationPrefs, Members, PushKind, PushNotifications, PushStatus
from app.services.push import apns, cases, clock, outbox, triggers

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_BATCHES = 5
LEASE = timedelta(minutes=5)
CONCURRENCY = 20
MAX_ATTEMPTS = 5
NEW_EVENT_DAILY_CAP = 2
# apns-priority 5 (power-considerate) for what is not about to happen; 10 for the rest.
LOW_PRIORITY = {
    PushKind.NEW_EVENT,
    PushKind.BROADCAST,
    PushKind.REGISTRATION_CLOSING,
    PushKind.CERTIFICATE,
    PushKind.EVENT_CONTENT,
    PushKind.WEEKLY_SUMMARY,
    PushKind.WELCOME,
}

GONE_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic"}
CONFIG_REASONS = {"InvalidProviderToken", "TopicDisallowed", "BadTopic", "BadCertificateEnvironment", "BadCertificate"}


@dataclass(frozen=True)
class Device:
    id: str
    # Out of the repr, so a Job or Device in a traceback's locals (Sentry) never shows it.
    token: str = field(repr=False)
    locale: str
    last_seen_at: datetime


@dataclass
class Job:
    id: str
    member_id: int
    kind: PushKind
    dedupe_key: str
    payload: dict
    expires_at: datetime
    attempts: int
    devices: list[Device]


@dataclass
class DeliveryResult:
    claimed: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    retried: int = 0
    devices_removed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "sent": self.sent,
            "skipped": self.skipped,
            "failed": self.failed,
            "retried": self.retried,
            "devices_removed": self.devices_removed,
        }


# ---------------------------------------------------------------- 1. claim


def lock_due(session: Session, now: datetime, limit: int) -> list[PushNotifications]:
    """Due rows no other transaction holds. The caller's transaction keeps them locked until it ends."""
    return list(
        session.scalars(
            select(PushNotifications)
            .where(PushNotifications.status == PushStatus.PENDING, PushNotifications.not_before <= now)
            .order_by(PushNotifications.not_before, PushNotifications.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )


def claim(now: datetime, limit: int = BATCH_SIZE) -> tuple[list[Job], int]:
    """Lease the due rows and return what to send, plus how many rows were skipped instead."""
    with db_session() as session:
        rows = lock_due(session, now, limit)
        if not rows:
            session.rollback()
            return [], 0
        # The daily cap is counted under a lock on each member's row, so two workers claiming at the same
        # moment cannot both pass it. A member another worker holds: their capped rows wait for the next round.
        capped = {row.member_id for row in rows if row.kind in cases.CAPPED}
        held = lock_members(session, capped) if capped else set()
        rows = [row for row in rows if row.kind not in cases.CAPPED or row.member_id in held]
        if not rows:
            session.rollback()
            return [], 0
        member_ids = {row.member_id for row in rows}
        devices = push_queries.devices_for(session, member_ids, outbox.device_environment())
        switches = _switches(session, member_ids)
        sent_today = _sent_today(
            session, {r.member_id for r in rows if r.kind == PushKind.NEW_EVENT}, {PushKind.NEW_EVENT}, now
        )
        capped_today = _capped_today(session, held, {row.id for row in rows}, now)
        jobs: list[Job] = []
        skipped = 0
        for row in rows:
            reason = _skip_reason(session, row, now, devices, switches, sent_today, capped_today)
            if reason is not None:
                row.status = PushStatus.SKIPPED
                row.last_error = reason
                skipped += 1
                continue
            if row.kind == PushKind.NEW_EVENT:
                sent_today[row.member_id] = sent_today.get(row.member_id, 0) + 1
            if row.kind in cases.CAPPED:
                capped_today[row.member_id] = capped_today.get(row.member_id, 0) + 1
            row.not_before = now + LEASE
            row.attempts += 1
            jobs.append(
                Job(
                    id=row.id,
                    member_id=row.member_id,
                    kind=row.kind,
                    dedupe_key=row.dedupe_key,
                    payload=row.payload,
                    expires_at=row.expires_at,
                    attempts=row.attempts,
                    devices=[
                        Device(d.id, d.token, d.locale.value, d.last_seen_at) for d in devices.get(row.member_id, [])
                    ],
                )
            )
        session.commit()
        return jobs, skipped


def _switches(session: Session, member_ids: set[int]) -> dict[int, dict[str, bool]]:
    rows = session.scalars(select(MemberNotificationPrefs).where(MemberNotificationPrefs.member_id.in_(member_ids)))
    return {prefs.member_id: push_queries.prefs_as_dict(prefs) for prefs in rows}


def lock_members(session: Session, member_ids: set[int]) -> set[int]:
    """Lock these members' rows until the claim commits; the ones another transaction holds are left out."""
    return set(
        session.scalars(
            select(Members.id).where(Members.id.in_(member_ids)).order_by(Members.id).with_for_update(skip_locked=True)
        ).all()
    )


def _capped_today(session: Session, member_ids: set[int], batch: set[str], now: datetime) -> dict[int, int]:
    """Capped pushes each member was sent this Riyadh day, plus the ones out for delivery (claimed, not recorded).

    The batch's own rows are left out: the loop counts them as it claims them.
    """
    counts = _sent_today(session, member_ids, cases.CAPPED, now)
    if not member_ids:
        return counts
    out = session.execute(
        select(PushNotifications.member_id, func.count())
        .where(
            PushNotifications.member_id.in_(member_ids),
            PushNotifications.kind.in_(cases.CAPPED),
            PushNotifications.status == PushStatus.PENDING,
            PushNotifications.attempts > 0,
            PushNotifications.id.notin_(batch),
        )
        .group_by(PushNotifications.member_id)
    ).all()
    for member_id, count in out:
        counts[member_id] = counts.get(member_id, 0) + int(count)
    return counts


def _sent_today(session: Session, member_ids: set[int], kinds, now: datetime) -> dict[int, int]:
    """How many pushes of ``kinds`` each member was sent this Riyadh day."""
    if not member_ids:
        return {}
    start, end = clock.riyadh_day_bounds(now)
    rows = session.execute(
        select(PushNotifications.member_id, func.count())
        .where(
            PushNotifications.member_id.in_(member_ids),
            PushNotifications.kind.in_(kinds),
            PushNotifications.status == PushStatus.SENT,
            PushNotifications.sent_at >= start,
            PushNotifications.sent_at < end,
        )
        .group_by(PushNotifications.member_id)
    ).all()
    return {member_id: int(count) for member_id, count in rows}


def _skip_reason(
    session: Session,
    row: PushNotifications,
    now: datetime,
    devices: dict,
    switches: dict[int, dict[str, bool]],
    sent_today: dict[int, int],
    capped_today: dict[int, int],
) -> str | None:
    if row.expires_at <= now:
        return "expired"
    switch = row.payload.get("switch") or outbox.SWITCH_FOR.get(row.kind)
    if switch and not switches.get(row.member_id, push_queries.DEFAULT_PREFS).get(switch, False):
        return "switch_off"
    gone = cases.cause_gone(session, row) if row.kind in cases.KINDS else triggers.cause_gone(session, row)
    if gone is not None:
        return gone
    if row.kind == PushKind.NEW_EVENT and sent_today.get(row.member_id, 0) >= NEW_EVENT_DAILY_CAP:
        return "daily_cap"
    if row.kind in cases.CAPPED and capped_today.get(row.member_id, 0) >= cases.DAILY_CAP:
        return "daily_cap"
    if not devices.get(row.member_id):
        return "no_device"
    return None


# ---------------------------------------------------------------- 2. send


def collapse_id(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode()).hexdigest()[:32]


async def _send_one(job: Job, device: Device) -> apns.ApnsResponse:
    try:
        text = job.payload.get(device.locale) or job.payload.get("ar") or {}
        body = apns.device_payload(
            title=text.get("title", ""),
            body=text.get("body", ""),
            url=job.payload.get("url") or "gdgq://notifications",
            kind=job.kind.value,
            notification_id=job.id,
            thread_id=job.payload.get("thread_id") or "news",
        )
        return await apns.send(
            device.token,
            body,
            priority=5 if job.kind in LOW_PRIORITY else 10,
            expiration=clock.epoch(job.expires_at),
            collapse_id=collapse_id(job.dedupe_key),
        )
    except apns.ApnsNotConfigured as exc:
        return apns.ApnsResponse(-1, str(exc)[:100])
    except Exception as exc:
        # One request failing in a way nobody planned for must not take the batch's other
        # answers with it: those rows would stay leased unrecorded and be sent again. Only the
        # type is logged; the traceback's locals hold the token. Retried like a timeout.
        logger.warning("Push %s: device %s request failed (%s)", job.id, device.id, type(exc).__name__)
        return apns.ApnsResponse(0, type(exc).__name__[:100])


async def send(jobs: list[Job]) -> dict[str, list[tuple[Device, apns.ApnsResponse]]]:
    pairs = [(job, device) for job in jobs for device in job.devices]
    answers = await apns.gather_limited([_send_one(job, device) for job, device in pairs], CONCURRENCY)
    results: dict[str, list[tuple[Device, apns.ApnsResponse]]] = {job.id: [] for job in jobs}
    for (job, device), answer in zip(pairs, answers):
        results[job.id].append((device, answer))
    return results


# ---------------------------------------------------------------- 3. record


def classify(answer: apns.ApnsResponse) -> str:
    """One device's answer, as what it means for the row."""
    if answer.status == 200:
        return "sent"
    if answer.status == 410:
        return "unregistered"
    if answer.status == 400 and answer.reason in GONE_REASONS:
        return "bad_token"
    if answer.status == -1 or answer.status == 403 or answer.reason in CONFIG_REASONS:
        return "config"
    if answer.status == 429:
        return "token_throttle" if answer.reason == "TooManyProviderTokenUpdates" else "throttle"
    if answer.status == 0 or answer.status >= 500:
        return "transient"
    return "permanent"


def record(
    jobs: list[Job], results: dict[str, list[tuple[Device, apns.ApnsResponse]]], now: datetime
) -> DeliveryResult:
    outcome = DeliveryResult(claimed=len(jobs))
    config_errors: list[str] = []
    with db_session() as session:
        for job in jobs:
            answers = results.get(job.id, [])
            kinds = []
            for device, answer in answers:
                kind = classify(answer)
                kinds.append((kind, answer))
                if kind == "unregistered":
                    seen_before = clock.from_epoch_ms(answer.timestamp_ms) if answer.timestamp_ms else None
                    if push_queries.forget_device(session, device.id, seen_before=seen_before):
                        outcome.devices_removed += 1
                    logger.warning("Push %s: device %s is no longer registered (410)", job.id, device.id)
                elif kind == "bad_token":
                    if push_queries.forget_device(session, device.id):
                        outcome.devices_removed += 1
                    logger.warning("Push %s: device %s refused (%s)", job.id, device.id, answer.reason)
                elif kind == "config":
                    config_errors.append(answer.reason or str(answer.status))
                elif kind != "sent":
                    logger.warning("Push %s: device %s answered %s %s", job.id, device.id, answer.status, answer.reason)
            _record_row(session, job, kinds, now, outcome)
        session.commit()
    if config_errors:
        # Once per batch: a bad key or topic fails every request the same way.
        logger.error(
            "APNs refused our credentials or topic (%s) for %d request(s); pushes stay queued",
            ", ".join(sorted(set(config_errors))),
            len(config_errors),
        )
    return outcome


def _record_row(session: Session, job: Job, kinds: list[tuple[str, apns.ApnsResponse]], now: datetime, outcome) -> None:
    found = {kind for kind, _ in kinds}
    reason = next((answer.reason for kind, answer in kinds if kind != "sent" and answer.reason), None)
    if "sent" in found:
        push_queries.mark(session, job.id, status=PushStatus.SENT, sent_at=now, last_error=None)
        outcome.sent += 1
        return
    # Not the message's fault: the attempt the claim counted is given back.
    for kind, delay in (
        ("config", LEASE),
        ("token_throttle", timedelta(minutes=5)),
        ("throttle", timedelta(minutes=1)),
    ):
        if kind in found:
            push_queries.mark(
                session, job.id, not_before=now + delay, attempts=max(job.attempts - 1, 0), last_error=reason
            )
            outcome.retried += 1
            return
    if "transient" in found:
        if job.attempts >= MAX_ATTEMPTS:
            push_queries.mark(session, job.id, status=PushStatus.FAILED, last_error=reason)
            outcome.failed += 1
            logger.warning("Push %s failed after %d attempts (%s)", job.id, job.attempts, reason)
        else:
            delay = timedelta(minutes=2**job.attempts)
            push_queries.mark(session, job.id, not_before=now + delay, last_error=reason)
            outcome.retried += 1
        return
    if "permanent" in found:
        push_queries.mark(session, job.id, status=PushStatus.FAILED, last_error=reason)
        outcome.failed += 1
        logger.warning("Push %s failed: %s", job.id, reason)
        return
    # Every phone the member had is gone.
    push_queries.mark(session, job.id, status=PushStatus.SKIPPED, last_error="no_device")
    outcome.skipped += 1


# ---------------------------------------------------------------- entry points


async def deliver_pending(batch_size: int = BATCH_SIZE) -> DeliveryResult:
    """Send everything due, a batch at a time. Does nothing while push is off."""
    total = DeliveryResult()
    if not config.push_enabled:
        return total
    for _ in range(MAX_BATCHES):
        jobs, skipped = await run_in_threadpool(claim, clock.now(), batch_size)
        total.skipped += skipped
        if jobs:
            results = await send(jobs)
            batch = await run_in_threadpool(record, jobs, results, clock.now())
            for name in ("claimed", "sent", "skipped", "failed", "retried", "devices_removed"):
                setattr(total, name, getattr(total, name) + getattr(batch, name))
        if len(jobs) + skipped < batch_size:
            break
    if total.claimed or total.skipped:
        logger.info("Push delivery: %s", total.counts())
    return total


async def kick() -> None:
    """``BackgroundTasks`` entry: deliver what a route just queued, without failing the response."""
    try:
        await deliver_pending()
    except Exception:
        logger.exception("Push delivery failed")
