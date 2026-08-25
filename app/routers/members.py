from fastapi import APIRouter, HTTPException, Query, status, Depends
from app.DB import members as member_queries
from app.DB.schema import RoleType

from app.routers.models import (
    Member_model,
    NotFoundResponse,
    ConflictResponse,
    CreatedMemberModel,
    manual_members,
    MemberWithRole_model,
    MemberUpdateModel,
    ManualMemberCreateModel,
    BatchCreateMembersRequest,
    BatchCreateMembersResponse,
    BatchCreateMemberItem,
)
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import CurrentMember, admin_guard, authenticated_guard, credentials_to_member_model, super_admin_guard
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_exception,
    write_log_json_to,
    write_log_title,
    write_log_traceback,
)
from typing import Annotated
from app.dependencies import DB

router = APIRouter(prefix="/members", tags=["members"])


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def get_current_member(member: CurrentMember, session: DB):
    session.commit()
    return member


@router.patch(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def update_current_member(updates: MemberUpdateModel, member: CurrentMember, session: DB):
    with LogFile("update current member"):
        write_log_title(f"Updating member with id {member.id}")
        if updates.email is not None:
            existing_by_email = member_queries.get_member_by_email_or_none(session, updates.email)
            if existing_by_email and existing_by_email.id != member.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=f"Member with email {updates.email} already exists"
                )
        updated_member = member_queries.update_member_by_id(session, member.id, updates.model_dump(exclude_none=True))
        write_log(f"Member with id {member.id} updated successfully")
        session.commit()
        return updated_member


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Member_model], dependencies=[Depends(admin_guard)])
def get_all_members(session: DB):
    members = member_queries.get_members(session)
    return members


@router.post(
    "/manual",
    status_code=status.HTTP_201_CREATED,
    response_model=CreatedMemberModel,
    responses={409: {"model": ConflictResponse, "description": "Member already exists"}},
    dependencies=[Depends(super_admin_guard)],
)
def create_member_manual(member_data: ManualMemberCreateModel, session: DB):
    with LogFile("create member manual"):
        write_log_title(f"Manually creating member with uni_id {member_data.uni_id}")
        if member_data.uni_id is not None:
            existing = member_queries.get_member_by_uni_id_or_none(session, member_data.uni_id)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Member with uni_id {member_data.uni_id} already exists",
                )
        existing_by_email = member_queries.get_member_by_email_or_none(session, member_data.email)
        if existing_by_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Member with email {member_data.email} already exists"
            )
        member = Member_model(
            name=member_data.name,
            email=member_data.email,
            phone_number=member_data.phone_number or "",
            uni_id=member_data.uni_id,
            gender=member_data.gender,
            uni_level=0,
            uni_college="UNKNOWN",
        )
        new_member = member_queries.create_member(session, member, is_authenticated=False)
        if new_member is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Member with uni_id {member_data.uni_id} already exists"
            )
        write_log(f"Member with uni_id {member_data.uni_id} created successfully with ID {new_member.id}")
        session.commit()
        return {"member": new_member, "already_exists": False}


@router.post(
    "/batch",
    status_code=status.HTTP_200_OK,
    response_model=BatchCreateMembersResponse,
    dependencies=[Depends(super_admin_guard)],
)
def batch_create_members(request: BatchCreateMembersRequest, session: DB):
    with LogFile("batch create members"):
        created_count = 0
        existing_count = 0
        failed_count = 0
        result_members: list[Member_model] = []

        for member_data in request.members:
            try:
                existing = (
                    member_queries.get_member_by_uni_id_or_none(session, member_data.uni_id)
                    if member_data.uni_id is not None
                    else None
                )
                if existing:
                    existing_count += 1
                    result_members.append(existing)
                    write_log(f"Member with uni_id {member_data.uni_id} already exists")
                    continue

                existing_by_email = member_queries.get_member_by_email_or_none(session, member_data.email)
                if existing_by_email:
                    failed_count += 1
                    write_log_exception(
                        f"Member with email {member_data.email} already exists as member id "
                        f"{existing_by_email.id} (uni_id {existing_by_email.uni_id}) - skipping row "
                        f"with uni_id {member_data.uni_id}"
                    )
                    continue

                member = Member_model(
                    name=member_data.name,
                    email=member_data.email,
                    phone_number=member_data.phone_number or "",
                    uni_id=member_data.uni_id,
                    gender=member_data.gender,
                    uni_level=member_data.uni_level if member_data.uni_level is not None else 0,
                    uni_college=member_data.uni_college if member_data.uni_college is not None else "UNKNOWN",
                )
                new_member = member_queries.create_member(session, member, is_authenticated=False)
                if new_member is None:
                    failed_count += 1
                    write_log_exception(f"Failed to create member with uni_id {member_data.uni_id}")
                    continue
                created_count += 1
                result_members.append(new_member)
                write_log(f"Member with uni_id {member_data.uni_id} created successfully")
            except Exception as e:
                failed_count += 1
                write_log_exception(f"Error creating member with uni_id {member_data.uni_id}: {e}")
                write_log_traceback()
                continue

        session.commit()

        return BatchCreateMembersResponse(
            created_count=created_count,
            existing_count=existing_count,
            failed_count=failed_count,
            members=result_members,
        )


@router.get(
    "/uni-id/{uni_id}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
    dependencies=[Depends(admin_guard)],
)
def get_member_by_uni_id(uni_id: str, session: DB):
    member = member_queries.get_member_by_uni_id(session, uni_id)
    return member


@router.get(
    "/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
    dependencies=[Depends(admin_guard)],
)
def get_member_by_id(member_id: int, session: DB):
    member = member_queries.get_member_by_id(session, member_id)
    return member


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CreatedMemberModel)
def create_member(credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)], session: DB):
    with LogFile("create member") as log:
        member: Member_model | None = None
        try:
            member = credentials_to_member_model(credentials)
            write_log_title(f"Creating Member {member.uni_id}")
            new_member, already_exist = member_queries.create_member_if_not_exists(
                session, member, is_authenticated=True
            )
            if new_member is None:
                # The email is already on a different, already-claimed member row (e.g. two
                # distinct people sharing an inbox) - not something we can auto-resolve.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This email is already associated with a different account",
                )
            if not already_exist:
                write_log(f"Member with uni_id {member.uni_id} created successfully with ID {new_member.id}")
            else:
                write_log(
                    f"Member with uni_id {member.uni_id} already exists with ID {new_member.id}, updated data successfully"
                )
            session.commit()
            return {"member": new_member, "already_exists": already_exist}
        finally:
            if new_member is not None and member is not None:
                write_log_json_to(log.file, member.model_dump())
                write_log(f"member {new_member.uni_id} {'Created' if not already_exist else 'Updated'} successfully")
            else:
                write_log_json_to(log.file, credentials.model_dump())


@router.get(
    "/roles",
    status_code=status.HTTP_200_OK,
    response_model=list[MemberWithRole_model],
    dependencies=[Depends(super_admin_guard)],
)
def get_member_roles(session: DB):
    roles = member_queries.get_member_roles(session)
    return roles


@router.post(
    "/roles",
    status_code=status.HTTP_200_OK,
    response_model=MemberWithRole_model,
    dependencies=[Depends(super_admin_guard)],
)
def update_member_roles(member_id: int, new_role: RoleType, session: DB):
    with LogFile("update member role"):
        write_log_title(f"Updating role for member_id {member_id} to {new_role.value}")
        updated_member = member_queries.update_member_role(session, member_id, new_role=new_role)
        session.commit()
        return updated_member
