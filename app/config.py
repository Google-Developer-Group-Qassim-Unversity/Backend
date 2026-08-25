"""
All environment variables and global configuration should be accessed through this module.
"""

from dotenv import load_dotenv
import os
from functools import lru_cache
from typing import Optional
from pathlib import Path
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer
from enum import Enum

if os.getenv("ENV") != "testing":
    load_dotenv(".env.local", override=True)

LOG_DIR_DEV = "logs"
LOG_DIR_PROD = str(Path.home() / "GDG-Logs")

# Semesters are no longer configured here - they live in the `semesters` table, see app/DB/semesters.py

ATTENDANCE_EARLY_HOURS_THRESHOLD = 6

CLUB_EMAIL_THRESHOLD = 500

EMAIL_THRESHOLDS: dict[str, int] = {"info@kerneltics.com": 2000, "gdg.qu1@gmail.com": 500}


class Config:
    @property
    def is_dev(self) -> bool:
        env = env_or_except("ENV", "Production")
        return env.lower() == "development"

    @property
    def DATABASE_URL(self) -> str:
        return env_or_except("DATABASE_URL")

    @property
    def CLERK_GUARD(self):
        return _clerk_bearer(auto_error=True)

    @property
    def CLERK_GUARD_optional(self):
        return _clerk_bearer(auto_error=False)

    @property
    def LOG_DIR(self) -> str:
        if self.is_dev:
            os.makedirs(LOG_DIR_DEV, exist_ok=True)
            return LOG_DIR_DEV
        else:
            os.makedirs(LOG_DIR_PROD, exist_ok=True)
            return LOG_DIR_PROD

    @property
    def SES_FROM_ADDRESS(self) -> str:
        return env_or_except("SES_FROM_ADDRESS")

    @property
    def CLUB_EMAIL_THRESHOLD(self) -> int:
        return CLUB_EMAIL_THRESHOLD

    @property
    def EMAIL_THRESHOLDS(self) -> dict[str, int]:
        return EMAIL_THRESHOLDS

    @property
    def ATTENDANCE_EARLY_HOURS_THRESHOLD(self) -> int:
        return ATTENDANCE_EARLY_HOURS_THRESHOLD

    @property
    def GOOGLE_CLIENT_ID(self) -> str:
        return env_or_except("GOOGLE_CLIENT_ID")

    @property
    def GOOGLE_CLIENT_SECRET(self) -> str:
        return env_or_except("GOOGLE_CLIENT_SECRET")

    @property
    def JWT_SECRET(self) -> str:
        return env_or_except("JWT_SECRET")

    @property
    def CERTIFICATE_API_URL(self) -> str:
        return env_or_except("CERTIFICATE_API_URL")

    @property
    def MEMBER_APP_URL(self) -> str:
        return env_or_except("MEMBER_APP_URL")

    @property
    def MEMBER_APP_REVALIDATE_SECRET(self) -> str:
        return env_or_except("MEMBER_APP_REVALIDATE_SECRET")

    @property
    def R2_ACCOUNT_ID(self) -> str:
        return env_or_except("R2_ACCOUNT_ID")

    @property
    def R2_ACCESS_KEY_ID(self) -> str:
        return env_or_except("R2_ACCESS_KEY_ID")

    @property
    def R2_SECRET_ACCESS_KEY(self) -> str:
        return env_or_except("R2_SECRET_ACCESS_KEY")

    @property
    def R2_BUCKET_NAME(self) -> str:
        return env_or_except("R2_BUCKET_NAME")

    @property
    def R2_PUBLIC_URL(self) -> str:
        return env_or_except("R2_PUBLIC_URL")

    @property
    def SENTRY_DSN(self) -> Optional[str]:
        return os.getenv("SENTRY_DSN")


@lru_cache(maxsize=2)
def _clerk_bearer(auto_error: bool) -> ClerkHTTPBearer:
    """One bearer per mode, shared process-wide.

    Each ClerkHTTPBearer builds its own JWKS client, so returning a fresh one on
    every property access meant five of them - one per guard - each fetching and
    caching Clerk's signing keys separately.
    """
    return ClerkHTTPBearer(config=ClerkConfig(jwks_url=env_or_except("CLERK_JWKS_URL")), auto_error=auto_error)


def env_or_except(key: str, default: Optional[str] = None) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"⚠️ Environment variable '{key}' is not set.")
    return value


config = Config()
