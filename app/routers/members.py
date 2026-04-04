from datetime import date
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
from app.config import config
from app.helpers import (
    admin_guard,
    authenticated_guard,
    get_uni_id_from_credentials,
    credentials_to_member_model,
    get_pydantic_members,
    super_admin_guard,
)
from app.routers.upload import validate_sheet
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
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with uni_id {uni_id} not found")
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
            if not updated_member:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with uni_id {uni_id} not found"
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
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with university ID {uni_id} not found"
            )
    return member


@router.get(
    "/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def get_member_by_id(member_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(config.CLERK_GUARD)]):
    with SessionLocal() as session:
        member = member_queries.get_member_by_id(session, member_id)
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found")
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
            if not updated_member:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Member with id {member_id} not found"
                )
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


@router.post("/manual", status_code=status.HTTP_201_CREATED)
def create_member_manual(members_sheet: manual_members):
    # Only allow in development environment
    if not config.is_dev:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This endpoint is only available in development mode"
        )
    try:
        with LogFile("manual member creation"), SessionLocal() as session:
            write_log_title("Manual Member Creation")
            write_log(f"Validating request to create members from sheet: {members_sheet.members_sheet}")
            validate_sheet(str(members_sheet.members_sheet), date.today(), date.today())
            write_log(f"Sheet validation successful for {members_sheet.members_sheet}")
            members = get_pydantic_members(members_sheet.members_sheet)
            created_members = []
            existing_count = 0
            new_count = 0
            members_len = len(members)
            for i, member in enumerate(members, start=1):
                write_log(f"Processing member {i}/{members_len} with uni_id {member.uni_id}")
                existing_member = member_queries.get_member_by_uni_id(session, member.uni_id)
                if not existing_member:
                    write_log(f"No existing member found with ID {member.id}, creating new member")
                    created_member = member_queries.create_member(session, member, is_authenticated=False)
                    if created_member is None:
                        exception = ValueError(f"Failed to create member with uni_id {member.uni_id}")
                        write_log_exception(exception)
                        raise exception
                    new_count += 1
                    created_members.append(created_member)
                else:
                    write_log(f"Existing member found with ID {existing_member.id}, skipping ⏩")
                    created_members.append(existing_member)
                    existing_count += 1
            session.commit()
        return {"existing_count": existing_count, "new_count": new_count, "created_members": created_members}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.args[0])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating members"
        )


@router.get("/history", status_code=status.HTTP_200_OK)
def get_member_history(credentials: Annotated[HTTPAuthorizationCredentials, Depends(config.CLERK_GUARD)]):
    with SessionLocal() as session:
        member_uni_id = credentials.model_dump()["decoded"]["metadata"]["uiId"]
        member_history = member_queries.get_member_history(session, member_uni_id)
        print(member_history)
    return member_history
