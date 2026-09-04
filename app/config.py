"""All environment variables and global configuration.

Backed by ``pydantic-settings`` so every variable the app reads is declared in
one place and typed. ``config`` keeps the attribute names it has always had, so
call sites did not change.

Two deliberate choices:

- **``.env.local`` is loaded by ``load_dotenv(override=True)``, not by
  pydantic's ``env_file``.** pydantic-settings gives real environment variables
  priority over the file; this codebase has always done the opposite. Keeping
  the explicit call preserves that precedence.
- **Settings are built lazily and every field is optional.** Values are read
  when a feature needs them, not at import. An instance with no R2 or Wallet
  credentials still boots and serves everything else, and the test suite can
  import the app before the database container exists. Accessors raise a clear
  error when something genuinely required is missing.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer
from pydantic_settings import BaseSettings, SettingsConfigDict

if os.getenv("ENV") != "testing":
    load_dotenv(".env.local", override=True)

LOG_DIR_DEV = "logs"
LOG_DIR_PROD = str(Path.home() / "GDG-Logs")

# Semesters are no longer configured here - they live in the `semesters` table, see app/DB/semesters.py

ATTENDANCE_EARLY_HOURS_THRESHOLD = 6

CLUB_EMAIL_THRESHOLD = 500

EMAIL_THRESHOLDS: dict[str, int] = {"info@kerneltics.com": 2000, "gdg.qu1@gmail.com": 500}


class MissingSettingError(RuntimeError):
    """A feature was used without the environment variable it needs."""

    def __init__(self, name: str):
        super().__init__(f"⚠️ Environment variable '{name}' is not set.")


class Settings(BaseSettings):
    """Every environment variable the application reads.

    All optional at load time; see the module docstring.
    """

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore")

    ENV: str = "Production"
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: Optional[str] = None

    DATABASE_URL: Optional[str] = None
    CLERK_JWKS_URL: Optional[str] = None
    JWT_SECRET: Optional[str] = None

    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REFRESH_TOKEN: Optional[str] = None
    TEMPLATE_FORM_FILE_ID: Optional[str] = None
    GOOGLE_FORMS_TOPIC_NAME: Optional[str] = None
    GOOGLE_ALLOWED_EMAIL_DOMAINS: str = "gmail.com,googlemail.com"

    CERTIFICATE_API_URL: Optional[str] = None
    MEMBER_APP_URL: Optional[str] = None
    MEMBER_APP_REVALIDATE_SECRET: Optional[str] = None
    SES_FROM_ADDRESS: Optional[str] = None

    R2_ACCOUNT_ID: Optional[str] = None
    R2_ACCESS_KEY_ID: Optional[str] = None
    R2_SECRET_ACCESS_KEY: Optional[str] = None
    R2_BUCKET_NAME: Optional[str] = None
    R2_PUBLIC_URL: Optional[str] = None

    # ------------------------------------------------------------------
    # Wallet
    #
    # TODO: remove the hardcoded fallbacks below.
    #
    # APPLE_TEAM_ID, APPLE_PASS_TYPE_ID and GOOGLE_WALLET_ISSUER_ID default to
    # this club's real production identifiers. They were inlined in
    # app/wallet_signer.py before this module existed and are reproduced here
    # unchanged so that porting the settings did not also change behaviour.
    #
    # Why they should go: a deployment that forgets these variables does not
    # fail - it silently signs passes with production identity. That is the
    # wrong failure mode for a signing credential, and it means staging can
    # issue passes indistinguishable from production ones. They are also
    # organisation identifiers sitting in a public-ish repository.
    #
    # What removing them requires: confirm the values are set in Infisical for
    # every environment that issues wallet passes (prod at minimum), then drop
    # the defaults here and let the accessors raise. Wallet passes are exercised
    # by app/routers/wallet.py and tests/test_google_wallet_signer.py, which
    # sets its own values via monkeypatch and is unaffected either way.
    # ------------------------------------------------------------------
    APPLE_TEAM_ID: str = "7NN7W24VXR"
    APPLE_PASS_TYPE_ID: str = "pass.pass.com.gdg-q.wallet"
    APPLE_P12_BASE64: Optional[str] = None
    APPLE_P12_PASSWORD: Optional[str] = None
    APPLE_P12_PATH: Optional[str] = None
    APPLE_WWDR_BASE64: Optional[str] = None
    APPLE_WWDR_PATH: Optional[str] = None
    GOOGLE_WALLET_ISSUER_ID: str = "BCR2DN6DTK643EAC"
    GOOGLE_WALLET_CLASS_ID: str = ""
    GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL: str = ""
    GOOGLE_WALLET_PRIVATE_KEY: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings. Built on first use, not at import."""
    return Settings()


def reload_settings() -> None:
    """Drop the cache so the next read picks up changed environment variables.

    Only useful in tests; nothing in the running app changes its environment.
    """
    get_settings.cache_clear()


@lru_cache(maxsize=2)
def _clerk_bearer(auto_error: bool) -> ClerkHTTPBearer:
    """One bearer per mode, shared process-wide.

    Each ClerkHTTPBearer builds its own JWKS client, so returning a fresh one on
    every property access meant five of them - one per guard - each fetching and
    caching Clerk's signing keys separately.
    """
    return ClerkHTTPBearer(config=ClerkConfig(jwks_url=env_or_except("CLERK_JWKS_URL")), auto_error=auto_error)


class Config:
    """Facade over :class:`Settings` preserving the original attribute names."""

    @property
    def is_dev(self) -> bool:
        return get_settings().ENV.lower() == "development"

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
    def GOOGLE_REFRESH_TOKEN(self) -> str:
        return env_or_except("GOOGLE_REFRESH_TOKEN")

    @property
    def TEMPLATE_FORM_FILE_ID(self) -> str:
        return env_or_except("TEMPLATE_FORM_FILE_ID")

    @property
    def GOOGLE_FORMS_TOPIC_NAME(self) -> str:
        return env_or_except("GOOGLE_FORMS_TOPIC_NAME")

    @property
    def GOOGLE_ALLOWED_EMAIL_DOMAINS(self) -> list[str]:
        return [
            domain.strip().lower()
            for domain in get_settings().GOOGLE_ALLOWED_EMAIL_DOMAINS.split(",")
            if domain.strip()
        ]

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
        return get_settings().SENTRY_DSN

    @property
    def LOG_LEVEL(self) -> str:
        return get_settings().LOG_LEVEL

    # ---- wallet ----

    @property
    def APPLE_TEAM_ID(self) -> str:
        return get_settings().APPLE_TEAM_ID

    @property
    def APPLE_PASS_TYPE_ID(self) -> str:
        return get_settings().APPLE_PASS_TYPE_ID

    @property
    def APPLE_P12_BASE64(self) -> Optional[str]:
        return get_settings().APPLE_P12_BASE64

    @property
    def APPLE_P12_PASSWORD(self) -> Optional[str]:
        return get_settings().APPLE_P12_PASSWORD

    @property
    def APPLE_P12_PATH(self) -> Optional[str]:
        return get_settings().APPLE_P12_PATH

    @property
    def APPLE_WWDR_BASE64(self) -> Optional[str]:
        return get_settings().APPLE_WWDR_BASE64

    @property
    def APPLE_WWDR_PATH(self) -> Optional[str]:
        return get_settings().APPLE_WWDR_PATH

    @property
    def GOOGLE_WALLET_ISSUER_ID(self) -> str:
        return get_settings().GOOGLE_WALLET_ISSUER_ID

    @property
    def GOOGLE_WALLET_CLASS_ID(self) -> str:
        return get_settings().GOOGLE_WALLET_CLASS_ID

    @property
    def GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL(self) -> str:
        return get_settings().GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL

    @property
    def GOOGLE_WALLET_PRIVATE_KEY(self) -> str:
        return get_settings().GOOGLE_WALLET_PRIVATE_KEY


def env_or_except(key: str, default: Optional[str] = None) -> str:
    """Read a setting, raising if it is unset and no default is given.

    Reads through :class:`Settings` rather than ``os.environ`` so every variable
    stays declared in one place.
    """
    value = getattr(get_settings(), key, None)
    if value is None or value == "":
        if default is not None:
            return default
        raise MissingSettingError(key)
    return str(value)


config = Config()
