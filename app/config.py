"""
All environment variables and global configuration should be accessed through this module.
"""

from dotenv import load_dotenv
import os
from typing import Optional
from pathlib import Path
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer

load_dotenv(override=True)


UPLOAD_DIR_DEV = "uploads"
UPLOAD_DIR_PROD = str(Path.home() / "GDG-Files")
LOG_DIR_DEV = "logs"
LOG_DIR_PROD = str(Path.home() / "GDG-Logs")

CURRENT_SEMESTER = 472
PUBLIC_SEMESTERS = [472]
SEMESTERS = {
    472: ("2026-01-18", "2026-08-23"),
    471: ("2025-08-24", "2026-01-17"),
}

ATTENDANCE_EARLY_HOURS_THRESHOLD = 6


class Config:
    @property
    def is_dev(self) -> bool:
        env = env_or_except("ENV", "Production")
        return env.lower() == "development"

    @property
    def DATABASE_URL(self) -> str:
        print(env_or_except("DATABASE_URL"))
        return env_or_except("DATABASE_URL")

    @property
    def CLERK_GUARD(self):
        jwks_url = env_or_except("CLERK_JWKS_URL")
        clerk_config = ClerkConfig(jwks_url=jwks_url)
        return ClerkHTTPBearer(config=clerk_config)

    @property
    def CLERK_GUARD_optional(self):
        jwks_url = env_or_except("CLERK_JWKS_URL")
        clerk_config = ClerkConfig(jwks_url=jwks_url)
        return ClerkHTTPBearer(config=clerk_config, auto_error=False)

    @property
    def UPLOAD_DIR(self) -> str:
        if self.is_dev:
            os.makedirs(UPLOAD_DIR_DEV, exist_ok=True)
            return UPLOAD_DIR_DEV
        else:
            os.makedirs(UPLOAD_DIR_PROD, exist_ok=True)
            return UPLOAD_DIR_PROD

    @property
    def LOG_DIR(self) -> str:
        if self.is_dev:
            os.makedirs(LOG_DIR_DEV, exist_ok=True)
            return LOG_DIR_DEV
        else:
            os.makedirs(LOG_DIR_PROD, exist_ok=True)
            return LOG_DIR_PROD

    @property
    def ATTENDANCE_EARLY_HOURS_THRESHOLD(self) -> int:
        return ATTENDANCE_EARLY_HOURS_THRESHOLD

    @property
    def CURRENT_SEMESTER(self) -> int:
        return CURRENT_SEMESTER

    @property
    def PUBLIC_SEMESTERS(self) -> list[int]:
        return PUBLIC_SEMESTERS

    @property
    def SEMESTERS(self) -> dict[int, tuple[str, str]]:
        return SEMESTERS

    def get_semester_dates(self, semester_id: int) -> tuple[str, str]:
        if semester_id not in SEMESTERS:
            raise ValueError(f"Semester {semester_id} not found")
        return SEMESTERS[semester_id]

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


def env_or_except(key: str, default: Optional[str] = None) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"⚠️ Environment variable '{key}' is not set.")
    return value


config = Config()