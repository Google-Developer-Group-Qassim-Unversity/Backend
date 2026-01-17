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

DB_PROD = "scoresTest2" # temporarly replacing prod with test until we migrate the prod db, leave as is for now
DB_DEV = "scoresTest2"

CLERK_PROD = "https://clerk.gdg-q.com/.well-known/jwks.json"
CLERK_DEV = "https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json"

UPLOAD_DIR_DEV = "uploads"
UPLOAD_DIR_PROD = str(Path.home() / "GDG-Files")
LOG_DIR = "logs"

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
    
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
    
    @property
    def is_dev(self) -> bool:
        env = env_or_except("ENV", "Production")
        return env.lower() == "development" 
    
    @property
    def DATABASE_URL(self) -> str:
        url = env_or_except("DATABASE_URL")
        parts = url.rsplit('/', 1)
        if len(parts) == 2:
            url = f"{parts[0]}/{DB_DEV}"
        else:
            raise ValueError(f"⚠️ Invalid DATABASE_URL format excpected to end with /<dbname>, got: {url}")
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
        return LOG_DIR
    
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


def env_or_except(key: str, default: Optional[str] = None) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"⚠️ Environment variable '{key}' is not set.")
    return value

config = Config()
