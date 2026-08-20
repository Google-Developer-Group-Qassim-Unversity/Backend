import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.DB.schema import MemberProfiles, MemberProfilesNameLanguage, Members, Role, RoleType


def get_member_by_uni_id_or_none(session: Session, uni_id: str) -> Optional[Members]:
    """Finds a member by uni_id, with roles and profile preloaded."""
    stmt = (
        select(Members)
        .options(joinedload(Members.role), joinedload(Members.profile))
        .where(Members.uni_id == uni_id)
    )
    return session.scalars(stmt).first()


def get_member_by_email_or_none(session: Session, email: str) -> Optional[Members]:
    """Finds a member by email fallback, with roles and profile preloaded."""
    stmt = (
        select(Members)
        .options(joinedload(Members.role), joinedload(Members.profile))
        .where(Members.email == email)
    )
    return session.scalars(stmt).first()


def is_member_admin(member: Members) -> bool:
    """Checks if a member has admin or super_admin privileges in the database."""
    if not member or not member.role:
        return False
    return any(r.role in (RoleType.ADMIN, RoleType.SUPER_ADMIN) for r in member.role)


def get_or_create_member_profile(session: Session, member_id: int) -> MemberProfiles:
    """
    Retrieves the existing MemberProfiles for a member, or creates one with a permanent unique UUID.
    """
    stmt = select(MemberProfiles).where(MemberProfiles.member_id == member_id)
    profile = session.scalars(stmt).first()

    if profile:
        return profile

    new_uuid = uuid.uuid4().hex
    default_visibility = {
        "showPhone": False,
        "showEmail": False,
        "showAcademic": True,
        "showBio": True,
        "academicConfigured": False,
    }

    new_profile = MemberProfiles(
        member_id=member_id,
        uuid=new_uuid,
        custom_name=None,
        theme_id="gdg-blue",
        name_language=MemberProfilesNameLanguage.AR,
        user_status=None,
        education_level=None,
        institution=None,
        major=None,
        study_year_or_level=None,
        bio="",
        social_links=[],
        visibility=default_visibility,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    session.add(new_profile)
    session.flush()
    return new_profile


def get_public_profile_by_uuid(session: Session, profile_uuid: str) -> Optional[Tuple[Members, MemberProfiles, bool]]:
    """
    Retrieves a member and their profile by public UUID.
    Returns (member, profile, is_admin) or None if not found.
    """
    stmt = (
        select(MemberProfiles)
        .options(joinedload(MemberProfiles.member).joinedload(Members.role))
        .where(MemberProfiles.uuid == profile_uuid)
    )
    profile = session.scalars(stmt).first()
    if not profile or not profile.member:
        return None

    member = profile.member
    is_admin = is_member_admin(member)
    return member, profile, is_admin


def update_member_profile(
    session: Session,
    member_id: int,
    custom_name: Optional[str] = None,
    theme_id: Optional[str] = None,
    name_language: Optional[str] = None,
    user_status: Optional[str] = None,
    education_level: Optional[str] = None,
    institution: Optional[str] = None,
    major: Optional[str] = None,
    study_year_or_level: Optional[str] = None,
    bio: Optional[str] = None,
    social_links: Optional[list] = None,
    visibility: Optional[dict] = None,
) -> MemberProfiles:
    """
    Updates the profile and academic fields for a member.
    """
    profile = get_or_create_member_profile(session, member_id)

    if custom_name is not None:
        profile.custom_name = custom_name.strip() if custom_name else None

    if theme_id is not None:
        profile.theme_id = theme_id

    if name_language is not None:
        lang_str = str(name_language).lower()
        if lang_str in ("ar", "en"):
            profile.name_language = MemberProfilesNameLanguage.AR if lang_str == "ar" else MemberProfilesNameLanguage.EN

    if user_status is not None:
        profile.user_status = user_status

    if education_level is not None:
        profile.education_level = education_level

    if institution is not None:
        profile.institution = institution

    if major is not None:
        profile.major = major

    if study_year_or_level is not None:
        profile.study_year_or_level = study_year_or_level

    if bio is not None:
        profile.bio = bio

    if social_links is not None:
        profile.social_links = social_links

    if visibility is not None:
        current_vis = dict(profile.visibility or {})
        current_vis.update(visibility)
        profile.visibility = current_vis

    if user_status in ("student", "graduate"):
        current_vis = dict(profile.visibility or {})
        current_vis["academicConfigured"] = True
        profile.visibility = current_vis

    profile.updated_at = datetime.now()
    session.flush()
    return profile
