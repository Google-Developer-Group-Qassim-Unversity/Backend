"""Bodies of the member app's account routes: POST /members/onboarding and DELETE /members/me."""

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.member_names import validate_member_name

MemberStatus = Literal["qu_student", "other_university", "high_school", "graduate"]

# The optional fields each status takes. Anything else sent with it is a 422,
# the way the Website's form clears the fields a status hides.
STATUS_FIELDS: dict[str, frozenset[str]] = {
    "qu_student": frozenset({"uni_level", "uni_college"}),
    "other_university": frozenset({"university_name", "uni_level", "uni_college"}),
    "high_school": frozenset({"school_name"}),
    "graduate": frozenset({"uni_college"}),
}

# What the Website's onboarding accepts: 05 and eight more digits, ASCII only.
SAUDI_MOBILE = re.compile(r"05[0-9]{8}")


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _optional_text(value: Any, max_length: int) -> str | None:
    value = _blank_to_none(value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"must be at most {max_length} characters")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("must not contain control characters")
    return value


class MemberOnboardingRequest(BaseModel):
    """The member app's onboarding answers.

    The Website's onboarding questions, plus a fourth status: a student at
    another university. Who the caller is is not part of it - the Clerk user,
    their university ID and their emails come from Clerk - so a body that
    tries to carry anything else is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    status: MemberStatus
    name: str = Field(description="Full official name, 1-50 characters; not shown publicly")
    public_name: str | None = Field(
        default=None, description="Name on the leaderboard, 1-150 characters; defaults to first + family name"
    )
    gender: Literal["Male", "Female"]
    phone_number: str | None = Field(default=None, description="Optional; 05 followed by 8 digits")
    personal_email: EmailStr | None = Field(
        default=None,
        description="Required for university-ID accounts (not an @qu.edu.sa address); "
        "Google and Apple accounts default to, and may only use, an address on the account",
    )
    uni_level: int | None = Field(default=None, ge=1, le=10, description="qu_student and other_university only")
    uni_college: str | None = Field(
        default=None, description="College (or major), up to 100 characters; qu_student, graduate, other_university"
    )
    university_name: str | None = Field(default=None, description="Required for other_university, up to 150 characters")
    school_name: str | None = Field(default=None, description="Optional for high_school, up to 150 characters")

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value):
        return validate_member_name(value, max_length=50)

    @field_validator("public_name", mode="before")
    @classmethod
    def validate_public_name(cls, value):
        value = _blank_to_none(value)
        return None if value is None else validate_member_name(value, max_length=150)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone_number(cls, value):
        value = _blank_to_none(value)
        if value is None:
            return None
        if not isinstance(value, str) or not SAUDI_MOBILE.fullmatch(value.strip()):
            raise ValueError("phone_number must be 05 followed by 8 digits")
        return value.strip()

    @field_validator("personal_email", mode="before")
    @classmethod
    def validate_personal_email(cls, value):
        value = _blank_to_none(value)
        return value.strip() if isinstance(value, str) else value

    @field_validator("uni_college", mode="before")
    @classmethod
    def validate_uni_college(cls, value):
        return _optional_text(value, 100)

    @field_validator("university_name", "school_name", mode="before")
    @classmethod
    def validate_institution(cls, value):
        return _optional_text(value, 150)

    @model_validator(mode="after")
    def check_fields_for_status(self):
        if self.status == "other_university" and self.university_name is None:
            raise ValueError("university_name is required for a student at another university")
        allowed = STATUS_FIELDS[self.status]
        for field in ("uni_level", "uni_college", "university_name", "school_name"):
            if getattr(self, field) is not None and field not in allowed:
                raise ValueError(f"{field} does not apply to status {self.status}")
        return self


class DeletedAccountResponse(BaseModel):
    deleted: bool
    # False when the sign-in had no member record, so only the sign-in went.
    member_anonymized: bool


class CodedErrorResponse(BaseModel):
    """An expected failure. Clients switch on ``code``; ``detail`` is for logs."""

    detail: str | list[dict[str, Any]]
    code: str | None = None
