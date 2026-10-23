"""Writing push notifications to the outbox (``push_notifications``).

A trigger calls ``enqueue`` in the same transaction as the change that causes
the push, so the row commits or rolls back with it. Rows go only to members a
push can reach: the notification's switch on, and at least one phone in the
APNs environment this server sends to. Members without the app get nothing
new; email stays their channel. With push off (no APNs key), nothing is
written at all.

Every row's ``dedupe_key`` is unique: a trigger that runs twice - four
workers, a retried request, the sweep - adds nothing the second time.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import config
from app.DB import push as push_queries
from app.DB.ids import new_id
from app.DB.schema import PushEnvironment, PushKind, PushStatus
from app.services.push import clock
from app.services.push.messages import Text

logger = logging.getLogger(__name__)

# Which switch each kind answers to. A broadcast says its own: news for everyone, remind for one event's registrants.
SWITCH_FOR: dict[PushKind, str] = {
    PushKind.REMINDER: "remind",
    PushKind.ACCEPTED: "accept",
    PushKind.POINTS: "points",
    PushKind.NEW_EVENT: "news",
    PushKind.EVENT_UPDATED: "remind",
    PushKind.EVENT_CANCELLED: "remind",
    PushKind.EVENT_LIVE: "remind",
    PushKind.REGISTRATION_CLOSING: "news",
    PushKind.CERTIFICATE: "points",
    PushKind.EVENT_CONTENT: "remind",
    PushKind.WEEKLY_SUMMARY: "points",
    PushKind.WELCOME: "news",
}


def device_environment() -> PushEnvironment:
    """The phones this server can reach: the APNs key is production-only, so dev builds' tokens never are."""
    return PushEnvironment.DEVELOPMENT if config.APNS_ENVIRONMENT == "sandbox" else PushEnvironment.PRODUCTION


def reachable(session: Session, switch: str, member_ids: Iterable[int] | None = None) -> set[int]:
    return push_queries.reachable_member_ids(session, switch, device_environment(), member_ids)


@dataclass(frozen=True)
class Push:
    """One notification for one member, before it becomes a row."""

    member_id: int
    dedupe_key: str
    cause: dict | None = None


def enqueue(
    session: Session,
    kind: PushKind,
    pushes: Iterable[Push],
    *,
    text: Text | Callable[[Push], Text],
    url: str,
    thread_id: str,
    expires_at: datetime,
    switch: str | None = None,
    event_id: int | None = None,
    broadcast_id: str | None = None,
    quiet_hours: bool = True,
    now: datetime | None = None,
    reachable_ids: set[int] | None = None,
    delay: timedelta = timedelta(0),
) -> int:
    """Write the reachable ones; returns how many rows were added (keys already there add nothing)."""
    if not config.push_enabled:
        return 0
    pushes = list(pushes)
    if not pushes:
        return 0
    now = now or clock.now()
    switch = switch or SWITCH_FOR[kind]
    if reachable_ids is None:
        reachable_ids = reachable(session, switch, {push.member_id for push in pushes})
    wanted = {push.dedupe_key: push for push in pushes if push.member_id in reachable_ids}
    existing = push_queries.existing_dedupe_keys(session, wanted)
    fresh = [push for key, push in wanted.items() if key not in existing]
    if not fresh:
        return 0
    # ``delay`` holds the row back first (a debounce); quiet hours then apply to that moment.
    not_before = clock.after_quiet_hours(now + delay) if quiet_hours else now + delay
    rows = []
    for push in fresh:
        payload: dict[str, Any] = dict(text(push) if callable(text) else text)
        payload.update({"url": url, "thread_id": thread_id, "switch": switch, "cause": push.cause or {}})
        rows.append(
            {
                "id": new_id(),
                "member_id": push.member_id,
                "kind": kind.value,
                "dedupe_key": push.dedupe_key,
                "event_id": event_id,
                "broadcast_id": broadcast_id,
                "payload": payload,
                "status": PushStatus.PENDING.value,
                "attempts": 0,
                "not_before": not_before,
                "expires_at": expires_at,
                "created_at": now,
            }
        )
    push_queries.insert_notifications(session, rows)
    logger.info("Queued %d %s push(es)%s", len(rows), kind.value, f" for event {event_id}" if event_id else "")
    return len(rows)
