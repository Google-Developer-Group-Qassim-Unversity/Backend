import logging
import re
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func, or_, asc, desc, case, delete, null, update
from app.DB.schema import EmailLogs, MemberProfiles, Members, MembersLogs, Submissions
from app.exceptions import MemberAlreadyExists, MemberNotFound
from app.routers.models import Member_model
from datetime import datetime

logger = logging.getLogger(__name__)


def create_member(session: Session, member: Member_model, is_authenticated: bool = False):
    try:
        with session.begin_nested():
            new_member = Members(
                name=member.name,
                email=member.email,
                phone_number=member.phone_number,
                uni_id=member.uni_id,
                clerk_user_id=member.clerk_user_id,
                gender=member.gender,
                uni_level=member.uni_level,
                uni_college=member.uni_college,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                is_authenticated=is_authenticated,
            )
            session.add(new_member)
            session.flush()
        return new_member
    except IntegrityError as e:
        logger.warning("IntegrityError in create_member: %s", e)
        return None


def create_member_if_not_exists(
    session: Session, member: Member_model, is_authenticated: bool = False
) -> tuple[Members | None, bool]:
    existing_member = None
    if member.clerk_user_id is not None:
        existing_member = session.scalar(select(Members).where(Members.clerk_user_id == member.clerk_user_id))
    if not existing_member and member.uni_id is not None:
        existing_member = session.scalar(select(Members).where(Members.uni_id == member.uni_id))
    if not existing_member and member.email is not None:
        # Clerk verifies the signup email (and doesn't let members change it), so an
        # unclaimed admin-created row with a matching email is safe to fold in
        # automatically - no "is this you?" confirmation needed.
        existing_member = get_unclaimed_member_by_email_or_none(session, member.email)
    if existing_member:
        member.id = existing_member.id
        already_exist = True
        updated_member = update_member(session, member, is_authenticated)
        return updated_member, already_exist
    refuse_second_record(session, member.phone_number, member.clerk_user_id)
    already_exist = False
    return create_member(session, member, is_authenticated), already_exist


def refuse_second_record(session: Session, phone_number: str | None, clerk_user_id: str | None) -> None:
    """Stop a sign-up from creating a second record for someone who already has one.

    Runs only after every proven match (Clerk id, university ID, email) has
    missed, right before a new row would be inserted. A member who signs up
    again with a different account - Google instead of their university ID,
    say - shares nothing proven with their first record, but they type the same
    phone number. On 2026-10-20 every pair of live records sharing a phone
    number on production (46 of them) was one person twice.

    The phone number is typed, not proven, so it never links the sign-in to the
    record: that would let anyone take over a record by typing its number. It
    only refuses, and the person signs in with their first account or has an
    admin move the record.
    """
    existing = get_member_by_phone_or_none(session, phone_number)
    if existing is not None:
        logger.warning(
            "Sign-up %s has the phone number of member %s (signed in as %s); refusing a second record",
            clerk_user_id,
            existing.id,
            existing.clerk_user_id,
        )
        raise MemberAlreadyExists(existing.id)


def get_members(session: Session):
    last_activity_subq = (
        select(MembersLogs.member_id, func.max(MembersLogs.date).label("last_activity"))
        .group_by(MembersLogs.member_id)
        .subquery()
    )
    statement = (
        select(Members, last_activity_subq.c.last_activity)
        .outerjoin(last_activity_subq, last_activity_subq.c.member_id == Members.id)
        .where(Members.deleted_at.is_(None))
    )
    members = []
    for member, last_activity in session.execute(statement).all():
        member.last_activity = last_activity
        members.append(member)
    return members


# Columns the members table is allowed to sort by, mapped to their ORM column.
# Anything else falls back to name so a bad query param can never 500.
_MEMBER_SORT_COLUMNS = {
    "name": Members.name,
    "uni_id": Members.uni_id,
    "email": Members.email,
    "created_at": Members.created_at,
    "is_authenticated": Members.is_authenticated,
}


def _member_search_filter(search: str | None):
    """A case-insensitive LIKE across the columns the UI search box covers."""
    if not search or not search.strip():
        return None
    like = f"%{search.strip()}%"
    return or_(
        Members.name.like(like), Members.email.like(like), Members.uni_id.like(like), Members.phone_number.like(like)
    )


def count_members(session: Session, search: str | None = None) -> int:
    stmt = select(func.count()).select_from(Members).where(Members.deleted_at.is_(None))
    search_filter = _member_search_filter(search)
    if search_filter is not None:
        stmt = stmt.where(search_filter)
    return session.scalar(stmt) or 0


def get_members_paginated(
    session: Session, limit: int, offset: int, search: str | None = None, sort_by: str = "name", order: str = "asc"
):
    """One page of members, carrying last_activity like get_members(), filtered
    and ordered in the database so the client never loads the whole table."""
    last_activity_subq = (
        select(MembersLogs.member_id, func.max(MembersLogs.date).label("last_activity"))
        .group_by(MembersLogs.member_id)
        .subquery()
    )
    statement = (
        select(Members, last_activity_subq.c.last_activity)
        .outerjoin(last_activity_subq, last_activity_subq.c.member_id == Members.id)
        .where(Members.deleted_at.is_(None))
    )
    search_filter = _member_search_filter(search)
    if search_filter is not None:
        statement = statement.where(search_filter)

    if sort_by == "last_activity":
        sort_column = last_activity_subq.c.last_activity
    else:
        sort_column = _MEMBER_SORT_COLUMNS.get(sort_by, Members.name)
    direction = desc if order == "desc" else asc
    # id is the tiebreaker so the page boundary is always deterministic.
    statement = statement.order_by(direction(sort_column), asc(Members.id)).offset(offset).limit(limit)

    members = []
    for member, last_activity in session.execute(statement).all():
        member.last_activity = last_activity
        members.append(member)
    return members


def get_member_by_id(session: Session, member_id: int):
    """A member by id. A deleted account is not found: nothing should act on it any more."""
    statement = select(Members).where(Members.id == member_id, Members.deleted_at.is_(None))
    member = session.scalars(statement).first()
    if not member:
        raise MemberNotFound(member_id)
    return member


def get_members_by_id(session: Session, member_ids: list[int]):
    statement = select(Members).where(Members.id.in_(member_ids))
    return session.scalars(statement).all()


def get_member_by_uni_id(session: Session, uni_id: str):
    member = get_member_by_uni_id_or_none(session, uni_id)
    if not member:
        raise MemberNotFound(uni_id)
    return member


def get_member_by_uni_id_or_none(session: Session, uni_id: str) -> Members | None:
    statement = select(Members).where(Members.uni_id == uni_id)
    return session.scalars(statement).first()


def get_member_by_email_or_none(session: Session, email: str) -> Members | None:
    statement = select(Members).where(func.lower(Members.email) == email.strip().lower())
    return session.scalars(statement).first()


def phone_key(phone_number: str | None) -> str | None:
    """A Saudi mobile number's last nine digits, so 05XXXXXXXX, 5XXXXXXXX and +9665XXXXXXXX compare equal."""
    digits = re.sub(r"\D", "", phone_number or "")
    return digits[-9:] if len(digits) >= 9 else None


def get_member_by_phone_or_none(session: Session, phone_number: str | None) -> Members | None:
    """A live member row with this phone number, however either side is written."""
    key = phone_key(phone_number)
    if key is None:
        return None
    digits_only = Members.phone_number
    for separator in (" ", "-", "+", "(", ")"):
        digits_only = func.replace(digits_only, separator, "")
    statement = select(Members).where(digits_only.endswith(key), Members.deleted_at.is_(None))
    return next((m for m in session.scalars(statement) if phone_key(m.phone_number) == key), None)


def get_unclaimed_member_by_email_or_none(session: Session, email: str) -> Members | None:
    """Find an admin-created member (no Clerk identity attached yet) by email.

    Used by ``create_member_if_not_exists`` to automatically fold an admin-created
    shadow row into a new signup's Clerk identity when the email matches."""
    statement = select(Members).where(
        func.lower(Members.email) == email.strip().lower(),
        Members.clerk_user_id.is_(None),
        Members.is_authenticated == 0,
    )
    return session.scalars(statement).first()


def get_unlinked_member_by_email_or_none(session: Session, email: str) -> Members | None:
    """A member row that no Clerk sign-in uses yet, by email (case-insensitive)."""
    statement = select(Members).where(
        func.lower(Members.email) == email.strip().lower(),
        Members.clerk_user_id.is_(None),
        Members.deleted_at.is_(None),
    )
    return session.scalars(statement).first()


def get_member_by_clerk_user_id(session: Session, clerk_user_id: str) -> Members:
    member = get_member_by_clerk_user_id_or_none(session, clerk_user_id)
    if not member:
        raise MemberNotFound(clerk_user_id)
    return member


def get_member_by_clerk_user_id_or_none(session: Session, clerk_user_id: str) -> Members | None:
    statement = select(Members).where(Members.clerk_user_id == clerk_user_id)
    return session.scalars(statement).first()


def lock_member(session: Session, member_id: int) -> Members | None:
    """A member row read as it is now and locked until the transaction ends."""
    statement = (
        select(Members).where(Members.id == member_id).with_for_update().execution_options(populate_existing=True)
    )
    return session.scalars(statement).first()


def lock_member_by_clerk_user_id_or_none(session: Session, clerk_user_id: str) -> Members | None:
    """Like ``lock_member``, by the Clerk id."""
    statement = (
        select(Members)
        .where(Members.clerk_user_id == clerk_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return session.scalars(statement).first()


def update_member(session: Session, member: Member_model, is_authenticated: bool):
    if member.id is None:
        # update_member always identifies its target by id; the one caller
        # (create_member_if_not_exists) sets it right before calling this.
        raise MemberNotFound("unknown")
    existing_member = session.scalar(select(Members).where(Members.id == member.id))
    if not existing_member:
        raise MemberNotFound(member.id)
    existing_member.name = member.name
    existing_member.email = member.email
    existing_member.phone_number = member.phone_number
    existing_member.gender = member.gender
    existing_member.uni_level = member.uni_level
    existing_member.uni_college = member.uni_college
    if member.uni_id is not None:
        existing_member.uni_id = member.uni_id
    if member.clerk_user_id is not None:
        existing_member.clerk_user_id = member.clerk_user_id
    existing_member.updated_at = datetime.now()
    existing_member.is_authenticated = is_authenticated
    session.flush()
    logger.info("Updated member %s", existing_member.id)
    return existing_member


def set_member_clerk_user_id(session: Session, member: Members, clerk_user_id: str) -> Members:
    member.clerk_user_id = clerk_user_id
    session.flush()
    return member


def update_member_by_uni_id(session: Session, uni_id: str, updates: dict) -> Members | None:
    member = session.scalar(select(Members).where(Members.uni_id == uni_id))
    if not member:
        raise MemberNotFound(uni_id)
    return _apply_member_updates(session, member, updates)


def update_member_by_id(session: Session, member_id: int, updates: dict) -> Members | None:
    member = session.scalar(select(Members).where(Members.id == member_id))
    if not member:
        raise MemberNotFound(member_id)
    return _apply_member_updates(session, member, updates)


def _apply_member_updates(session: Session, member: Members, updates: dict) -> Members:
    for key, value in updates.items():
        if value is not None and hasattr(member, key):
            setattr(member, key, value)

    member.updated_at = datetime.now()
    member.is_authenticated = True
    session.flush()
    return member


def get_blast_eligible_count(session: Session) -> int:
    stmt = select(func.count()).select_from(Members).where(Members.email.isnot(None), Members.email != "")
    return int(session.scalar(stmt) or 0)


def get_blast_recipients_by_activity(session: Session, limit: int, exclude_ids: list[int]):
    stmt = (
        select(Members)
        .outerjoin(MembersLogs, MembersLogs.member_id == Members.id)
        .where(Members.email.isnot(None), Members.email != "")
        .group_by(Members.id)
        .order_by(func.max(MembersLogs.date).desc())
        .limit(limit)
    )
    if exclude_ids:
        stmt = stmt.where(Members.id.notin_(exclude_ids))
    return session.scalars(stmt).all()


def get_blast_recipients_alphabetical(session: Session, limit: int, exclude_ids: list[int]):
    stmt = (
        select(Members).where(Members.email.isnot(None), Members.email != "").order_by(Members.name.asc()).limit(limit)
    )
    if exclude_ids:
        stmt = stmt.where(Members.id.notin_(exclude_ids))
    return session.scalars(stmt).all()


def get_member_stats(session: Session) -> dict:
    """Whole-table counts for the members page cards, in one grouped query so
    the page never has to load every row to show its totals."""
    row = session.execute(
        select(
            func.count().label("total"),
            func.coalesce(func.sum(case((Members.is_authenticated == 1, 1), else_=0)), 0).label("authenticated"),
            func.coalesce(func.sum(case((Members.gender == "Male", 1), else_=0)), 0).label("male"),
            func.coalesce(func.sum(case((Members.gender == "Female", 1), else_=0)), 0).label("female"),
        ).where(Members.deleted_at.is_(None))
    ).one()
    total = int(row.total)
    authenticated = int(row.authenticated)
    return {
        "total": total,
        "authenticated": authenticated,
        "manual": total - authenticated,
        "male": int(row.male),
        "female": int(row.female),
    }


DELETED_MEMBER_NAME = "Deleted member"


def anonymize_member(session: Session, member: Members) -> None:
    """Strip a member's personal data and mark the account deleted."""
    now = datetime.now()
    session.execute(delete(MemberProfiles).where(MemberProfiles.member_id == member.id))
    session.execute(delete(Submissions).where(Submissions.member_id == member.id))
    session.execute(update(EmailLogs).where(EmailLogs.member_id == member.id).values(member_id=None, data=null()))
    session.execute(
        update(Members)
        .where(Members.id == member.id)
        .values(
            name=DELETED_MEMBER_NAME,
            public_name=DELETED_MEMBER_NAME,
            email=None,
            phone_number=None,
            uni_id=None,
            clerk_user_id=None,
            uni_level=None,
            uni_college=None,
            updated_at=now,
            deleted_at=now,
        )
    )
    session.flush()
    logger.info("Anonymized member %s", member.id)
