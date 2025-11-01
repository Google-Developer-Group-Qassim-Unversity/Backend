from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from .schema import Members
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

def get_members(session: Session):
    statement = select(Members)
    member = session.scalars(statement).all()
    return member

def get_member_by_id(session: Session, member_id: int):
    statement = select(Members).where(Members.id == member_id)
    member = session.scalars(statement).first()
    
    return member

def update_member(session: Session, member_id: int, member: Member_model):
    existing_member = session.scalar(select(Members).where(Members.id == member_id))
    print(f"Updating member: {existing_member.name}")
    if not existing_member:
        return None
    existing_member.name = member.name
    existing_member.email = member.email
    existing_member.phone_number = member.phone_number
    existing_member.uni_id = member.uni_id
    existing_member.gender = member.gender
    session.flush()
    print(f"Updated member: {existing_member.name}")
    return existing_member