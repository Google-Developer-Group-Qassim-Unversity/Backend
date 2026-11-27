"""Database queries and mutations for first-class bonuses.

A ``Bonuses`` row is either standalone (``event_id IS NULL``) or attached to a
real event (``event_id`` set). Each ``BonusGrants`` child stores a signed
``points`` snapshot and optional bilingual labels, and its
``BonusGrantRecipients`` children link to either ``member_id`` or
``department_id`` (XOR) with a ``given_at`` timestamp preserved across edits.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import Bonuses, BonusGrantRecipients, BonusGrants, BonusRecipientType, Events, EventsStatus


def get_bonus_by_id(session: Session, bonus_id: str) -> Bonuses | None:
    stmt = (
        select(Bonuses)
        .options(selectinload(Bonuses.grants).selectinload(BonusGrants.recipients))
        .where(Bonuses.id == bonus_id)
        .execution_options(populate_existing=True)
    )
    return session.scalar(stmt)


def get_standalone_bonus_by_legacy_id(session: Session, event_id: int) -> Bonuses | None:
    return session.scalar(
        select(Bonuses)
        .where(Bonuses.legacy_id == event_id, Bonuses.event_id.is_(None))
        .options(selectinload(Bonuses.grants).selectinload(BonusGrants.recipients))
    )


def get_bonus_grant_by_id(session: Session, grant_id: str) -> BonusGrants | None:
    stmt = (
        select(BonusGrants)
        .options(selectinload(BonusGrants.bonus), selectinload(BonusGrants.recipients))
        .where(
            BonusGrants.legacy_id == int(grant_id)
            if grant_id.isascii() and grant_id.isdecimal()
            else BonusGrants.id == grant_id
        )
    )
    return session.scalar(stmt)


def get_bonus_by_event_id(session: Session, event_id: int) -> Bonuses | None:
    """Return the primary bonus container attached to a real event, if any."""
    stmt = (
        select(Bonuses)
        .options(selectinload(Bonuses.grants).selectinload(BonusGrants.recipients))
        .where(Bonuses.event_id == event_id)
        .order_by(Bonuses.created_at.asc(), Bonuses.id.asc())
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).first()


def get_bonuses_by_event_id(session: Session, event_id: int) -> list[Bonuses]:
    stmt = (
        select(Bonuses)
        .options(selectinload(Bonuses.grants).selectinload(BonusGrants.recipients))
        .where(Bonuses.event_id == event_id)
        .order_by(Bonuses.created_at.asc(), Bonuses.id.asc())
        .execution_options(populate_existing=True)
    )
    return list(session.scalars(stmt).all())


def list_bonuses(
    session: Session, *, semester_id: str | None = None, standalone_only: bool = False, event_id: int | None = None
) -> list[Bonuses]:
    stmt = select(Bonuses).options(selectinload(Bonuses.grants).selectinload(BonusGrants.recipients))
    if semester_id is not None:
        stmt = stmt.outerjoin(Events, Events.id == Bonuses.event_id).where(
            func.coalesce(Events.semester_id, Bonuses.semester_id) == semester_id
        )
    if standalone_only:
        stmt = stmt.where(Bonuses.event_id.is_(None))
    if event_id is not None:
        stmt = stmt.where(Bonuses.event_id == event_id)
    stmt = stmt.order_by(Bonuses.given_at.desc(), Bonuses.id.desc())
    return list(session.scalars(stmt).all())


def create_bonus(
    session: Session,
    *,
    title: str,
    semester_id: str,
    given_at: datetime,
    is_public: bool = True,
    event_id: int | None = None,
    created_by: int | None = None,
    legacy_end_datetime: datetime | None = None,
) -> Bonuses:
    bonus = Bonuses(
        title=title,
        event_id=event_id,
        semester_id=semester_id,
        given_at=given_at,
        is_public=1 if is_public else 0,
        created_by=created_by,
        legacy_end_datetime=legacy_end_datetime,
    )
    session.add(bonus)
    session.flush()
    return bonus


def create_bonus_grant(
    session: Session,
    *,
    bonus_id: str,
    recipient_type: BonusRecipientType | str,
    points: int,
    source_action_id: int | None = None,
    label_en: str | None = None,
    label_ar: str | None = None,
) -> BonusGrants:
    rtype = recipient_type if isinstance(recipient_type, BonusRecipientType) else BonusRecipientType(recipient_type)
    grant = BonusGrants(
        bonus_id=bonus_id,
        recipient_type=rtype,
        points=points,
        source_action_id=source_action_id,
        label_en=label_en,
        label_ar=label_ar,
    )
    session.add(grant)
    session.flush()
    return grant


def set_grant_recipients(
    session: Session, grant: BonusGrants, recipient_ids: Iterable[int], *, now: datetime | None = None
) -> list[int]:
    """Synchronize a grant's member or department recipients; returns newly added IDs.

    Recipients who stay on the grant keep their existing rows and ``given_at``
    timestamps so editing a grant never shifts old points into the current week
    for the weekly summary push.
    """
    wanted = list(dict.fromkeys(recipient_ids))
    existing_rows = list(
        session.scalars(select(BonusGrantRecipients).where(BonusGrantRecipients.bonus_grant_id == grant.id)).all()
    )
    is_member = grant.recipient_type == BonusRecipientType.MEMBER

    kept: set[int] = set()
    for row in existing_rows:
        rid = row.member_id if is_member else row.department_id
        if rid is not None and rid in wanted:
            kept.add(rid)
        else:
            session.delete(row)
    session.flush()

    added = [rid for rid in wanted if rid not in kept]
    stamp = now or datetime.now()
    for rid in added:
        session.add(
            BonusGrantRecipients(
                bonus_grant_id=grant.id,
                member_id=rid if is_member else None,
                department_id=None if is_member else rid,
                given_at=stamp,
            )
        )
    session.flush()
    return added


def delete_bonus(session: Session, bonus_id: str) -> bool:
    bonus = session.get(Bonuses, bonus_id)
    if bonus is None:
        return False
    session.delete(bonus)
    session.flush()
    return True


def delete_bonus_grant(session: Session, grant_id: str) -> bool:
    grant = session.get(BonusGrants, grant_id)
    if grant is None:
        return False
    session.delete(grant)
    session.flush()
    return True


def get_member_public_bonuses(session: Session, member_id: int) -> list[dict[str, Any]]:
    """Public standalone bonuses a member received across all semesters (for GET /events/me)."""
    stmt = (
        select(
            Bonuses.id.label("bonus_id"),
            Bonuses.legacy_id,
            Bonuses.legacy_end_datetime,
            Bonuses.created_at,
            Bonuses.title.label("title"),
            Bonuses.given_at.label("given_at"),
            Bonuses.semester_id.label("semester_id"),
            func.sum(BonusGrants.points).label("points"),
            func.group_concat(func.distinct(BonusGrants.label_en)).label("label_en"),
            func.group_concat(func.distinct(BonusGrants.label_ar)).label("label_ar"),
        )
        .select_from(BonusGrantRecipients)
        .join(BonusGrants, BonusGrants.id == BonusGrantRecipients.bonus_grant_id)
        .join(Bonuses, Bonuses.id == BonusGrants.bonus_id)
        .where(
            BonusGrantRecipients.member_id == member_id,
            Bonuses.event_id.is_(None),
            Bonuses.is_public == 1,
            Bonuses.legacy_status == EventsStatus.PUBLISHED,
        )
        .group_by(Bonuses.id, Bonuses.title, Bonuses.given_at, Bonuses.semester_id)
        .order_by(Bonuses.given_at.desc(), Bonuses.id.desc())
    )
    return [dict(row._mapping) for row in session.execute(stmt).all()]


def department_has_bonus_points(session: Session, semester_id: str, department_id: int) -> bool:
    """Whether the department earned bonus points in this semester."""
    stmt = (
        select(BonusGrantRecipients.id)
        .join(BonusGrants, BonusGrants.id == BonusGrantRecipients.bonus_grant_id)
        .join(Bonuses, Bonuses.id == BonusGrants.bonus_id)
        .outerjoin(Events, Events.id == Bonuses.event_id)
        .where(
            BonusGrantRecipients.department_id == department_id,
            func.coalesce(Events.semester_id, Bonuses.semester_id) == semester_id,
            func.coalesce(Events.status, Bonuses.legacy_status) == EventsStatus.PUBLISHED,
        )
        .limit(1)
    )
    return session.scalar(stmt) is not None


def serialize_grant(grant: BonusGrants) -> dict[str, Any]:
    member_ids = sorted(r.member_id for r in grant.recipients if r.member_id is not None)
    department_ids = sorted(r.department_id for r in grant.recipients if r.department_id is not None)
    return {
        "id": grant.id,
        "recipient_type": grant.recipient_type.value,
        "points": grant.points,
        "source_action_id": grant.source_action_id,
        "label_en": grant.label_en,
        "label_ar": grant.label_ar,
        "member_ids": member_ids,
        "department_ids": department_ids,
        "created_at": grant.created_at,
    }


def serialize_bonus_detail(bonus: Bonuses) -> dict[str, Any]:
    grants = [serialize_grant(g) for g in bonus.grants]
    return {
        "id": bonus.id,
        "title": bonus.title,
        "event_id": bonus.event_id,
        "semester_id": bonus.semester_id,
        "given_at": bonus.given_at,
        "is_public": bool(bonus.is_public),
        "created_by": bonus.created_by,
        "created_at": bonus.created_at,
        "updated_at": bonus.updated_at,
        "grants": grants,
    }


def serialize_bonus_summary(bonus: Bonuses) -> dict[str, Any]:
    member_ids: set[int] = set()
    department_ids: set[int] = set()
    for grant in bonus.grants:
        for r in grant.recipients:
            if r.member_id is not None:
                member_ids.add(r.member_id)
            if r.department_id is not None:
                department_ids.add(r.department_id)
    return {
        "id": bonus.id,
        "title": bonus.title,
        "event_id": bonus.event_id,
        "semester_id": bonus.semester_id,
        "given_at": bonus.given_at,
        "is_public": bool(bonus.is_public),
        "grant_count": len(bonus.grants),
        "member_count": len(member_ids),
        "department_count": len(department_ids),
        "created_at": bonus.created_at,
    }


def collect_grants(bonuses: Sequence[Bonuses]) -> list[BonusGrants]:
    out: list[BonusGrants] = []
    for bonus in bonuses:
        out.extend(bonus.grants)
    return out
