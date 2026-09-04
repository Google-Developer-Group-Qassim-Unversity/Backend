import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func
from app.DB.schema import Members, MembersLogs, Role, RoleType
from app.exceptions import DataIntegrityError, MemberNotFound
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
    already_exist = False
    return create_member(session, member, is_authenticated), already_exist


def get_members(session: Session):
    last_activity_subq = (
        select(MembersLogs.member_id, func.max(MembersLogs.date).label("last_activity"))
        .group_by(MembersLogs.member_id)
        .subquery()
    )
    statement = select(Members, last_activity_subq.c.last_activity).outerjoin(
        last_activity_subq, last_activity_subq.c.member_id == Members.id
    )
    members = []
    for member, last_activity in session.execute(statement).all():
        member.last_activity = last_activity
        members.append(member)
    return members


def get_member_by_id(session: Session, member_id: int):
    statement = select(Members).where(Members.id == member_id)
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


def get_member_by_clerk_user_id(session: Session, clerk_user_id: str) -> Members:
    member = get_member_by_clerk_user_id_or_none(session, clerk_user_id)
    if not member:
        raise MemberNotFound(clerk_user_id)
    return member


def get_member_by_clerk_user_id_or_none(session: Session, clerk_user_id: str) -> Members | None:
    statement = select(Members).where(Members.clerk_user_id == clerk_user_id)
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


def get_member_roles(session: Session):
    query = session.query(
        Members.id,
        Members.name,
        Members.email,
        Members.phone_number,
        Members.uni_id,
        Members.gender,
        Members.uni_level,
        Members.uni_college,
        Members.is_authenticated,
        Members.created_at,
        Members.updated_at,
        Role.role,
    ).join(Role, Members.id == Role.member_id)

    return [row._asdict() for row in query.all()]


def update_member_role(session: Session, member_id: int, new_role: RoleType):
    existing_member = session.scalar(select(Members).where(Members.id == member_id))
    if not existing_member:
        raise MemberNotFound(member_id)

    # Check if member already has a role
    existing_role = session.scalar(select(Role).where(Role.member_id == member_id))

    if existing_role:
        # Update existing role
        existing_role.role = new_role
    else:
        # Create new role entry
        new_role_entry = Role(member_id=member_id, role=new_role)
        session.add(new_role_entry)

    session.flush()

    # Return member with role using the same query structure as get_member_roles
    result = (
        session.query(
            Members.id,
            Members.name,
            Members.email,
            Members.phone_number,
            Members.uni_id,
            Members.gender,
            Members.uni_level,
            Members.uni_college,
            Members.is_authenticated,
            Members.created_at,
            Members.updated_at,
            Role.role,
        )
        .join(Role, Members.id == Role.member_id)
        .filter(Members.id == member_id)
        .first()
    )
    if result is None:
        # Should be unreachable: existing_member was just confirmed to exist and
        # the role row above was either updated or created and flushed.
        raise DataIntegrityError(f"Member [{member_id}] has no role row immediately after one was assigned")

    return result._asdict()


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
