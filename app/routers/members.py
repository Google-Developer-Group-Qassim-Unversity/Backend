from fastapi import APIRouter, HTTPException, Query, status, Depends
from app.DB import members as member_queries
from app.DB import events as event_queries
from app.DB.schema import RoleType
from app.DB.main import SessionLocal
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
    ClaimCheckResponse,
    ClaimMemberRequest,
)
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import (
    admin_guard,
    authenticated_guard,
    credentials_to_member_model,
    resolve_member,
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
    with SessionLocal() as session:
        member = resolve_member(session, credentials)
        session.commit()
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
    with LogFile("update current member"), SessionLocal() as session:
        try:
            member = resolve_member(session, credentials)
            write_log_title(f"Updating member with id {member.id}")
            updated_member = member_queries.update_member_by_id(
                session, member.id, updates.model_dump(exclude_none=True)
            )
            write_log(f"Member with id {member.id} updated successfully")
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


@router.post(
    "/manual",
    status_code=status.HTTP_201_CREATED,
    response_model=CreatedMemberModel,
    responses={409: {"model": ConflictResponse, "description": "Member already exists"}},
)
def create_member_manual(
    member_data: ManualMemberCreateModel,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(super_admin_guard)],
):
    with LogFile("create member manual"), SessionLocal() as session:
        try:
            write_log_title(f"Manually creating member with uni_id {member_data.uni_id}")
            if member_data.uni_id is not None:
                existing = member_queries.get_member_by_uni_id_or_none(session, member_data.uni_id)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Member with uni_id {member_data.uni_id} already exists",
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
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Member with uni_id {member_data.uni_id} already exists",
                )
            write_log(f"Member with uni_id {member_data.uni_id} created successfully with ID {new_member.id}")
            session.commit()
            return {"member": new_member, "already_exists": False}
        except HTTPException:
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while creating the member"
            )


@router.post("/batch", status_code=status.HTTP_200_OK, response_model=BatchCreateMembersResponse)
def batch_create_members(
    request: BatchCreateMembersRequest, credentials: Annotated[HTTPAuthorizationCredentials, Depends(super_admin_guard)]
):
    with LogFile("batch create members"), SessionLocal() as session:
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

        try:
            session.commit()
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while committing batch member creation",
            )

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


@router.get("/claim-check", status_code=status.HTTP_200_OK, response_model=ClaimCheckResponse)
def claim_check(
    email: Annotated[str, Query(min_length=1)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)],
):
    """Look for an existing admin-created (unauthenticated, unclaimed) member with this email.

    Used during onboarding for members with no uni_id (e.g. Google sign-ups) so they can be
    offered a chance to link their existing points/attendance history instead of starting fresh.
    """
    with SessionLocal() as session:
        match = member_queries.get_unclaimed_member_by_email_or_none(session, email)
        if not match:
            return ClaimCheckResponse(found=False)
        attended, _participated = event_queries.get_member_events(session, match.id)
        attended_events = [event["name"] for event in attended]

    return ClaimCheckResponse(
        found=True,
        member={
            "id": match.id,
            "name": match.name,
            "email": match.email,
            "uni_college": match.uni_college,
            "uni_level": match.uni_level,
            "gender": match.gender,
            "attended_events": attended_events,
        },
    )


@router.post(
    "/claim",
    status_code=status.HTTP_200_OK,
    response_model=CreatedMemberModel,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
)
def claim_member(
    request: ClaimMemberRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)],
):
    """Attach the calling Clerk identity to an existing unclaimed member row, preserving its history."""
    with LogFile("claim member"), SessionLocal() as session:
        try:
            new_member_data = credentials_to_member_model(credentials)

            target = member_queries.get_member_by_id(session, request.member_id)

            if target.clerk_user_id is not None or target.is_authenticated:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="This member has already been claimed"
                )
            if not target.email or target.email.strip().lower() != (new_member_data.email or "").strip().lower():
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email does not match this member")

            new_member_data.id = target.id
            write_log_title(f"Claiming member {target.id} for clerk user {new_member_data.clerk_user_id}")
            updated_member = member_queries.update_member(session, new_member_data, is_authenticated=True)
            session.commit()
            write_log(
                f"clerk_user_id={new_member_data.clerk_user_id} claimed the email={target.email} on db_row_id={target.id}"
            )
            return {"member": updated_member, "already_exists": True}
        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while claiming the member"
            )


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
