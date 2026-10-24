"""Queries for the member app's push notifications: devices, switches, the outbox and the feed.

Times are naive UTC, like the DATETIME columns store them (``app/DB/main.py``).
"""

import datetime
from collections.abc import Iterable

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.DB.ids import new_id
from app.DB.schema import (
    MemberDevices,
    MemberNotificationPrefs,
    MemberRankStandings,
    PushEnvironment,
    PushLocale,
    PushNotifications,
    PushPlatform,
    PushStatus,
)

# The app's Settings board: every switch on until the member turns it off (club news too, since 1.0.3).
DEFAULT_PREFS: dict[str, bool] = {"remind": True, "accept": True, "points": True, "news": True}
SWITCHES = tuple(DEFAULT_PREFS)

# A member's phones; registering a sixth drops the one seen longest ago.
MAX_DEVICES_PER_MEMBER = 5


# ---------------------------------------------------------------- devices


def register_device(
    session: Session,
    member_id: int,
    *,
    token: str,
    platform: PushPlatform,
    environment: PushEnvironment,
    app_version: str,
    app_build: str,
    locale: PushLocale,
    now: datetime.datetime,
) -> MemberDevices:
    """Insert the token, or move it to this member and refresh it. One phone belongs to one member."""
    values = {
        "member_id": member_id,
        "platform": platform.value,
        "environment": environment.value,
        "app_version": app_version,
        "app_build": app_build,
        "locale": locale.value,
        "last_seen_at": now,
    }
    statement = insert(MemberDevices).values(id=new_id(), token=token, created_at=now, **values)
    session.execute(statement.on_duplicate_key_update(**values))
    session.flush()
    device = session.scalars(
        select(MemberDevices).where(MemberDevices.token == token).execution_options(populate_existing=True)
    ).one()
    _drop_extra_devices(session, member_id)
    return device


def _drop_extra_devices(session: Session, member_id: int) -> None:
    ids = session.scalars(
        select(MemberDevices.id)
        .where(MemberDevices.member_id == member_id)
        .order_by(MemberDevices.last_seen_at.desc(), MemberDevices.id.desc())
    ).all()
    if len(ids) > MAX_DEVICES_PER_MEMBER:
        session.execute(delete(MemberDevices).where(MemberDevices.id.in_(ids[MAX_DEVICES_PER_MEMBER:])))
        session.flush()


def delete_device(session: Session, member_id: int, device_id: str) -> bool:
    """Only the caller's own row. False when there is none (already gone, or someone else's)."""
    result = session.execute(
        delete(MemberDevices).where(MemberDevices.id == device_id, MemberDevices.member_id == member_id)
    )
    session.flush()
    return bool(getattr(result, "rowcount", 0))


def devices_for(
    session: Session, member_ids: Iterable[int], environment: PushEnvironment
) -> dict[int, list[MemberDevices]]:
    ids = set(member_ids)
    if not ids:
        return {}
    found: dict[int, list[MemberDevices]] = {}
    rows = session.scalars(
        select(MemberDevices)
        .where(MemberDevices.member_id.in_(ids), MemberDevices.environment == environment)
        .order_by(MemberDevices.last_seen_at.desc())
    ).all()
    for device in rows:
        found.setdefault(device.member_id, []).append(device)
    return found


def forget_device(session: Session, device_id: str, *, seen_before: datetime.datetime | None = None) -> bool:
    """Delete a token APNs refused. With ``seen_before``, only if it was not registered again after that moment."""
    statement = delete(MemberDevices).where(MemberDevices.id == device_id)
    if seen_before is not None:
        statement = statement.where(MemberDevices.last_seen_at < seen_before)
    result = session.execute(statement)
    return bool(getattr(result, "rowcount", 0))


# ---------------------------------------------------------------- switches


def get_prefs(session: Session, member_id: int) -> MemberNotificationPrefs | None:
    return session.scalar(select(MemberNotificationPrefs).where(MemberNotificationPrefs.member_id == member_id))


def prefs_as_dict(prefs: MemberNotificationPrefs | None) -> dict[str, bool]:
    if prefs is None:
        return dict(DEFAULT_PREFS)
    return {switch: bool(getattr(prefs, switch)) for switch in SWITCHES}


def save_prefs(
    session: Session, member_id: int, changes: dict[str, bool], now: datetime.datetime
) -> MemberNotificationPrefs:
    """Apply ``changes`` on top of the member's switches (the defaults when they never saved any)."""
    prefs = get_prefs(session, member_id)
    if prefs is None:
        values = {**DEFAULT_PREFS, **changes}
        statement = insert(MemberNotificationPrefs).values(
            id=new_id(), member_id=member_id, updated_at=now, **{k: int(v) for k, v in values.items()}
        )
        # Two first saves at once: the second becomes an update of the first's row.
        session.execute(statement.on_duplicate_key_update(updated_at=now, **{k: int(v) for k, v in changes.items()}))
        session.flush()
        prefs = session.scalars(
            select(MemberNotificationPrefs)
            .where(MemberNotificationPrefs.member_id == member_id)
            .execution_options(populate_existing=True)
        ).one()
        return prefs
    for switch, value in changes.items():
        setattr(prefs, switch, int(value))
    prefs.updated_at = now
    session.flush()
    return prefs


def reachable_member_ids(
    session: Session, switch: str, environment: PushEnvironment, member_ids: Iterable[int] | None = None
) -> set[int]:
    """Members with ``switch`` on and at least one phone in ``environment``: the ones a push can reach."""
    if switch not in DEFAULT_PREFS:
        raise ValueError(f"unknown notification switch {switch!r}")
    column = getattr(MemberNotificationPrefs, switch)
    statement = (
        select(MemberDevices.member_id)
        .distinct()
        .outerjoin(MemberNotificationPrefs, MemberNotificationPrefs.member_id == MemberDevices.member_id)
        .where(MemberDevices.environment == environment, func.coalesce(column, int(DEFAULT_PREFS[switch])) == 1)
    )
    if member_ids is not None:
        ids = set(member_ids)
        if not ids:
            return set()
        statement = statement.where(MemberDevices.member_id.in_(ids))
    return set(session.scalars(statement).all())


# ---------------------------------------------------------------- outbox


def existing_dedupe_keys(session: Session, keys: Iterable[str]) -> set[str]:
    keys = list(keys)
    if not keys:
        return set()
    found: set[str] = set()
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        found |= set(
            session.scalars(select(PushNotifications.dedupe_key).where(PushNotifications.dedupe_key.in_(chunk))).all()
        )
    return found


def insert_notifications(session: Session, rows: list[dict]) -> None:
    """Insert outbox rows; a row whose ``dedupe_key`` already exists is left as it is (a no-op, not an error)."""
    if not rows:
        return
    table = PushNotifications.__table__
    statement = insert(PushNotifications).on_duplicate_key_update(id=table.c.id)
    session.execute(statement, rows)
    session.flush()


def delete_member_push_data(session: Session, member_id: int) -> None:
    """Account deletion: the member's phones, switches, rank standings and every notification row, sent ones too."""
    session.execute(delete(PushNotifications).where(PushNotifications.member_id == member_id))
    session.execute(delete(MemberRankStandings).where(MemberRankStandings.member_id == member_id))
    session.execute(delete(MemberNotificationPrefs).where(MemberNotificationPrefs.member_id == member_id))
    session.execute(delete(MemberDevices).where(MemberDevices.member_id == member_id))
    session.flush()


def feed(session: Session, member_id: int, since: datetime.datetime, limit: int) -> list[PushNotifications]:
    """What was sent to the member since ``since``, newest first."""
    return list(
        session.scalars(
            select(PushNotifications)
            .where(
                PushNotifications.member_id == member_id,
                PushNotifications.status == PushStatus.SENT,
                PushNotifications.created_at >= since,
            )
            .order_by(PushNotifications.created_at.desc(), PushNotifications.id.desc())
            .limit(limit)
        ).all()
    )


def delete_old(session: Session, now: datetime.datetime, *, rows_after_days: int, devices_after_days: int) -> int:
    """The sweep's clean-up: notification rows past their keep time, and phones not seen for months."""
    rows = session.execute(
        delete(PushNotifications).where(PushNotifications.created_at < now - datetime.timedelta(days=rows_after_days))
    )
    devices = session.execute(
        delete(MemberDevices).where(MemberDevices.last_seen_at < now - datetime.timedelta(days=devices_after_days))
    )
    session.flush()
    return int(getattr(rows, "rowcount", 0) or 0) + int(getattr(devices, "rowcount", 0) or 0)


def mark(session: Session, notification_id: str, **values) -> None:
    session.execute(update(PushNotifications).where(PushNotifications.id == notification_id).values(**values))
