from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Actions, Members, MembersLogs, Logs, Events, Role
from ..routers.models import Member_model
from datetime import datetime


def create_member(
    session: Session, member: Member_model, is_authenticated: bool = False
):
    try:
        new_member = Members(
            name=member.name,
            email=member.email,
            phone_number=member.phone_number,
            uni_id=member.uni_id,
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
        session.rollback()
        print(f"IntegrityError in create_member: {str(e)[:50]}...")
        return None


def create_member_if_not_exists(
    session: Session, member: Member_model, is_authenticated: bool = False
) -> tuple[Members | None, bool]:
    existing_member = session.scalar(
        select(Members).where(Members.uni_id == member.uni_id)
    )
    if existing_member:
        member.id = existing_member.id
        already_exist = True
        updated_member = update_member(session, member, is_authenticated)
        return updated_member, already_exist
    already_exist = False
    return create_member(session, member, is_authenticated), already_exist


def get_members(session: Session):
    statement = select(Members)
    member = session.scalars(statement).all()
    return member


def get_member_by_id(session: Session, member_id: int | list[int]):
    if isinstance(member_id, list):
        statement = select(Members).where(Members.id.in_(member_id))
        return session.scalars(statement).all()
    else:
        statement = select(Members).where(Members.id == member_id)
        return session.scalars(statement).first()


def get_member_by_uni_id(session: Session, uni_id: str):
    statement = select(Members).where(Members.uni_id == uni_id)
    return session.scalars(statement).first()


def update_member(
    session: Session, member: Member_model, is_authenticated: bool = False
):
    existing_member = session.scalar(select(Members).where(Members.id == member.id))
    if not existing_member:
        return None
    print(f"Updating member: {existing_member.name}")
    existing_member.name = member.name
    existing_member.email = member.email
    existing_member.phone_number = member.phone_number
    existing_member.gender = member.gender
    existing_member.uni_level = member.uni_level
    existing_member.uni_college = member.uni_college
    existing_member.updated_at = datetime.now()
    existing_member.is_authenticated = is_authenticated
    session.flush()
    print(f"Updated member: {existing_member.name}")
    return existing_member


def get_member_history(session: Session, uni_id: str):
    query = (
        session.query(
            Events.name,
            Events.description,
            Events.location,
            Events.location_type,
            Events.start_datetime,
            Events.end_datetime,
            Actions.action_name,
            Actions.points,
        )
        .select_from(Members)
        .join(MembersLogs, Members.id == MembersLogs.member_id)
        .join(Logs, MembersLogs.log_id == Logs.id)
        .outerjoin(Events, Logs.event_id == Events.id)
        .join(Actions, Logs.action_id == Actions.id)
        .filter(Members.uni_id == uni_id)
    )

    return [row._asdict() for row in query.all()]


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


def update_member_role(session: Session, member_id: int, new_role: str):
    # Check if member exists
    existing_member = session.scalar(select(Members).where(Members.id == member_id))
    if not existing_member:
        return None

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

    return result._asdict() if result else None


def update_member_by_uni_id(
    session: Session, uni_id: str, updates: dict
) -> Members | None:
    member = session.scalar(select(Members).where(Members.uni_id == uni_id))
    if not member:
        return None

    for key, value in updates.items():
        if value is not None and hasattr(member, key):
            setattr(member, key, value)

    member.updated_at = datetime.now()
    member.is_authenticated = True
    session.flush()
    return member
