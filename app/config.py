"""
All environment variables and global configuration should be accessed through this module.
"""

from dotenv import load_dotenv
import os
from typing import Optional

# Load environment variables
load_dotenv()


# =============================================================================
# Configuration Constants (add more here and then expose them via Config class)
# =============================================================================

DB_PROD = "scores"
DB_DEV = "scoresTest"

CLERK_PROD = "https://clerk.gdg-q.com/.well-known/jwks.json"
CLERK_DEV = "https://quality-ram-46.clerk.accounts.dev/.well-known/jwks.json"

UPLOAD_DIR = "uploads"

# =============================================================================
# 
# =============================================================================

class Config:
    
    def __init__(self):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    @property
    def is_dev(self) -> bool:
        env = env_or_except("ENV", "Production")
        return env.lower() == "development"
    
    @property
    def DATABASE_URL(self) -> str:
        url = env_or_except("DATABASE_URL")
        if self.is_dev:
            return url.replace(DB_PROD, DB_DEV)
        return url
    
    @property
    def CLERK_JWKS_URL(self) -> str:
        if self.is_dev:
            return CLERK_DEV
        return CLERK_PROD
    
    @property
    def UPLOAD_DIR(self) -> str:
        return UPLOAD_DIR


def env_or_except(key: str, default: Optional[str] = None) -> str:
    value = os.getenv(key)
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"⚠️ Environment variable '{key}' is not set.")
    return value

config = Config()
