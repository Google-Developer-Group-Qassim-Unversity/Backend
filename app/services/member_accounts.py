"""The member app's account flows: onboarding, and deleting an account."""

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB import members as member_queries
from app.DB.schema import MemberProfiles, Members, MembersGender
from app.DB.wallet import update_member_profile
from app.exceptions import (
    AccountRecordNotProven,
    InvalidPersonalEmail,
    MemberEmailInUse,
    MemberLinkedToAnotherLogin,
    NotSignedIn,
    StaffAccountDeletion,
)
from app.helpers import identify_actor
from app.leaderboard_cache import refresh_member_names_cache
from app.routers.member_account_models import MAX_EMAIL_LENGTH, MemberOnboardingRequest
from app.routers.models import Member_model
from app.services import apple_signin, clerk_api
from app.services.clerk_api import ClerkUser
from app.services.permissions.access import resolve_access

logger = logging.getLogger(__name__)

UNIVERSITY_DOMAIN = "qu.edu.sa"


def find_member_for(session: Session, user: ClerkUser) -> Members | None:
    """The member record that is this Clerk user's, or None when they have none yet."""
    member = member_queries.get_member_by_clerk_user_id_or_none(session, user.id)
    if member is not None:
        return member

    if user.uni_id:
        member = member_queries.get_member_by_uni_id_or_none(session, user.uni_id)
        if member is not None:
            if member.clerk_user_id is not None:
                raise MemberLinkedToAnotherLogin(member.id)
            return member

    for email in user.proven_emails:
        member = member_queries.get_unlinked_member_by_email_or_none(session, email)
        if member is not None and not (user.uni_id and member.uni_id and member.uni_id != user.uni_id):
            return member
    return None


def _lock_for(session: Session, member: Members, user: ClerkUser) -> Members:
    """Lock the record this call is about to write, and check it is still this sign-in's to write."""
    linked_to_caller = member.clerk_user_id == user.id
    locked = member_queries.lock_member(session, member.id)
    if locked is None or locked.deleted_at is not None:
        if linked_to_caller:
            raise NotSignedIn("This account was deleted")
        raise MemberLinkedToAnotherLogin(member.id)
    if locked.clerk_user_id not in (None, user.id):
        raise MemberLinkedToAnotherLogin(locked.id)
    if locked.uni_id is not None and user.uni_id is not None and locked.uni_id != user.uni_id:
        raise AccountRecordNotProven(locked.id)
    return locked


def _personal_email(user: ClerkUser, answers: MemberOnboardingRequest) -> str:
    """The personal email, by the Website's rules."""
    typed = str(answers.personal_email).strip() if answers.personal_email else None
    if user.is_university_id_account:
        if typed is None:
            raise InvalidPersonalEmail("personal_email_required", "A personal email is required")
        if typed.lower().rsplit("@", 1)[-1] == UNIVERSITY_DOMAIN:
            raise InvalidPersonalEmail("personal_email_university", "Use a personal email, not the university one")
        return typed

    if typed is None:
        if user.primary_email is None:
            raise InvalidPersonalEmail("personal_email_required", "The account has no verified email; send one")
        if len(user.primary_email) > MAX_EMAIL_LENGTH:
            raise InvalidPersonalEmail("personal_email_invalid", "The account's email is not usable; send one")
        return user.primary_email
    if typed.lower() != user.primary_email and not user.owns_email(typed):
        raise InvalidPersonalEmail("personal_email_not_on_account", "Use an email address that is on this account")
    return typed


def _seed_card(session: Session, member: Members, answers: MemberOnboardingRequest) -> None:
    """The card's academic half, as the Website seeds it right after onboarding."""
    is_new_card = session.scalar(select(MemberProfiles.id).where(MemberProfiles.member_id == member.id)) is None
    institution = {
        "qu_student": answers.uni_college,
        "graduate": answers.uni_college,
        "other_university": answers.university_name,
        "high_school": answers.school_name,
    }[answers.status]
    update_member_profile(
        session,
        member.id,
        theme_id=("gdg-red" if answers.gender == "Female" else "gdg-blue") if is_new_card else None,
        user_status="graduate" if answers.status == "graduate" else "student",
        education_level="highschool" if answers.status == "high_school" else "university",
        institution=institution,
        major=answers.uni_college if answers.status == "other_university" else None,
    )


def _public_metadata(user: ClerkUser, answers: MemberOnboardingRequest, personal_email: str) -> dict[str, Any]:
    """The publicMetadata keys the Website's onboarding writes. ``None`` removes a key."""
    return {
        "uni_id": user.uni_id,
        "fullArabicName": answers.name,
        "saudiPhone": answers.phone_number,
        "gender": answers.gender,
        "memberStatus": answers.status,
        "uniLevel": answers.uni_level,
        "uniCollege": answers.uni_college,
        "highSchoolName": answers.school_name,
        "universityName": answers.university_name,
        "personalEmail": personal_email,
        "onboardingComplete": True,
    }


def onboard(session: Session, clerk_user_id: str, answers: MemberOnboardingRequest) -> tuple[Members, bool]:
    """Create the caller's member record, or link and update the one that is theirs."""
    user = clerk_api.get_user(clerk_user_id)
    personal_email = _personal_email(user, answers)

    member = find_member_for(session, user)
    if member is not None:
        member = _lock_for(session, member, user)
    holder = member_queries.get_member_by_email_or_none(session, personal_email)
    if holder is not None and (member is None or holder.id != member.id):
        raise MemberEmailInUse()

    try:
        data = Member_model(
            name=answers.name,
            email=personal_email,
            phone_number=answers.phone_number,
            uni_id=user.uni_id,
            clerk_user_id=user.id,
            gender=MembersGender(answers.gender),
            uni_level=answers.uni_level,
            uni_college=answers.uni_college,
        )
    except ValidationError as exc:
        # Only the account's own address can get here unchecked: the body was validated already.
        raise InvalidPersonalEmail("personal_email_invalid", "The account's email is not usable; send one") from exc
    already_existed = member is not None
    if member is None:
        member = member_queries.create_member(session, data, is_authenticated=True)
        if member is None:
            # Lost an insert race: a locking read sees the row the other call committed.
            member = member_queries.lock_member_by_clerk_user_id_or_none(session, user.id)
            if member is None:
                raise MemberEmailInUse()
            already_existed = True
    if already_existed:
        data.id = member.id
        member = member_queries.update_member(session, data, is_authenticated=True)

    identify_actor(member)
    public_name_changed = False
    if answers.public_name is not None and answers.public_name != member.public_name:
        member.public_name = answers.public_name
        public_name_changed = True
    _seed_card(session, member, answers)
    session.commit()
    logger.info("Member %s onboarded from the app (%s)", member.id, "linked" if already_existed else "created")

    try:
        still_signed_in = clerk_api.update_public_metadata(user.id, _public_metadata(user, answers, personal_email))
    finally:
        if public_name_changed and already_existed:
            refresh_member_names_cache()
    if not still_signed_in:
        if not already_existed:
            member_queries.anonymize_member(session, member)
            session.commit()
            logger.info("Member %s was created for a sign-in deleted meanwhile; anonymized", member.id)
        raise NotSignedIn("This sign-in no longer exists")
    return member, already_existed


def _carries_proven_identity(user: ClerkUser, member: Members) -> bool:
    """Whether the record carries a university ID or an email address the user proved to Clerk."""
    if member.uni_id is not None and member.uni_id in user.proven_uni_ids:
        return True
    return member.email is not None and user.owns_email(member.email)


def delete_account(
    session: Session, clerk_user_id: str, apple_authorization_code: str | None = None
) -> tuple[bool, bool | None]:
    """Delete the caller's account: anonymize their member record, delete their sign-in, then revoke Apple's tokens.

    Returns whether a member record was anonymized, and whether Apple's tokens
    were revoked (None when no Apple code was sent). Apple is called last, with
    no row lock held, and only once the deletion went through.
    """
    user = clerk_api.get_user(clerk_user_id)
    member = member_queries.lock_member_by_clerk_user_id_or_none(session, clerk_user_id)
    if member is not None:
        identify_actor(member)
        if not _carries_proven_identity(user, member):
            raise AccountRecordNotProven(member.id)
        if resolve_access(session, member).is_staff:
            raise StaffAccountDeletion(member.id)
        member_queries.anonymize_member(session, member)
    # Also when there was no record: it ends the transaction, and the lock, before Clerk is called.
    session.commit()
    if member is not None:
        logger.info("Member %s deleted their account", member.id)

    try:
        if not clerk_api.delete_user(clerk_user_id):
            logger.info("Clerk user %s was already deleted", clerk_user_id)
    finally:
        if member is not None:
            refresh_member_names_cache()

    apple_token_revoked = None
    if apple_authorization_code is not None:
        apple_token_revoked = apple_signin.revoke_tokens(
            apple_authorization_code, user.apple_user_id, f"Clerk user {clerk_user_id}"
        )
    return member is not None, apple_token_revoked
