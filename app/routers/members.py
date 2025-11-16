from fastapi import APIRouter, HTTPException, status
from app.DB import members as member_queries
from ..DB.main import SessionLocal
from app.routers.models import Member_model, NotFoundResponse
router = APIRouter()
clerk_config = ClerkConfig(jwks_url="https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json") 
clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Member_model])
def get_all_members():
    with SessionLocal() as session:
        members = member_queries.get_members(session)
    return members

@router.get("/{member_id}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}})
def get_member_by_id(member_id: int):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
    return member

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=list[Member_model], responses={409: {"model": NotFoundResponse, "description": "Member already exists"}})
def create_members(members: list[Member_model]):
    with SessionLocal() as session:
        created_members = []
        for member in members:
            new_member = member_queries.create_member(session, member)
            if not new_member:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"member with the uni_id '{member.uni_id}' already exists")
            created_members.append(new_member)
        session.commit()
    return created_members

@router.put("/{member_id}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}, 409: {"model": NotFoundResponse, "description": "Member already exists"}})
def update_member(member_id: int, member: Member_model):
    with SessionLocal() as session:
        updated_member = member_queries.update_member(session, member_id, member)
        if updated_member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
        if updated_member == -1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"member with the uni_id '{member.uni_id}' already exists")
        session.commit()
    return updated_member

