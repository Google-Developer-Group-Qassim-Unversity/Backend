import logging
from app.leaderboard_cache import refresh_member_names_cache
from fastapi import APIRouter, HTTPException, Response, status, Depends, Query
from app.DB import members as member_queries
from app.DB.schema import Members

from app.routers.models import (
    Member_model,
    MemberWithActivity_model,
    NotFoundResponse,
    ConflictResponse,
    CreatedMemberModel,
    PaginatedMembers_model,
    MemberStats_model,
    MemberUpdateModel,
    ManualMemberCreateModel,
    BatchCreateMembersRequest,
    BatchCreateMembersResponse,
)
from app.routers.member_account_models import CodedErrorResponse, DeletedAccountResponse, MemberOnboardingRequest
from app.services import member_accounts
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import CurrentMember, SignedInClerkUserId, authenticated_guard, credentials_to_member_model
from typing import Annotated
from app.dependencies import DB
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require

logger = logging.getLogger(__name__)


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
    logger.info(f"Updating member with id {member.id}")
    if updates.email is not None:
        existing_by_email = member_queries.get_member_by_email_or_none(session, updates.email)
        if existing_by_email and existing_by_email.id != member.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Member with email {updates.email} already exists"
            )
    public_name_changed = updates.public_name is not None and updates.public_name != member.public_name
    updated_member = member_queries.update_member_by_id(session, member.id, updates.model_dump(exclude_none=True))
    logger.info(f"Member with id {member.id} updated successfully")
    session.commit()
    if public_name_changed:
        refresh_member_names_cache()
    return updated_member


@router.delete(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=DeletedAccountResponse,
    responses={
        401: {"model": CodedErrorResponse, "description": "No session token, or one that does not verify"},
        409: {
            "model": CodedErrorResponse,
            "description": "member_is_staff: has a club role this semester; member_not_verified: the record "
            "carries no university ID or email this sign-in proved, so an admin deletes it",
        },
        502: {"model": CodedErrorResponse, "description": "clerk_unavailable: retry; repeating the call is safe"},
        503: {"model": CodedErrorResponse, "description": "clerk_not_configured"},
    },
)
def delete_my_account(clerk_user_id: SignedInClerkUserId, session: DB):
    """Delete the caller's account (App Store 5.1.1(v), Google Play account deletion).

    The member record linked to this sign-in is anonymized: names, emails,
    phone, university ID, Clerk link, academic details, card and public
    profile, and registrations with their form answers are removed, and the
    record leaves every leaderboard, history and member list. Its points and
    attendance rows stay, under no name. Then the Clerk sign-in is deleted.
    Repeating the call is safe, and is how a 502 is recovered from; once the
    sign-in is gone, a repeat answers 401.
    """
    member_anonymized = member_accounts.delete_account(session, clerk_user_id)
    return {"deleted": True, "member_anonymized": member_anonymized}


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    response_model=list[MemberWithActivity_model],
    dependencies=[Depends(Require(Perm.MEMBERS_VIEW))],
)
def get_all_members(session: DB):
    members = member_queries.get_members(session)
    return members


@router.get(
    "/paginated",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedMembers_model,
    dependencies=[Depends(Require(Perm.MEMBERS_VIEW))],
)
def list_members_paginated(
    session: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    search: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str, Query()] = "name",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
):
    """One page of members, searched/sorted/counted in the database. The full
    list at GET / is untouched for callers that need every row."""
    offset = (page - 1) * page_size
    total = member_queries.count_members(session, search)
    items = member_queries.get_members_paginated(session, page_size, offset, search, sort_by, order)
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
    response_model=MemberStats_model,
    dependencies=[Depends(Require(Perm.MEMBERS_VIEW))],
)
def get_member_stats(session: DB):
    """Aggregate member counts for the page cards - one grouped COUNT query."""
    return member_queries.get_member_stats(session)


@router.post(
    "/manual",
    status_code=status.HTTP_201_CREATED,
    response_model=CreatedMemberModel,
    responses={409: {"model": ConflictResponse, "description": "Member already exists"}},
    dependencies=[Depends(Require(Perm.MEMBERS_CREATE))],
)
def create_member_manual(member_data: ManualMemberCreateModel, session: DB):
    logger.info(f"Manually creating member with uni_id {member_data.uni_id}")
    if member_data.uni_id is not None:
        existing = member_queries.get_member_by_uni_id_or_none(session, member_data.uni_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"Member with uni_id {member_data.uni_id} already exists"
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
    logger.info(f"Member with uni_id {member_data.uni_id} created successfully with ID {new_member.id}")
    session.commit()
    return {"member": new_member, "already_exists": False}


@router.post(
    "/batch",
    status_code=status.HTTP_200_OK,
    response_model=BatchCreateMembersResponse,
    dependencies=[Depends(Require(Perm.MEMBERS_CREATE))],
)
def batch_create_members(request: BatchCreateMembersRequest, session: DB):
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
                result_members.append(Member_model.model_validate(existing))
                logger.info(f"Member with uni_id {member_data.uni_id} already exists")
                continue

            existing_by_email = member_queries.get_member_by_email_or_none(session, member_data.email)
            if existing_by_email:
                failed_count += 1
                logger.error(
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
                logger.error(f"Failed to create member with uni_id {member_data.uni_id}")
                continue
            created_count += 1
            result_members.append(Member_model.model_validate(new_member))
            logger.info(f"Member with uni_id {member_data.uni_id} created successfully")
        except Exception as e:
            failed_count += 1
            logger.exception(f"Error creating member with uni_id {member_data.uni_id}: {e}")
            continue

    session.commit()

    return BatchCreateMembersResponse(
        created_count=created_count, existing_count=existing_count, failed_count=failed_count, members=result_members
    )


@router.get(
    "/uni-id/{uni_id}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
    dependencies=[Depends(Require(Perm.MEMBERS_VIEW))],
)
def get_member_by_uni_id(uni_id: str, session: DB):
    member = member_queries.get_member_by_uni_id(session, uni_id)
    return member


@router.get(
    "/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Member_model,
    responses={404: {"model": NotFoundResponse, "description": "Member not found"}},
    dependencies=[Depends(Require(Perm.MEMBERS_VIEW))],
)
def get_member_by_id(member_id: int, session: DB):
    member = member_queries.get_member_by_id(session, member_id)
    return member


@router.post(
    "/onboarding",
    status_code=status.HTTP_201_CREATED,
    response_model=CreatedMemberModel,
    responses={
        200: {"model": CreatedMemberModel, "description": "An existing member record was linked or updated"},
        401: {
            "model": CodedErrorResponse,
            "description": "No session token, one that does not verify, or a sign-in deleted meanwhile",
        },
        409: {
            "model": CodedErrorResponse,
            "description": "member_claimed, email_in_use, or member_not_verified: the record linked to this "
            "sign-in carries a different university ID",
        },
        502: {"model": CodedErrorResponse, "description": "clerk_unavailable: retry"},
        503: {"model": CodedErrorResponse, "description": "clerk_not_configured"},
    },
)
def onboard_member(
    answers: MemberOnboardingRequest, clerk_user_id: SignedInClerkUserId, session: DB, response: Response
):
    """The member app's onboarding: what the Website does with publicMetadata and ``POST /`` together.

    Creates the caller's member record, or links the one that is theirs (by the
    university ID or an email address they proved to Clerk, never by anything
    in the body) and updates it with these answers. Seeds the card's status,
    and writes the same Clerk publicMetadata the Website's onboarding writes,
    ``onboardingComplete`` included, so the Website treats the member as
    onboarded too. 201 when a record was created, 200 when one already existed.
    Repeating the call is safe. Refresh the session token afterwards: it still
    carries the old metadata.
    """
    member, already_exists = member_accounts.onboard(session, clerk_user_id, answers)
    if already_exists:
        response.status_code = status.HTTP_200_OK
    return {"member": member, "already_exists": already_exists}


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CreatedMemberModel)
def create_member(credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)], session: DB):
    member: Member_model | None = None
    # credentials_to_member_model can raise before either of these is assigned;
    # the `finally` below reads them regardless of how the try block exits.
    new_member: Members | None = None
    already_exist: bool = False
    try:
        member = credentials_to_member_model(credentials)
        logger.info(f"Creating Member {member.uni_id}")
        new_member, already_exist = member_queries.create_member_if_not_exists(session, member, is_authenticated=True)
        if new_member is None:
            # The email is already on a different, already-claimed member row (e.g. two
            # distinct people sharing an inbox) - not something we can auto-resolve.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="This email is already associated with a different account"
            )
        if not already_exist:
            logger.info(f"Member with uni_id {member.uni_id} created successfully with ID {new_member.id}")
        else:
            logger.info(
                f"Member with uni_id {member.uni_id} already exists with ID {new_member.id}, updated data successfully"
            )
        session.commit()
        return {"member": new_member, "already_exists": already_exist}
    finally:
        if new_member is not None and member is not None:
            logger.debug("request body: %s", member.model_dump())
            logger.info(f"member {new_member.uni_id} {'Created' if not already_exist else 'Updated'} successfully")
        else:
            logger.debug("request body: %s", credentials.model_dump())
