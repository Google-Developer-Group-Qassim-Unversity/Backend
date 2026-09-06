import logging
import uuid as uuid_lib
from typing import Annotated, Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel, Field

from app.config import config
from app.DB.schema import Members
from app.DB.wallet import (
    get_or_create_member_profile,
    get_public_profile_by_uuid,
    is_member_admin,
    update_member_profile,
)
from app.helpers import MemberOrGuest, authenticated_guard
from app.wallet_signer import generate_apple_pkpass, generate_google_wallet_pass_url
from app.dependencies import DB
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class WalletProfile(BaseModel):
    """The wallet-card half of a member profile.

    `created_at` / `updated_at` are absent from the unregistered-member fallback,
    so they default to None.
    """

    uuid: str | None = None
    custom_name: str | None = None
    theme_id: str | None = None
    name_language: str | None = None
    user_status: str | None = None
    education_level: str | None = None
    institution: str | None = None
    major: str | None = None
    study_year_or_level: str | None = None
    bio: str | None = None
    social_links: list[Any] = []
    visibility: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WalletMeResponse(BaseModel):
    member_id: int | None = None
    name: str
    official_name: str
    custom_name: str | None = None
    uni_id: str | None = None
    email: str | None = None
    phone_number: str | None = None
    gender: str | None = None
    uni_level: int | None = None
    uni_college: str | None = None
    is_admin: bool
    roles: list[str] = []
    profile: WalletProfile


class WalletUpdateResponse(BaseModel):
    success: bool
    name: str
    email: str | None = None
    phone_number: str | None = None
    profile: WalletProfile


class GoogleWalletPassResponse(BaseModel):
    saveUrl: str


class PublicProfileResponse(BaseModel):
    uuid: str
    name: str
    name_language: str | None = None
    theme_id: str | None = None
    user_status: str | None = None
    education_level: str | None = None
    institution: str | None = None
    major: str | None = None
    study_year_or_level: str | None = None
    is_admin: bool
    bio: str | None = None
    social_links: list[Any] = []
    email: str | None = None
    phone: str | None = None
    visibility: dict[str, Any] | None = None
    created_at: str | None = None


class WalletHealthResponse(BaseModel):
    status: str
    apple_wallet_configured: bool
    google_wallet_configured: bool


router = APIRouter(prefix="/wallet", tags=["wallet"])


# =============================================================================
# Request & Response Models
# =============================================================================


class SocialLinkItem(BaseModel):
    id: str
    platform: str
    url: str
    label: Optional[str] = None


class ProfileVisibility(BaseModel):
    showPhone: Optional[bool] = False
    showEmail: Optional[bool] = False
    showAcademic: Optional[bool] = True
    showBio: Optional[bool] = True


class UpdateWalletMePayload(BaseModel):
    custom_name: Optional[str] = Field(default=None, description="Preferred display name on card")
    theme_id: Optional[str] = Field(default=None, description="Theme ID (gdg-blue, gdg-red, gdg-gold-admin)")
    name_language: Optional[str] = Field(default=None, description="Name language label preference: ar or en")
    user_status: Optional[str] = Field(default=None, description="student or graduate")
    education_level: Optional[str] = Field(default=None, description="university or highschool")
    institution: Optional[str] = Field(default=None, description="University or school name")
    major: Optional[str] = Field(default=None, description="Major / field of study")
    study_year_or_level: Optional[str] = Field(default=None, description="Level e.g. المستوى 7")
    bio: Optional[str] = Field(default=None, description="Short member bio")
    social_links: Optional[List[SocialLinkItem]] = Field(default=None, description="List of social links")
    visibility: Optional[ProfileVisibility] = Field(default=None, description="Visibility settings")
    email: Optional[str] = Field(default=None, description="Updated contact email")
    phone_number: Optional[str] = Field(default=None, description="Updated phone number")


# =============================================================================
# Helpers
# =============================================================================


DEFAULT_THEME = "gdg-blue"
ADMIN_THEME = "gdg-gold-admin"


def _permitted_theme(requested: str | None, is_admin: bool) -> str:
    """The gold card is for admins. Anyone else asking for it gets the blue one.

    Written twice before - once against the member's real role, once with the
    answer hardcoded to False on the guest path - which is two places for the
    same rule to be wrong.
    """
    theme_id = requested or DEFAULT_THEME
    if theme_id == ADMIN_THEME and not is_admin:
        return DEFAULT_THEME
    return theme_id


def _member_card_data(session: Session, member: Members, card_data: Dict[str, Any]) -> Dict[str, Any]:
    """A pass for a registered member: the database wins, the payload fills gaps."""
    profile = get_or_create_member_profile(session, member.id)
    session.commit()
    is_admin = is_member_admin(member)

    return {
        "uuid": profile.uuid,
        "fullName": profile.custom_name or card_data.get("fullName") or member.name,
        "uniId": member.uni_id,
        "email": member.email or card_data.get("email", ""),
        "phone": member.phone_number or card_data.get("phone", ""),
        "uniCollege": profile.institution
        or member.uni_college
        or card_data.get("institution")
        or card_data.get("uniCollege", "جامعة القصيم"),
        "major": profile.major or member.uni_college or card_data.get("major", "علوم حاسب"),
        "userStatus": profile.user_status or card_data.get("userStatus", "student"),
        "educationLevel": profile.education_level or card_data.get("educationLevel", "university"),
        "studyYearOrLevel": profile.study_year_or_level or card_data.get("studyYearOrLevel", ""),
        "themeId": _permitted_theme(profile.theme_id or card_data.get("themeId"), is_admin),
        "nameLanguage": "ar",
        "isAdmin": is_admin,
    }


def _guest_card_data(card_data: Dict[str, Any]) -> Dict[str, Any]:
    """A pass for someone with no member row: everything comes from the payload.

    The uuid is generated per request rather than persisted - a guest card is
    not tied to a profile, so it has nothing stable to point at.
    """
    return {
        "uuid": card_data.get("uuid") or uuid_lib.uuid4().hex,
        "fullName": card_data.get("fullName") or "عضو GDG",
        "uniId": card_data.get("uniId", ""),
        "email": card_data.get("email", ""),
        "phone": card_data.get("phone", ""),
        "uniCollege": card_data.get("institution") or card_data.get("uniCollege") or "جامعة القصيم",
        "major": card_data.get("major") or "علوم حاسب",
        "userStatus": card_data.get("userStatus", "student"),
        "educationLevel": card_data.get("educationLevel", "university"),
        "studyYearOrLevel": card_data.get("studyYearOrLevel", ""),
        "themeId": _permitted_theme(card_data.get("themeId"), is_admin=False),
        "nameLanguage": "ar",
        "isAdmin": False,
    }


def _unregistered_wallet_me(credentials) -> Dict[str, Any]:
    """What `/wallet/me` returns for a signed-in caller with no member row.

    Everything the token knows and nothing else. Deliberately a 200: the member
    app shows this as an unregistered card, and a 404 here would read as "the
    endpoint is broken" rather than "you are not registered yet".
    """
    decoded = credentials.model_dump().get("decoded", {})
    return {
        "member_id": None,
        "name": decoded.get("name") or "",
        "official_name": "",
        "custom_name": None,
        "uni_id": decoded.get("metadata", {}).get("uni_id"),
        "email": decoded.get("email") or "",
        "phone_number": None,
        "gender": None,
        "uni_level": None,
        "uni_college": None,
        "is_admin": False,
        "roles": [],
        "profile": {
            "uuid": None,
            "custom_name": None,
            "theme_id": DEFAULT_THEME,
            "name_language": "ar",
            "user_status": "student",
            "education_level": "university",
            "institution": "جامعة القصيم",
            "major": "علوم حاسب",
            "study_year_or_level": "",
            "bio": "",
            "social_links": [],
            "visibility": {"showPhone": False, "showEmail": False, "showAcademic": True, "showBio": True},
        },
    }


def _pass_card_data(session: Session, member: Members | None, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What goes on the card, for a member or for a guest.

    This used to read the Authorization header itself, call the guard by hand
    and wrap the lot in `except Exception: logger.info(...)` - so an expired
    token, a database error and a genuine guest all produced the same blank
    card. Resolving the caller is the `MemberOrGuest` dependency's job now, and
    this only decides what to print.
    """
    card_data = dict(payload or {})
    if member is None:
        return _guest_card_data(card_data)
    return _member_card_data(session, member, card_data)


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/me", summary="Get authenticated member wallet data and profile", response_model=WalletMeResponse)
def get_wallet_me(session: DB, member: MemberOrGuest, credentials=Depends(authenticated_guard)):
    """
    Returns the authenticated member's core info, role permissions, and MemberProfiles settings.

    `authenticated_guard` means a token is required; `MemberOrGuest` means the
    member row is not. Somebody who has just signed up with Clerk has the first
    and not the second, and gets the skeleton below rather than a 404 - the
    member app renders it as an unregistered card.
    """
    if member is None:
        return _unregistered_wallet_me(credentials)

    profile = get_or_create_member_profile(session, member.id)
    session.commit()

    is_admin = is_member_admin(member)
    role_names = [r.role.value for r in member.role] if member.role else []

    effective_name = profile.custom_name or member.name
    effective_institution = profile.institution or member.uni_college or "جامعة القصيم"
    effective_major = profile.major or member.uni_college or "علوم حاسب"
    effective_level = profile.study_year_or_level or (
        f"المستوى {member.uni_level}" if member.uni_level else "عضو مجتمع GDG"
    )

    return {
        "member_id": member.id,
        "name": effective_name,
        "official_name": member.name,
        "custom_name": profile.custom_name,
        "uni_id": member.uni_id,
        "email": member.email,
        "phone_number": member.phone_number,
        "gender": member.gender.value if hasattr(member.gender, "value") else str(member.gender),
        "uni_level": member.uni_level,
        "uni_college": member.uni_college,
        "is_admin": is_admin,
        "roles": role_names,
        "profile": {
            "uuid": profile.uuid,
            "custom_name": profile.custom_name,
            "theme_id": profile.theme_id,
            "name_language": "ar",
            "user_status": profile.user_status or "student",
            "education_level": profile.education_level or "university",
            "institution": effective_institution,
            "major": effective_major,
            "study_year_or_level": effective_level,
            "bio": profile.bio or "",
            "social_links": profile.social_links or [],
            "visibility": profile.visibility
            or {"showPhone": False, "showEmail": False, "showAcademic": True, "showBio": True},
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        },
    }


@router.put("/me", summary="Update member wallet profile settings", response_model=WalletUpdateResponse)
@router.patch("/me", summary="Update member wallet profile settings", response_model=WalletUpdateResponse)
def update_wallet_me(
    payload: UpdateWalletMePayload, session: DB, member: MemberOrGuest, credentials=Depends(authenticated_guard)
):
    """
    Updates the authenticated member's profile and academic fields (custom_name, theme_id, name_language, user_status, education_level, institution, major, study_year_or_level, bio, social_links, visibility).
    Enforces server-side admin role check if gold card (gdg-gold-admin) is requested.

    Unlike the GET above, there is nothing to write to without a member row, so
    this is the one wallet endpoint where not being registered is an error.
    """
    if member is None:
        uni_id = credentials.model_dump().get("decoded", {}).get("metadata", {}).get("uni_id")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"عضو غير مسجل في قاعدة البيانات الأساسية بعد ({uni_id or 'حساب جديد'}).",
        )

    is_admin = is_member_admin(member)

    # Check theme authorization
    if payload.theme_id == ADMIN_THEME and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: Gold Leadership Card is restricted to GDG Administrators and Board Members.",
        )

    social_links_dict = (
        [item.model_dump() for item in payload.social_links] if payload.social_links is not None else None
    )
    visibility_dict = payload.visibility.model_dump() if payload.visibility is not None else None

    if payload.email is not None:
        member.email = str(payload.email)
    if payload.phone_number is not None:
        member.phone_number = payload.phone_number.strip() or None

    updated_profile = update_member_profile(
        session=session,
        member_id=member.id,
        custom_name=payload.custom_name,
        theme_id=payload.theme_id,
        name_language=payload.name_language,
        user_status=payload.user_status,
        education_level=payload.education_level,
        institution=payload.institution,
        major=payload.major,
        study_year_or_level=payload.study_year_or_level,
        bio=payload.bio,
        social_links=social_links_dict,
        visibility=visibility_dict,
    )
    session.commit()

    effective_name = updated_profile.custom_name or member.name

    return {
        "success": True,
        "name": effective_name,
        "email": member.email,
        "phone_number": member.phone_number,
        "profile": {
            "uuid": updated_profile.uuid,
            "custom_name": updated_profile.custom_name,
            "theme_id": updated_profile.theme_id,
            "name_language": updated_profile.name_language.value
            if hasattr(updated_profile.name_language, "value")
            else str(updated_profile.name_language),
            "user_status": updated_profile.user_status,
            "education_level": updated_profile.education_level,
            "institution": updated_profile.institution,
            "major": updated_profile.major,
            "study_year_or_level": updated_profile.study_year_or_level,
            "bio": updated_profile.bio or "",
            "social_links": updated_profile.social_links or [],
            "visibility": updated_profile.visibility,
            "updated_at": updated_profile.updated_at.isoformat() if updated_profile.updated_at else None,
        },
    }


@router.post(
    "/apple-pass", summary="Generate signed Apple Wallet (.pkpass) for member or guest", response_class=Response
)
def create_apple_wallet_pass(
    session: DB, member: MemberOrGuest, payload: Annotated[Optional[Dict[str, Any]], Body()] = None
):
    """
    Generates and signs an official Apple Wallet .pkpass file.
    Supports authenticated members (loading DB record) and guest cards without 401 failures.
    """
    card_data = _pass_card_data(session, member, payload)
    pkpass_bytes = generate_apple_pkpass(card_data)
    file_name = f"gdg-pass-{card_data['uuid'][:8]}.pkpass"

    return Response(
        content=pkpass_bytes,
        media_type="application/vnd.apple.pkpass",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@router.post(
    "/google-pass",
    summary="Generate signed Google Wallet save link for member or guest",
    response_model=GoogleWalletPassResponse,
)
def create_google_wallet_pass(
    session: DB, member: MemberOrGuest, payload: Annotated[Optional[Dict[str, Any]], Body()] = None
):
    """
    Generates a signed Google Wallet save URL.
    Supports authenticated members and guest cards.
    """
    card_data = _pass_card_data(session, member, payload)
    try:
        save_url = generate_google_wallet_pass_url(card_data)
    except ValueError as exc:
        logger.error("Google Wallet configuration error: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return {"saveUrl": save_url}


@router.get(
    "/{uuid}",
    summary="Get public member profile by UUID with strict visibility filtering",
    response_model=PublicProfileResponse,
)
def get_public_profile(uuid: str, session: DB):
    """
    Publicly accessible endpoint for /p/{uuid}.
    Returns only the permitted profile and academic fields according to the owner's visibility preferences.
    """
    result = get_public_profile_by_uuid(session, uuid)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    member, profile, is_admin = result

    vis = profile.visibility or {}
    show_phone = bool(vis.get("showPhone", False))
    show_email = bool(vis.get("showEmail", False))
    show_academic = bool(vis.get("showAcademic", True))
    show_bio = bool(vis.get("showBio", True))

    effective_name = profile.custom_name or member.name
    effective_institution = profile.institution or member.uni_college or "جامعة القصيم"
    effective_major = profile.major or member.uni_college or "علوم حاسب"
    effective_level = profile.study_year_or_level or (
        f"المستوى {member.uni_level}" if member.uni_level else "عضو مجتمع GDG"
    )

    return {
        "uuid": profile.uuid,
        "name": effective_name,
        "name_language": profile.name_language.value
        if hasattr(profile.name_language, "value")
        else str(profile.name_language),
        "theme_id": profile.theme_id,
        "user_status": profile.user_status or "student",
        "education_level": profile.education_level or "university",
        "institution": effective_institution if show_academic else None,
        "major": effective_major if show_academic else None,
        "study_year_or_level": effective_level if show_academic else None,
        "is_admin": is_admin,
        "bio": (profile.bio or "") if show_bio else None,
        "social_links": profile.social_links or [],
        "email": member.email if show_email else None,
        "phone": member.phone_number if show_phone else None,
        "visibility": {
            "showPhone": show_phone,
            "showEmail": show_email,
            "showAcademic": show_academic,
            "showBio": show_bio,
        },
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


@router.get("/health", summary="Check Wallet Pass engine health", response_model=WalletHealthResponse)
def wallet_health():
    """
    Returns the status of the Wallet signing engine and available credentials.
    """
    has_apple_p12 = bool(config.APPLE_P12_BASE64) or bool(config.APPLE_P12_PASSWORD)
    has_google_key = bool(config.GOOGLE_WALLET_PRIVATE_KEY)
    return {"status": "healthy", "apple_wallet_configured": has_apple_p12, "google_wallet_configured": has_google_key}
