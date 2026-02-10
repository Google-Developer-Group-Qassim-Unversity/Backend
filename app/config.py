"""
All environment variables and global configuration should be accessed through this module.
"""

from dotenv import load_dotenv
import os
from typing import Optional
from pathlib import Path
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer

# Load environment variables
load_dotenv()


# =============================================================================
# Configuration Constants (add more here and then expose them via Config class)
# =============================================================================

DB_PROD = "scores"
DB_DEV = "scores_dev"

CLERK_PROD = "https://clerk.gdg-q.com/.well-known/jwks.json"
CLERK_DEV = "https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json"

UPLOAD_DIR_DEV = "uploads"
UPLOAD_DIR_PROD = str(Path.home() / "GDG-Files")
LOG_DIR_DEV = "logs"
LOG_DIR_PROD = str(Path.home() / "GDG-Logs")

CERTIFICATE_API_URL_DEV = "http://localhost:8000"
CERTIFICATE_API_URL_PROD = "http://localhost:8000"

# Pagination settings
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

# Sorting settings
DEFAULT_SORT_ORDER = "DESC"
DEFAULT_EVENTS_SORT_BY = "start_datetime"
ALLOWED_EVENTS_SORT_BY = ["start_datetime"]

# =============================================================================
#
# =============================================================================


class Config:
    @property
    def is_dev(self) -> bool:
        env = env_or_except("ENV", "Production")
        return env.lower() == "development"

    @property
    def DATABASE_URL(self) -> str:
        url = env_or_except("DATABASE_URL")
        parts = url.rsplit("/", 1)
        if len(parts) == 2:
            if self.is_dev:
                url = f"{parts[0]}/{DB_DEV}"
                print(
                    f"Using database: {DB_DEV}"
                    if self.is_dev
                    else f"Using database: {DB_PROD}"
                )
            else:
                url = f"{parts[0]}/{DB_PROD}"
        else:
            raise ValueError(
                f"⚠️ Invalid DATABASE_URL format excpected to end with /<dbname>, got: {url}"
            )
        return url

    @property
    def CLERK_GUARD(self) -> str:
        if self.is_dev:
            clerk_config = ClerkConfig(jwks_url=CLERK_DEV)
        else:
            clerk_config = ClerkConfig(jwks_url=CLERK_PROD)
        clerk_auth_guard = ClerkHTTPBearer(config=clerk_config)
        return clerk_auth_guard

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
    def DEFAULT_PAGE_SIZE(self) -> int:
        return DEFAULT_PAGE_SIZE

    @property
    def MAX_PAGE_SIZE(self) -> int:
        return MAX_PAGE_SIZE

    @property
    def DEFAULT_SORT_ORDER(self) -> str:
        return DEFAULT_SORT_ORDER

    @property
    def DEFAULT_EVENTS_SORT_BY(self) -> str:
        return DEFAULT_EVENTS_SORT_BY

    @property
    def ALLOWED_EVENTS_SORT_BY(self) -> list[str]:
        return ALLOWED_EVENTS_SORT_BY

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
        if self.is_dev:
            return CERTIFICATE_API_URL_DEV
        else:
            return CERTIFICATE_API_URL_PROD


def env_or_except(key: str, default: Optional[str] = None) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"⚠️ Environment variable '{key}' is not set.")
    return value


config = Config()
