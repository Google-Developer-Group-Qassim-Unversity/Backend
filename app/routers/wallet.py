import json
import logging
import os
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.wallet_signer import generate_apple_pkpass, generate_google_wallet_pass_url

logger = logging.getLogger(__name__)

router = APIRouter()


class WalletCardPayload(BaseModel):
    uuid: Optional[str] = Field(default=None, description="Unique Identifier of the Card")
    fullName: str = Field(..., description="Full Name of the member")
    englishName: Optional[str] = Field(default="", description="Member name in English")
    countryCode: Optional[str] = Field(default="+966")
    phone: Optional[str] = Field(default="")
    email: Optional[str] = Field(default="")
    themeId: Optional[str] = Field(default="gdg-blue")
    userStatus: Optional[str] = Field(default="")
    educationLevel: Optional[str] = Field(default="")
    institution: Optional[str] = Field(default="")
    major: Optional[str] = Field(default="")
    studyYearOrLevel: Optional[str] = Field(default="")
    adminPasscode: Optional[str] = Field(default=None, description="Passcode required for Admin Gold Card")


def verify_admin_card_authorization(payload: WalletCardPayload, request: Request):
    """
    Enforces authorization check if the client requests the restricted Admin Gold Card.
    """
    if payload.themeId != "gdg-gold-admin":
        return

    # Check 1: Header / Token
    auth_header = request.headers.get("x-admin-passcode") or request.headers.get("Authorization")
    provided_code = payload.adminPasscode or auth_header or ""

    configured_passcode = os.getenv("ADMIN_WALLET_PASSCODE", "").strip().upper()
    if not configured_passcode:
        logger.error("ADMIN_WALLET_PASSCODE is not configured; refusing an Admin Gold Card request")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin Gold Card issuance is not configured."
        )

    cleaned = str(provided_code).replace("Bearer ", "").strip().upper()
    if cleaned != configured_passcode:
        logger.warning(f"Unauthorized attempt to generate Admin Gold Card for: {payload.fullName}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: Only GDG Board and Administrators can issue the Gold Leadership Card.",
        )


@router.post("/apple-pass", summary="Generate signed Apple Wallet (.pkpass) file")
async def create_apple_wallet_pass(payload: WalletCardPayload, request: Request):
    """
    Creates and signs an official Apple Wallet .pkpass file using the Backend crypto engine.
    """
    verify_admin_card_authorization(payload, request)

    try:
        card_data = payload.model_dump()
        pkpass_bytes = generate_apple_pkpass(card_data)
        file_name = f"gdg-card-{payload.uuid or 'pass'}.pkpass"

        return Response(
            content=pkpass_bytes,
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to sign Apple Wallet pass: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate Apple Wallet pass: {str(e)}")


@router.post("/google-pass", summary="Generate signed Google Wallet save link")
async def create_google_wallet_pass(payload: WalletCardPayload, request: Request):
    """
    Creates a cryptographically signed Google Wallet save URL using RS256 JWT.
    """
    verify_admin_card_authorization(payload, request)

    try:
        card_data = payload.model_dump()
        save_url = generate_google_wallet_pass_url(card_data)
        return {"saveUrl": save_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to sign Google Wallet pass: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate Google Wallet link: {str(e)}")


@router.get("/health", summary="Check Wallet Pass engine health")
async def wallet_health():
    """
    Returns the status of the Wallet signing engine and available credentials.
    """
    has_apple_p12 = bool(os.getenv("APPLE_P12_BASE64")) or bool(os.getenv("APPLE_P12_PASSWORD"))
    has_google_key = bool(os.getenv("GOOGLE_WALLET_PRIVATE_KEY"))
    return {"status": "healthy", "apple_wallet_configured": has_apple_p12, "google_wallet_configured": has_google_key}
