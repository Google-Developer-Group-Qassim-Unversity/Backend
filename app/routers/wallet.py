import logging
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import config
from app.DB.main import SessionLocal
from app.DB.wallet import (
    get_member_by_uni_id_or_none,
    get_member_by_email_or_none,
    get_or_create_member_profile,
    get_public_profile_by_uuid,
    is_member_admin,
    update_member_profile,
)
from app.helpers import authenticated_guard, get_uni_id_from_credentials
from app.wallet_signer import generate_apple_pkpass, generate_google_wallet_pass_url

logger = logging.getLogger(__name__)

router = APIRouter()


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
    theme_id: Optional[str] = Field(default=None, description="Theme ID (gdg-blue, gdg-red, gdg-gold-admin)")
    name_language: Optional[str] = Field(default=None, description="Name language label preference: ar or en")
    bio: Optional[str] = Field(default=None, description="Short member bio")
    social_links: Optional[List[SocialLinkItem]] = Field(default=None, description="List of social links")
    visibility: Optional[ProfileVisibility] = Field(default=None, description="Visibility settings")


# =============================================================================
# Helpers
# =============================================================================

def _resolve_authenticated_member(session, credentials):
    """
    Safely resolves the authenticated Member from Clerk credentials
    using uni_id extracted directly by get_uni_id_from_credentials.
    """
    try:
        uni_id = get_uni_id_from_credentials(credentials)
    except Exception as e:
        logger.warning(f"Failed to extract uni_id from Clerk credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Clerk token missing uni_id student identifier in metadata.",
        )

    member = get_member_by_uni_id_or_none(session, str(uni_id))
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member with university ID '{uni_id}' not found in system.",
        )

    return member


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/me", summary="Get authenticated member wallet data and profile")
def get_wallet_me(credentials=Depends(authenticated_guard)):
    """
    Returns the authenticated member's core info, role permissions, and MemberProfiles settings.
    """
    with SessionLocal() as session:
        member = _resolve_authenticated_member(session, credentials)
        profile = get_or_create_member_profile(session, member.id)
        session.commit()

        is_admin = is_member_admin(member)
        role_names = [r.role.value for r in member.role] if member.role else []

        return {
            "member_id": member.id,
            "name": member.name,
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
                "theme_id": profile.theme_id,
                "name_language": profile.name_language.value if hasattr(profile.name_language, "value") else str(profile.name_language),
                "bio": profile.bio or "",
                "social_links": profile.social_links or [],
                "visibility": profile.visibility or {
                    "showPhone": False,
                    "showEmail": False,
                    "showAcademic": True,
                    "showBio": True,
                },
                "created_at": profile.created_at.isoformat() if profile.created_at else None,
                "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            },
        }


@router.put("/me", summary="Update member wallet profile settings")
@router.patch("/me", summary="Update member wallet profile settings")
def update_wallet_me(payload: UpdateWalletMePayload, credentials=Depends(authenticated_guard)):
    """
    Updates the authenticated member's mutable profile fields (theme_id, name_language, bio, social_links, visibility).
    Enforces server-side admin role check if gold card (gdg-gold-admin) is requested.
    """
    with SessionLocal() as session:
        member = _resolve_authenticated_member(session, credentials)
        is_admin = is_member_admin(member)

        # Check theme authorization
        if payload.theme_id == "gdg-gold-admin" and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized: Gold Leadership Card is restricted to GDG Administrators and Board Members.",
            )

        social_links_dict = [item.model_dump() for item in payload.social_links] if payload.social_links is not None else None
        visibility_dict = payload.visibility.model_dump() if payload.visibility is not None else None

        updated_profile = update_member_profile(
            session=session,
            member_id=member.id,
            theme_id=payload.theme_id,
            name_language=payload.name_language,
            bio=payload.bio,
            social_links=social_links_dict,
            visibility=visibility_dict,
        )
        session.commit()

        return {
            "success": True,
            "profile": {
                "uuid": updated_profile.uuid,
                "theme_id": updated_profile.theme_id,
                "name_language": updated_profile.name_language.value if hasattr(updated_profile.name_language, "value") else str(updated_profile.name_language),
                "bio": updated_profile.bio or "",
                "social_links": updated_profile.social_links or [],
                "visibility": updated_profile.visibility,
                "updated_at": updated_profile.updated_at.isoformat() if updated_profile.updated_at else None,
            },
        }


@router.post("/apple-pass", summary="Generate signed Apple Wallet (.pkpass) for authenticated member")
def create_apple_wallet_pass(credentials=Depends(authenticated_guard)):
    """
    Generates and signs an official Apple Wallet .pkpass file by joining member and member_profiles directly from DB.
    """
    with SessionLocal() as session:
        member = _resolve_authenticated_member(session, credentials)
        profile = get_or_create_member_profile(session, member.id)
        session.commit()

        is_admin = is_member_admin(member)
        theme_id = profile.theme_id

        # Enforce admin check for gold theme
        if theme_id == "gdg-gold-admin" and not is_admin:
            theme_id = "gdg-blue"

        card_data = {
            "uuid": profile.uuid,
            "fullName": member.name,
            "uniId": member.uni_id,
            "email": member.email or "",
            "phone": member.phone_number or "",
            "uniCollege": member.uni_college or "جامعة القصيم",
            "themeId": theme_id,
            "nameLanguage": profile.name_language.value if hasattr(profile.name_language, "value") else str(profile.name_language),
            "isAdmin": is_admin,
        }

        pkpass_bytes = generate_apple_pkpass(card_data)
        file_name = f"gdg-pass-{profile.uuid[:8]}.pkpass"

        return Response(
            content=pkpass_bytes,
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )


@router.post("/google-pass", summary="Generate signed Google Wallet save link for authenticated member")
def create_google_wallet_pass(credentials=Depends(authenticated_guard)):
    """
    Generates a signed Google Wallet save URL by joining member and member_profiles directly from DB.
    """
    with SessionLocal() as session:
        member = _resolve_authenticated_member(session, credentials)
        profile = get_or_create_member_profile(session, member.id)
        session.commit()

        is_admin = is_member_admin(member)
        theme_id = profile.theme_id

        if theme_id == "gdg-gold-admin" and not is_admin:
            theme_id = "gdg-blue"

        card_data = {
            "uuid": profile.uuid,
            "fullName": member.name,
            "uniId": member.uni_id,
            "email": member.email or "",
            "phone": member.phone_number or "",
            "uniCollege": member.uni_college or "جامعة القصيم",
            "themeId": theme_id,
            "nameLanguage": profile.name_language.value if hasattr(profile.name_language, "value") else str(profile.name_language),
            "isAdmin": is_admin,
        }

        save_url = generate_google_wallet_pass_url(card_data)
        return {"saveUrl": save_url}


@router.get("/{uuid}", summary="Get public member profile by UUID with strict visibility filtering")
def get_public_profile(uuid: str):
    """
    Publicly accessible endpoint for /p/{uuid}.
    Returns only the permitted profile and academic fields according to the owner's visibility preferences.
    """
    with SessionLocal() as session:
        result = get_public_profile_by_uuid(session, uuid)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

        member, profile, is_admin = result

        vis = profile.visibility or {}
        show_phone = bool(vis.get("showPhone", False))
        show_email = bool(vis.get("showEmail", False))
        show_academic = bool(vis.get("showAcademic", True))
        show_bio = bool(vis.get("showBio", True))

        return {
            "uuid": profile.uuid,
            "name": member.name,
            "name_language": profile.name_language.value if hasattr(profile.name_language, "value") else str(profile.name_language),
            "theme_id": profile.theme_id,
            "is_admin": is_admin,
            "bio": (profile.bio or "") if show_bio else None,
            "social_links": profile.social_links or [],
            "email": member.email if show_email else None,
            "phone": member.phone_number if show_phone else None,
            "uni_college": member.uni_college if show_academic else None,
            "uni_level": member.uni_level if show_academic else None,
            "visibility": {
                "showPhone": show_phone,
                "showEmail": show_email,
                "showAcademic": show_academic,
                "showBio": show_bio,
            },
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }


@router.get("/health", summary="Check Wallet Pass engine health")
async def wallet_health():
    """
    Returns the status of the Wallet signing engine and available credentials.
    """
    has_apple_p12 = bool(os.getenv("APPLE_P12_BASE64")) or bool(os.getenv("APPLE_P12_PASSWORD"))
    has_google_key = bool(os.getenv("GOOGLE_WALLET_PRIVATE_KEY"))
    return {
        "status": "healthy",
        "apple_wallet_configured": has_apple_p12,
        "google_wallet_configured": has_google_key,
    }
