from fastapi import APIRouter, HTTPException, status, Depends
from app.DB import members as member_queries
from app.DB.schema import RoleType
from app.DB.main import SessionLocal
from app.routers.models import (
    Member_model,
    NotFoundResponse,
    CreatedMemberModel,
    manual_members,
    MemberWithRole_model,
    MemberUpdateModel,
)
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import (
    admin_guard,
    authenticated_guard,
    get_uni_id_from_credentials,
    credentials_to_member_model,
    super_admin_guard,
)
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_exception,
    write_log_json_to,
    write_log_title,
    write_log_traceback,
)
from typing import Annotated


router = APIRouter()


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def get_current_member(credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)]):
    uni_id = get_uni_id_from_credentials(credentials)
    with SessionLocal() as session:
        member = member_queries.get_member_by_uni_id(session, uni_id)
    return member


@router.patch(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def update_current_member(
    updates: MemberUpdateModel, credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)]
):
    uni_id = get_uni_id_from_credentials(credentials)
    with LogFile("update current member"), SessionLocal() as session:
        try:
            write_log_title(f"Updating member with uni_id {uni_id}")
            updated_member = member_queries.update_member_by_uni_id(
                session, uni_id, updates.model_dump(exclude_none=True)
            )
            write_log(f"Member with uni_id {uni_id} updated successfully")
            session.commit()
            return updated_member
        except HTTPException:
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while updating member"
            )


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Member_model])
def get_all_members(credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        members = member_queries.get_members(session)
    return members


@router.get(
    "/uni-id/{uni_id}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def get_member_by_uni_id(uni_id: str, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        member = member_queries.get_member_by_uni_id(session, uni_id)
    return member


@router.get(
    "/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def get_member_by_id(member_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
    return member


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CreatedMemberModel)
def create_member(credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)]):
    with LogFile("create member") as log, SessionLocal() as session:
        member: Member_model | None = None
        try:
            member = credentials_to_member_model(credentials)
            write_log_title(f"Creating Member {member.uni_id}")
            new_member, already_exist = member_queries.create_member_if_not_exists(
                session, member, is_authenticated=True
            )
            if not already_exist:
                write_log(f"Member with uni_id {member.uni_id} created successfully with ID {new_member.id}")
            else:
                write_log(
                    f"Member with uni_id {member.uni_id} already exists with ID {new_member.id}, updated data successfully"
                )
            session.commit()
            return {"member": new_member, "already_exists": already_exist}
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the member"
            )
        finally:
            if new_member is not None and member is not None:
                write_log_json_to(log.file, member.model_dump())
                write_log(f"member {new_member.uni_id} {'Created' if not already_exist else 'Updated'} successfully")
            else:
                write_log_json_to(log.file, credentials.model_dump())


@router.get("/roles", status_code=status.HTTP_200_OK, response_model=list[MemberWithRole_model])
def get_member_roles(credentials: Annotated[HTTPAuthorizationCredentials, Depends(super_admin_guard)]):
    with SessionLocal() as session:
        roles = member_queries.get_member_roles(session)
    return roles


@router.post("/roles", status_code=status.HTTP_200_OK, response_model=MemberWithRole_model)
def update_member_roles(
    member_id: int, new_role: RoleType, credentials: Annotated[HTTPAuthorizationCredentials, Depends(super_admin_guard)]
):
    with LogFile("update member role"), SessionLocal() as session:
        try:
            write_log_title(f"Updating role for member_id {member_id} to {new_role.value}")
            updated_member = member_queries.update_member_role(session, member_id, new_role=new_role)
            session.commit()
            return updated_member
        except HTTPException:
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while updating the member's role",
            )
