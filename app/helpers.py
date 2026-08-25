import logging
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Annotated
from app.config import config
from app.dependencies import DB
from app.routers.models import Member_model
from app.DB.schema import Members
from app.DB import members as member_queries
from app.exceptions import MemberNotFound
from json import dumps
import jwt
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)


def get_effective_date(dt: datetime, threshold: int) -> date:
    """
    Get the intended/effective date for attendance purposes.

    Times in early hours (00:00 to threshold-1) are considered part of the
    previous day. This handles midnight-crossing events where late-night
    attendance should count as the "intended" day.

    Args:
        dt: The datetime to convert
        threshold: Hour threshold (0-23). Hours < threshold shift to previous day.

    Returns:
        The effective date.

    Example:
        With threshold=6:
        - Mar 3, 2:00 AM → effective date is Mar 2
        - Mar 3, 8:00 AM → effective date is Mar 3"""
    if dt.hour < threshold:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def get_uni_id_from_credentials(credentials):
    decoded = credentials.model_dump()["decoded"]
    assert "metadata" in decoded, "Decoded credentials missing 'metadata'"
    assert "uni_id" in decoded["metadata"], "Decoded credentials metadata missing 'uni_id'"
    # print("Got decoded credentials 🔒:")
    # print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    uni_id: str = str(decoded["metadata"]["uni_id"])
    return uni_id


def get_clerk_user_id_from_credentials(credentials) -> str:
    """The Clerk JWT ``sub`` claim - the stable, provider-agnostic user id.

    Unlike ``uni_id`` (only present for password/uni_id signups), this is
    always present on every Clerk-issued token regardless of sign-in method.
    """
    decoded = credentials.model_dump()["decoded"]
    assert "sub" in decoded, "Decoded credentials missing 'sub'"
    return str(decoded["sub"])


def resolve_member(session: Session, credentials) -> Members:
    """Resolve the ``Members`` row for the currently authenticated caller.

    Looks up by ``clerk_user_id`` first (works for every member once they've
    authenticated at least once after this identity model was introduced).
    Falls back to ``uni_id`` for members who haven't re-authenticated yet,
    self-healing their ``clerk_user_id`` in the process.
    """
    clerk_user_id = get_clerk_user_id_from_credentials(credentials)
    member = member_queries.get_member_by_clerk_user_id_or_none(session, clerk_user_id)
    if member:
        return member

    decoded = credentials.model_dump()["decoded"]
    uni_id = decoded.get("metadata", {}).get("uni_id")
    if uni_id:
        member = member_queries.get_member_by_uni_id_or_none(session, str(uni_id))
        if member:
            return member_queries.set_member_clerk_user_id(session, member, clerk_user_id)

    raise MemberNotFound(clerk_user_id)


def is_admin(credentials) -> bool:
    decoded = credentials.model_dump()["decoded"]
    metadata = decoded.get("metadata", {})
    return (
        metadata.get("is_admin", False)
        or metadata.get("is_super_admin", False)
        or metadata.get("is_admin_points", False)
    )


def is_admin_points(credentials) -> bool:
    decoded = credentials.model_dump()["decoded"]
    metadata = decoded.get("metadata", {})
    return metadata.get("is_admin_points", False) or metadata.get("is_super_admin", False)


def is_super_admin(credentials) -> bool:
    decoded = credentials.model_dump()["decoded"]
    metadata = decoded.get("metadata", {})
    return metadata.get("is_super_admin", False)


def authenticated_guard(credentials=Depends(config.CLERK_GUARD)):
    return credentials


def optional_clerk_guard(credentials=Depends(config.CLERK_GUARD_optional)):
    return credentials


def admin_guard(credentials=Depends(config.CLERK_GUARD)):
    if not is_admin(credentials):
        metadata = credentials.model_dump().get("decoded", {}).get("metadata", {})
        logger.warning("Access denied, user metadata: %s", metadata)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return credentials


def admin_points_guard(credentials=Depends(config.CLERK_GUARD)):
    if not is_admin_points(credentials):
        metadata = credentials.model_dump().get("decoded", {}).get("metadata", {})
        logger.warning("Access denied, user metadata: %s", metadata)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin Points privileges required")
    return credentials


def super_admin_guard(credentials=Depends(config.CLERK_GUARD)):
    if not is_super_admin(credentials):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return credentials


def get_current_member(session: DB, credentials=Depends(authenticated_guard)) -> Members:
    """The ``Members`` row for the authenticated caller.

    Wraps the ``authenticated_guard`` + ``resolve_member`` pair that a route
    would otherwise repeat by hand. Like ``resolve_member`` it only flushes the
    ``clerk_user_id`` self-heal; a route that wants it persisted still commits.
    """
    return resolve_member(session, credentials)


CurrentMember = Annotated[Members, Depends(get_current_member)]


def credentials_to_member_model(credentials) -> Member_model:
    """Convert Clerk credentials into the internal ``Member_model``.

    Returns:
        Member_model: A populated ``Member_model`` instance.

    Raises:
        ValueError: If the credentials are invalid or missing required fields.

    Notes:
        Current metadata fields set in clerk's publicMetadata by the authenticated repository:

        - ``uni_id`` (str)
        - ``fullArabicName`` (str)
        - ``saudiPhone`` (str)
        - ``gender`` (Literal["Male", "Female"])
        - ``uniLevel`` (int)
        - ``uniCollege`` (str)
        - ``personalEmail`` (str)
    """

    # 1. decode and validate metadata from credentials
    credentials_dict = credentials.model_dump()
    credentials_str = dumps(credentials.model_dump(), ensure_ascii=False, indent=4)
    if not credentials_dict["decoded"]["metadata"]:
        logger.error("Invalid credentials structure: %s", credentials_str)
        raise ValueError("Invalid credentials: 'decoded' or 'metadata' missing")

    # 2. create Member_model from metadata
    metadata = credentials_dict["decoded"]["metadata"]
    member = Member_model(
        name=metadata.get("fullArabicName"),
        email=metadata.get("personalEmail"),
        phone_number=metadata.get("saudiPhone"),
        uni_id=metadata.get("uni_id"),
        clerk_user_id=str(credentials_dict["decoded"]["sub"]),
        gender=metadata.get("gender").title(),
        uni_level=metadata.get("uniLevel"),
        uni_college=metadata.get("uniCollege"),
    )
    return member


def validate_attendance_token(token: str, expected_event_id: int) -> dict:
    try:
        # 1. Decode & Verify
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "eventId"]})

        # 2. Extract Data
        token_event_id = payload.get("eventId")

        if int(token_event_id) != int(expected_event_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Token event ID does not match the requested event"
            )

        return {"valid": True, "event_id": token_event_id, "payload": payload}

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,  # 401 is usually better for expired tokens
            detail="رابط الحضور هذا منتهي الصلاحية. الرجاء التواصل مع المنظم للحصول على رابط جديد.",
        )
    except jwt.MissingRequiredClaimError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Token missing required claim: {e.claim}")

    # More specific "invalid token" causes:
    except jwt.InvalidSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid attendance token signature")

    except jwt.InvalidAlgorithmError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attendance token algorithm")

    except jwt.DecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed attendance token")

    except jwt.ImmatureSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Attendance token not yet valid")

    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid attendance token ({type(e).__name__})"
        )
