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


def get_email_from_credentials(credentials) -> str | None:
    """The caller's email, from the three places a Clerk token has carried it.

    ``metadata.email`` is publicMetadata - written by the auth app rather than
    verified by Clerk - so it is tried last, after the two claims Clerk issues
    itself. It stays in the chain because members whose row predates the
    top-level claim are only findable through it.
    """
    decoded = credentials.model_dump().get("decoded", {})
    return decoded.get("email") or decoded.get("primary_email_address") or decoded.get("metadata", {}).get("email")


def resolve_member(session: Session, credentials) -> Members:
    """Resolve the ``Members`` row for the currently authenticated caller.

    One chain, ordered by how much each claim is worth:

    1. ``clerk_user_id`` - the token's ``sub``. On every Clerk token, and the
       only one of the three that cannot drift.
    2. ``uni_id`` from publicMetadata, for members who have not authenticated
       since this identity model was introduced.
    3. the email claim, for a member whose row was created by an admin before
       they ever signed in, so it carries neither of the first two.

    A hit on 2 or 3 writes ``clerk_user_id`` back, so the next request takes the
    first branch and the fallbacks decay into dead weight rather than a
    permanent cost.

    Lookup failures are not swallowed. "This caller is not a member" and "the
    database is unreachable" are different answers, and the wallet router used
    to have its own copy of this that reported both as the first.
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

    email = get_email_from_credentials(credentials)
    if email:
        member = member_queries.get_member_by_email_or_none(session, str(email))
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


def get_member_or_none(session: DB, credentials=Depends(optional_clerk_guard)) -> Members | None:
    """The caller's ``Members`` row, or ``None`` when there is not one.

    Two different situations collapse into ``None`` on purpose - nobody is
    signed in, and somebody is signed in but has no member row yet - because
    every caller of this treats them the same way: show the guest version.
    A route that needs to tell them apart pairs this with
    ``Depends(authenticated_guard)``, which rejects the anonymous case before
    the handler runs.

    ``MemberNotFound`` is the only exception caught. Anything else - a database
    error, a malformed token that somehow got past the guard - propagates, so a
    broken lookup cannot quietly present an admin as a guest.
    """
    if credentials is None:
        return None
    try:
        return resolve_member(session, credentials)
    except MemberNotFound:
        return None


MemberOrGuest = Annotated[Members | None, Depends(get_member_or_none)]


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
