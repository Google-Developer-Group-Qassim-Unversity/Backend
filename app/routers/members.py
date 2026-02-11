from datetime import date
from fastapi import APIRouter, HTTPException, status, Depends
from app.DB import members as member_queries, logs as logs_queries
from ..DB.main import SessionLocal
from app.routers.models import Member_model, NotFoundResponse, MemberHistory_model, MeberCreate_model, manual_members, MemberWithRole_model, RoleEnum
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from  app.config import config
import json
from app.helpers import admin_guard, get_uni_id_from_credentials, credentials_to_member_model, get_pydantic_members, super_admin_guard
from app.routers.upload import validate_sheet
from app.routers.logging import write_log_exception, write_log_traceback, create_log_file, write_log, write_log_title, write_log_json
router = APIRouter()



@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Member_model])
def get_all_members(credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        members = member_queries.get_members(session)
    return members

@router.get("/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}})
def get_member_by_id(member_id: int, credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
    return member

@router.get("/uni", status_code=status.HTTP_200_OK, response_model=list[Member_model])
def get_member_by_uni_id(uni_id: list[str], credentials: HTTPAuthorizationCredentials = Depends(admin_guard)):
    with SessionLocal() as session:
        members = member_queries.get_member_by_uni_id(session, uni_id)
        if not members:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with uni_id {uni_id} not found")
    return members

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=MeberCreate_model, responses={403: {"model": NotFoundResponse, "description": "You can only create your own member profile"}})
def create_member(credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    with SessionLocal() as session:
        log_file = create_log_file("create member")
        member = None
        try:
            member = credentials_to_member_model(credentials)
            write_log_title(log_file, f"Creating Member {member.uni_id}")
            new_member, already_exist = member_queries.create_member_if_not_exists(session, member, is_authenticated=True)
            if not already_exist:
                write_log(log_file, f"Member with uni_id {member.uni_id} created successfully with ID {new_member.id}")
            else:
                write_log(log_file, f"Member with uni_id {member.uni_id} already exists with ID {new_member.id}, updated data successfully")
            # === Special case starting from 2026-02-08. giving members 10 points for creating an account ===
            SIGNUP_LOG_ID=208
            write_log(log_file, f"Giving points for creating account, on log_id: {SIGNUP_LOG_ID}")
            new_log = logs_queries.create_member_log(session, new_member.id, SIGNUP_LOG_ID)
            write_log(log_file, f"Points given successfully for creating account")
            session.commit()
            return {"member": new_member, "already_exists": already_exist}
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the member")
        finally:
            if new_member is not None:
                write_log_json(log_file, member.model_dump())
                write_log(log_file, f"member {new_member.uni_id} {"Created" if not already_exist else "Updated"} successfully")
            else:
                write_log_json(log_file, credentials.model_dump())


@router.get("/roles", status_code=status.HTTP_200_OK, response_model=list[MemberWithRole_model])
def get_member_roles(credentials: HTTPAuthorizationCredentials = Depends(super_admin_guard)):
    with SessionLocal() as session:
        roles = member_queries.get_member_roles(session)
    return roles

@router.post("/roles", status_code=status.HTTP_200_OK, response_model=MemberWithRole_model)
def update_member_roles(member_id: int, new_role: RoleEnum, credentials: HTTPAuthorizationCredentials = Depends(super_admin_guard)):
    with SessionLocal() as session:
        updated_member = member_queries.update_member_role(session, member_id, new_role.value)
        if not updated_member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
        session.commit()
    return updated_member

@router.post("/manual", status_code=status.HTTP_201_CREATED)
def create_member_manual(members_sheet: manual_members):
    # Only allow in development environment
    if not config.is_dev:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This endpoint is only available in development mode")
    try: 
        log_file = create_log_file("manual member creation")
        with SessionLocal() as session:
            write_log_title(log_file, "Manual Member Creation")
            write_log(log_file, f"Validating request to create members from sheet: {members_sheet.members_sheet}")
            validate_sheet(str(members_sheet.members_sheet), date.today(), date.today())
            write_log(log_file, f"Sheet validation successful for {members_sheet.members_sheet}")
            members = get_pydantic_members(members_sheet.members_sheet)
            created_members= []
            existing_count = 0
            new_count = 0
            members_len = len(members)
            for i, member in enumerate(members, start=1):
                write_log(log_file, f"Processing member {i}/{members_len} with uni_id {member.uni_id}")
                new_member, already_exist = member_queries.create_member_if_not_exists(session, member, is_authenticated=False)
                created_members.append(new_member)
                if already_exist:
                    existing_count += 1
                else:
                    new_count += 1
            session.commit()
        return {"existing_count": existing_count, "new_count": new_count, "created_members": created_members, }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.args[0])
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating members")

# OLD, needs auth and correct update_member parameters (member model not id, and is_authenticated)
# @router.put("/{member_id:int}", status_code=status.HTTP_200_OK, response_model=Member_model, responses={404: {"model": NotFoundResponse, "description": "Member not found"}, 409: {"model": NotFoundResponse, "description": "Member already exists"}})
# def update_member(member_id: int, member: Member_model):
#     with SessionLocal() as session:
#         updated_member = member_queries.update_member(session, member_id, member)
#         if updated_member is None:
#             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
#         if updated_member == -1:
#             raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"member with the uni_id '{member.uni_id}' already exists")
#         session.commit()
#     return updated_member

@router.get("/history", status_code=status.HTTP_200_OK)
def get_member_history(credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    with SessionLocal() as session:
        member_uni_id = credentials.model_dump()['decoded']['metadata']['uiId']
        member_history = member_queries.get_member_history(session, member_uni_id)
        print(member_history)
    return member_history

@router.get("/votes", status_code=status.HTTP_200_OK)
def did_member_vote(credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD)):
    member_uni_id = get_uni_id_from_credentials(credentials)
    with open("votes_members.json", "r") as f:
        voted_members = json.load(f)
    return {"has_voted": member_uni_id in voted_members}