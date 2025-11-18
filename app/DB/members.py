from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Actions, Members, MembersLogs, Logs, Events
from ..routers.models import Member_model

def create_member(session: Session, member: Member_model):
    try:
        new_member = Members(
            name=member.name,
            email=member.email,
            phone_number=member.phone_number,
            uni_id=member.uni_id,
            gender=member.gender
        )
        session.add(new_member)
        session.flush()
        return new_member
    except IntegrityError as e:
        session.rollback()
        print(f"IntegrityError in create_member: {str(e)[:50]}...")
        return None

def create_member_if_not_exists(session: Session, member: Member_model) -> tuple[Members, bool]:
    existing_member = session.scalar(select(Members).where(Members.uni_id == member.uni_id))
    if existing_member:
        doesExist = True
        session.flush()
        return existing_member, doesExist
    doesExist = False
    return create_member(session, member), doesExist

def get_members(session: Session):
    statement = select(Members)
    member = session.scalars(statement).all()
    return member

def get_member_by_id(session: Session, member_id: int):
    statement = select(Members).where(Members.id == member_id)
    member = session.scalars(statement).first()
    
    return member

def get_member_by_uni_id(session: Session, uni_id: str):
    statement = select(Members).where(Members.uni_id == uni_id)
    member = session.scalars(statement).first()
    return member

def update_member(session: Session, member_id: int, member: Member_model):
    existing_member = session.scalar(select(Members).where(Members.id == member_id))
    print(f"Updating member: {existing_member.name}")
    if not existing_member:
        return None
    try:
        existing_member.uni_id = member.uni_id
        session.flush()
    except IntegrityError as e:
        session.rollback()
        print(f"IntegrityError in update_member: {str(e)[:50]}...")
        return -1
    existing_member.name = member.name
    existing_member.email = member.email
    existing_member.phone_number = member.phone_number
    existing_member.gender = member.gender
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


