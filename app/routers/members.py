from fastapi import APIRouter, HTTPException
from app.DB.queries import members as member_queries
from app.DB.schema import SessionLocal
from app.DB.models import Member_model
from sqlalchemy.exc import IntegrityError
router = APIRouter()

@router.get("/", status_code=200, response_model=list[Member_model])
def members():
    with SessionLocal() as session:
        members = member_queries.get_members(session)
    return members

@router.get("/{member_id}", status_code=200, response_model=Member_model)
def get_member(member_id: int):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    session.flush()
    return member

@router.post("/", status_code=201, response_model=Member_model)
def create_member(member: Member_model):
    with SessionLocal() as session:
        try:
            new_member = member_queries.create_member(session, member)
        except IntegrityError as e:
            print(f"IntegrityError: {e}")
            raise HTTPException(status_code=400, detail=f"Member with this uni_id already exists.",)
    return new_member

@router.put("/{member_id}", status_code=200, response_model=Member_model)
def update_member(member_id: int, member: Member_model):
    with SessionLocal() as session:
        updated_member = member_queries.update_member(session, member_id, member)
    if updated_member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return updated_member