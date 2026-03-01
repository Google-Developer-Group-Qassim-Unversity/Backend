from fastapi import Depends, HTTPException, HTTPException, status
from app.config import config
from app.routers.models import Member_model
from typing import List, Union, Optional
import pandas as pd
from pydantic import HttpUrl
from json import dumps
import jwt
from datetime import datetime


def get_pydantic_members(source: Union[str, HttpUrl]):
    if isinstance(source, HttpUrl):
        print(f"[2] Got link: \x1b[33m{source}\x1b[0m")
        df = pd.read_csv(source.__str__())
    else:
        print(f"[2] Got file: \x1b[33m{source}\x1b[0m")
        df = pd.read_excel(f"uploads/{source}")

    members: List[Member_model] = []

    # Get column names
    df.columns = df.columns.str.strip()
    columns = df.columns.tolist()
    print(f"[2] Columns found: \x1b[36m{columns}\x1b[0m")
    print(f"[2] Date columns found: \x1b[36m{columns[5:]}\x1b[0m")

    # Iterate through DataFrame rows
    for _, row in df.iterrows():
        member = Member_model(
            name=row.get("name"),
            email=str(row.get("email")),
            phone_number=None if pd.isna(row.get("phone number")) or row.get("phone number") == "" else str(row.get("phone number")),
            uni_id=str(row.get("uni id")),
            gender=row.get("gender"),
            uni_level=0 if pd.isna(row.get("uni level")) or row.get("uni level") == "" else int(row.get("uni level")),
            uni_college="unknown" if pd.isna(row.get("uni college")) or row.get("uni college") == "" else str(row.get("uni college")),
        )

        members.append(member)

    source_type = "URL" if isinstance(source, HttpUrl) else "file"
    print(f"[2] Extracted \x1b[32m{len(members)}\x1b[0m members from {source_type}")
    return members


def get_uni_id_from_credentials(credentials):
    decoded = credentials.model_dump()["decoded"]
    assert "metadata" in decoded, "Decoded credentials missing 'metadata'"
    assert "uni_id" in decoded["metadata"], "Decoded credentials metadata missing 'uni_id'"
    # print("Got decoded credentials 🔒:")
    # print(dumps(credentials.model_dump(), ensure_ascii=False, indent=4))
    uni_id: str = str(decoded["metadata"]["uni_id"])
    return uni_id


def is_admin(credentials) -> bool:
    decoded = credentials.model_dump()["decoded"]
    metadata = decoded.get("metadata", {})
    return metadata.get("is_admin", False) or metadata.get("is_super_admin", False)


def is_super_admin(credentials) -> bool:
    decoded = credentials.model_dump()["decoded"]
    metadata = decoded.get("metadata", {})
    return metadata.get("is_super_admin", False)


def admin_guard(credentials=Depends(config.CLERK_GUARD)):
    print("🔒 User authenticated, checking admin privileges...")
    if not is_admin(credentials):
        metadata = credentials.model_dump().get("decoded", {}).get("metadata", {})
        print(f"🚫 Access Denied! User Metadata: {metadata}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return credentials


def super_admin_guard(credentials=Depends(config.CLERK_GUARD)):
    print("🔒 User authenticated, checking super 🦸‍♂ admin privileges...")
    if not is_super_admin(credentials):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    return credentials


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
        print(f"Invalid credentials structure:\n{credentials_str}")
        raise ValueError("Invalid credentials: 'decoded' or 'metadata' missing")

    # 2. create Member_model from metadata
    metadata = credentials_dict["decoded"]["metadata"]
    member = Member_model(
        name=metadata.get("fullArabicName"),
        email=metadata.get("personalEmail"),
        phone_number=metadata.get("saudiPhone"),
        uni_id=metadata.get("uni_id"),
        gender=metadata.get("gender").title(),
        uni_level=metadata.get("uniLevel"),
        uni_college=metadata.get("uniCollege"),
    )
    return member


def validate_attendance_token(token: str, expected_event_id: int) -> dict:
    try:
        # 1. Decode & Verify
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "eventId"]},
        )

        # 2. Extract Data
        token_event_id = payload.get("eventId")

        if int(token_event_id) != int(expected_event_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token event ID does not match the requested event",
            )

        return {"valid": True, "event_id": token_event_id, "payload": payload}

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,  # 401 is usually better for expired tokens
            detail="رابط الحضور هذا منتهي الصلاحية. الرجاء التواصل مع المنظم للحصول على رابط جديد.",
        )
    except jwt.MissingRequiredClaimError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token missing required claim: {e.claim}",
        )

    # More specific "invalid token" causes:
    except jwt.InvalidSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid attendance token signature",
        )

    except jwt.InvalidAlgorithmError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid attendance token algorithm",
        )

    except jwt.DecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed attendance token",
        )

    except jwt.ImmatureSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Attendance token not yet valid",
        )

    except jwt.InvalidTokenError as e:
        # Catch-all for anything else JWT-related
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid attendance token ({type(e).__name__})",
        )


if __name__ == "__main__":
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTI-xnfTaaEhNO4G4Vx1dJejKq2kDtHSi5yWtcrFGNfKJJxqRvIpBXk2_M9dxDc49NrDY-dD5SiJ6pR/pub?gid=1781104695&single=true&output=csv"
    members = get_pydantic_members(url)
    print(f"done ✅ got {len(members)} members")
