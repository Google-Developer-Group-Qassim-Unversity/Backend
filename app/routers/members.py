from fastapi import APIRouter, HTTPException, status, Depends
from app.DB import members as member_queries
from ..DB.main import SessionLocal
from app.routers.models import Member_model, NotFoundResponse, MemberHistory_model, MeberCreate_model
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.routers.auth import clerk_auth_guard
import json
from app.helpers import get_uni_id_from_credentials, credentials_to_member_model
from app.routers.logging import write_log_exception, write_log_traceback, create_log_file, write_log, write_log_title
router = APIRouter()



@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Member_model])
def get_all_members():
    with SessionLocal() as session:
        members = member_queries.get_members(session)
    return members

@router.get("/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}})
def get_member_by_id(member_id: int):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
    return member

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=MeberCreate_model, responses={403: {"model": NotFoundResponse, "description": "You can only create your own member profile"}})
def create_member(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        log_file = create_log_file("create member")
        try:
            member = credentials_to_member_model(credentials)
            write_log_title(log_file, f"Creating Member {member.uni_id}")
            new_member, already_exist = member_queries.create_member_if_not_exists(session, member)
            session.commit()
            return {"member": new_member, "already_exists": already_exist}
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the member")




@router.put("/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}, 409: {"model": NotFoundResponse, "description": "Member already exists"}})
def update_member(member_id: int, member: Member_model):
    with SessionLocal() as session:
        updated_member = member_queries.update_member(session, member_id, member)
        if updated_member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
        if updated_member == -1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"member with the uni_id '{member.uni_id}' already exists")
        session.commit()
    return updated_member

@router.get("/history", status_code=status.HTTP_200_OK)
def get_member_history(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    with SessionLocal() as session:
        member_uni_id = credentials.model_dump()['decoded']['metadata']['uiId']
        member_history = member_queries.get_member_history(session, member_uni_id)
        print(member_history)
    return member_history

@router.get("/votes", status_code=status.HTTP_200_OK)
def did_member_vote(credentials: HTTPAuthorizationCredentials = Depends(clerk_auth_guard)):
    member_uni_id = get_uni_id_from_credentials(credentials)
    with open("votes_members.json", "r") as f:
        voted_members = json.load(f)
    return {"has_voted": member_uni_id in voted_members}