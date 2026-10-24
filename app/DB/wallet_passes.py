"""Queries for the Apple Wallet pass web service: which devices hold whose pass, and the welcome.

Times are naive UTC, like the DATETIME columns store them (``app/DB/main.py``).
See app/services/wallet_updates.py for the rules and docs/WALLET.md.
"""

import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.DB.ids import new_id
from app.DB.schema import MemberProfiles, WalletPassRegistrations

# A member's pass on more devices than this (phones, a watch, an iPad, old ones never
# unregistered): registering one more drops the one registered longest ago.
MAX_REGISTRATIONS_PER_MEMBER = 10


def profiles_for_serial_prefix(session: Session, prefix: str) -> list[MemberProfiles]:
    """Profiles whose uuid starts with ``prefix``, in either case: usually one, two when serials collide.

    ``prefix`` must already be checked to hold no ``LIKE`` wildcards (the caller matches it against a pattern).
    """
    return list(
        session.scalars(
            select(MemberProfiles).where(
                or_(MemberProfiles.uuid.like(f"{prefix.lower()}%"), MemberProfiles.uuid.like(f"{prefix.upper()}%"))
            )
        ).all()
    )


def mark_welcomed(session: Session, profile_id: int, now: datetime.datetime) -> bool:
    """Record the member's welcome and bump their pass's update tag, unless they were welcomed before.

    True only for the call that set it: the ``IS NULL`` condition is checked
    under the row's lock, so two devices registering at once welcome once.
    ``updated_at`` is assigned its own value so MySQL's ``ON UPDATE`` leaves it
    alone: it means when the member last edited their card.
    """
    result = session.execute(
        update(MemberProfiles)
        .where(MemberProfiles.id == profile_id, MemberProfiles.pass_welcomed_at.is_(None))
        .values(pass_welcomed_at=now, pass_updated_at=now, updated_at=MemberProfiles.updated_at)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    return bool(getattr(result, "rowcount", 0))


def register(
    session: Session,
    member_id: int,
    *,
    device_library_id: str,
    serial_number: str,
    push_token: str,
    now: datetime.datetime,
) -> tuple[WalletPassRegistrations, bool]:
    """Insert the device's registration of this pass, or refresh its push token. True when it is new.

    A row for the same device and serial that belonged to another member (two
    members' serials collide, and the device swapped one card for the other)
    moves to this member and counts as new; a push still owed to the other
    member's card is dropped.
    """
    existing = session.scalar(
        select(WalletPassRegistrations).where(
            WalletPassRegistrations.device_library_id == device_library_id,
            WalletPassRegistrations.serial_number == serial_number,
        )
    )
    created = existing is None or existing.member_id != member_id
    values: dict = {"member_id": member_id, "push_token": push_token, "updated_at": now}
    refresh = dict(values)
    if existing is not None and existing.member_id != member_id:
        refresh |= {"push_due_at": None, "push_attempts": 0}
    statement = insert(WalletPassRegistrations).values(
        id=new_id(), device_library_id=device_library_id, serial_number=serial_number, created_at=now, **values
    )
    session.execute(statement.on_duplicate_key_update(**refresh))
    session.flush()
    registration = session.scalars(
        select(WalletPassRegistrations)
        .where(
            WalletPassRegistrations.device_library_id == device_library_id,
            WalletPassRegistrations.serial_number == serial_number,
        )
        .execution_options(populate_existing=True)
    ).one()
    _drop_extra_registrations(session, member_id)
    return registration, created


def _drop_extra_registrations(session: Session, member_id: int) -> None:
    ids = session.scalars(
        select(WalletPassRegistrations.id)
        .where(WalletPassRegistrations.member_id == member_id)
        .order_by(WalletPassRegistrations.updated_at.desc(), WalletPassRegistrations.id.desc())
    ).all()
    if len(ids) > MAX_REGISTRATIONS_PER_MEMBER:
        session.execute(
            delete(WalletPassRegistrations).where(WalletPassRegistrations.id.in_(ids[MAX_REGISTRATIONS_PER_MEMBER:]))
        )
        session.flush()


def unregister(session: Session, member_id: int, *, device_library_id: str, serial_number: str) -> bool:
    """Only the caller's own pass on that device. False when there was no such registration."""
    result = session.execute(
        delete(WalletPassRegistrations).where(
            WalletPassRegistrations.device_library_id == device_library_id,
            WalletPassRegistrations.serial_number == serial_number,
            WalletPassRegistrations.member_id == member_id,
        )
    )
    session.flush()
    return bool(getattr(result, "rowcount", 0))


def device_passes(session: Session, device_library_id: str) -> list[tuple[str, datetime.datetime | None]]:
    """Every pass the device registered, as (serial number, the pass's update tag)."""
    rows = session.execute(
        select(WalletPassRegistrations.serial_number, MemberProfiles.pass_updated_at)
        .join(MemberProfiles, MemberProfiles.member_id == WalletPassRegistrations.member_id)
        .where(WalletPassRegistrations.device_library_id == device_library_id)
        .order_by(WalletPassRegistrations.serial_number)
    ).all()
    return [(serial, updated_at) for serial, updated_at in rows]


def owe_push(session: Session, registration_id: str, now: datetime.datetime) -> None:
    """A pass update push is owed to this device from ``now`` (app/services/wallet_push.py sends it)."""
    session.execute(
        update(WalletPassRegistrations)
        .where(WalletPassRegistrations.id == registration_id)
        .values(push_due_at=now, push_attempts=0)
        .execution_options(synchronize_session=False)
    )
    session.flush()


def lock_due_pushes(session: Session, now: datetime.datetime, limit: int) -> list[WalletPassRegistrations]:
    """Registrations owed a push, that no other transaction holds. Locked until the caller's transaction ends."""
    return list(
        session.scalars(
            select(WalletPassRegistrations)
            .where(WalletPassRegistrations.push_due_at.is_not(None), WalletPassRegistrations.push_due_at <= now)
            .order_by(WalletPassRegistrations.push_due_at, WalletPassRegistrations.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )


def set_push_due(session: Session, registration_id: str, due_at: datetime.datetime | None, *, attempts: int) -> None:
    """Retry at ``due_at``, or nothing owed any more (``None``)."""
    session.execute(
        update(WalletPassRegistrations)
        .where(WalletPassRegistrations.id == registration_id)
        .values(push_due_at=due_at, push_attempts=attempts)
        .execution_options(synchronize_session=False)
    )


def forget(
    session: Session, registration_id: str, *, push_token: str, seen_before: datetime.datetime | None = None
) -> bool:
    """Delete a registration whose push token APNs refused, unless the device registered again since.

    ``push_token`` must still be the refused one; with ``seen_before``, the
    registration must also be older than the moment APNs says the token died.
    """
    statement = delete(WalletPassRegistrations).where(
        WalletPassRegistrations.id == registration_id, WalletPassRegistrations.push_token == push_token
    )
    if seen_before is not None:
        statement = statement.where(WalletPassRegistrations.updated_at < seen_before)
    result = session.execute(statement)
    return bool(getattr(result, "rowcount", 0))


def delete_member_registrations(session: Session, member_id: int) -> None:
    """Account deletion: every device registration of the member's pass."""
    session.execute(delete(WalletPassRegistrations).where(WalletPassRegistrations.member_id == member_id))
    session.flush()
