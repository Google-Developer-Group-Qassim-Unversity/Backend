"""Bodies and answers of the member app's notification routes (``/notifications``)."""

import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.DB.schema import PushKind
from app.services.push import links

TITLE_MAX = 60
BODY_MAX = 180


def _utc(value: datetime | None) -> datetime | None:
    """Naive UTC from the database, sent with its ``Z``."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class DeviceRegistrationRequest(BaseModel):
    """The phone's APNs device token and what the app knows about itself."""

    model_config = ConfigDict(extra="forbid")

    # repr=False: a request body in a traceback's locals never shows the token.
    token: Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{64,200}$", description="APNs device token, hex", repr=False)]
    platform: Literal["ios"]
    environment: Literal["production", "development"] = Field(
        description="The build's APNs environment: TestFlight and App Store builds are production"
    )
    app_version: Annotated[str, Field(min_length=1, max_length=20)]
    app_build: Annotated[str, Field(min_length=1, max_length=20)]
    locale: Literal["ar", "en"] = Field(description="The app's language; pushes to this phone use it")

    @field_validator("token")
    @classmethod
    def _lowercase(cls, value: str) -> str:
        return value.lower()


class DeviceRegisteredResponse(BaseModel):
    id: str = Field(description="The device row's id: what DELETE /notifications/devices/{device_id} takes")
    registered: bool


class DeviceDeletedResponse(BaseModel):
    deleted: bool


class NotificationPrefsResponse(BaseModel):
    remind: bool
    accept: bool
    points: bool
    news: bool
    updated_at: datetime | None = Field(description="null: never saved, these are the defaults")

    @field_validator("updated_at")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class NotificationPrefsUpdate(BaseModel):
    """Any of the four switches; the others keep their value."""

    model_config = ConfigDict(extra="forbid")

    remind: bool | None = None
    accept: bool | None = None
    points: bool | None = None
    news: bool | None = None


class FeedItemResponse(BaseModel):
    id: str
    kind: PushKind = Field(description="What it is about; the 1.0.3 kinds are listed in docs/PUSH.md")
    title: str
    body: str
    url: str
    event_id: int | None
    created_at: datetime = Field(description="When it was sent to the member, UTC")

    @field_validator("created_at")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        return _utc(value) or value


def _clean(value: str | None, max_length: int, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("is required")
        return None
    value = value.strip()
    if not value:
        if required:
            raise ValueError("must not be empty")
        return None
    if len(value) > max_length:
        raise ValueError(f"must be at most {max_length} characters")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("must not contain control characters")
    return value


class AllMembers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["all"]


class EventRegistrants(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["event"]
    event_id: int


class BroadcastRequest(BaseModel):
    """An announcement to members' phones. English is optional: English phones get the Arabic text without it."""

    model_config = ConfigDict(extra="forbid")

    title_ar: str
    body_ar: str
    title_en: str | None = None
    body_en: str | None = None
    url: str | None = Field(default=None, description="An app link (gdgq://...) from the allow-list in docs/PUSH.md")
    audience: Annotated[AllMembers | EventRegistrants, Field(discriminator="type")]
    dry_run: bool = Field(default=False, description="Only count who it would reach; nothing is sent or saved")

    @field_validator("title_ar", "title_en")
    @classmethod
    def _title(cls, value: str | None, info) -> str | None:
        return _clean(value, TITLE_MAX, required=info.field_name == "title_ar")

    @field_validator("body_ar", "body_en")
    @classmethod
    def _body(cls, value: str | None, info) -> str | None:
        return _clean(value, BODY_MAX, required=info.field_name == "body_ar")

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return value.strip()

    @model_validator(mode="after")
    def _english_in_pairs(self) -> "BroadcastRequest":
        if (self.title_en is None) != (self.body_en is None):
            raise ValueError("title_en and body_en go together")
        return self

    @property
    def url_allowed(self) -> bool:
        return self.url is None or links.is_allowed(self.url)


class BroadcastResponse(BaseModel):
    broadcast_id: str | None = Field(description="null on a dry run")
    recipients: int = Field(description="Members it reaches: the switch on and the app on a phone")
