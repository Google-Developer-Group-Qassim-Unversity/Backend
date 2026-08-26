"""Request and response shapes for the email endpoints.

Moved out of app/routers/emails.py, which was 1,479 lines. Unchanged otherwise.
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, model_validator

from app.DB.schema import EmailLogsEmailType, EmailLogsFromAddress, EmailProvider, MembersGender


class EmailJobResponse(BaseModel):
    """Acknowledgement that a send was queued onto a background task."""

    message: str
    recipient_count: int


class EmailTestResponse(BaseModel):
    """Result of a test send - the addresses it actually went to."""

    sent_count: int
    emails: list[str]


class CertificateEligibleMember(BaseModel):
    id: int
    name: str
    email: str
    gender: MembersGender


class CertificateEligibleCountResponse(BaseModel):
    eligible_count: int
    eligible_members: list[CertificateEligibleMember]
    sent_count: int


class EmailStatsResponse(BaseModel):
    usage: dict[str, int]
    club_threshold: int


class BlastQueuedResponse(BaseModel):
    message: str
    recipient_count: int
    guaranteed_count: int
    algorithmic_count: int


class BlastEligibleCountResponse(BaseModel):
    eligible_count: int
    remaining_capacity: int | None = None


class CertificateLanguage(str, Enum):
    ARABIC = "ar"
    ENGLISH = "en"


class CertificateFormat(str, Enum):
    PNG = "png"
    PDF = "pdf"


class SimpleMember(BaseModel):
    name: str
    email: EmailStr
    gender: MembersGender


class SimpleEvent(BaseModel):
    name: str
    date: str
    official: bool


class CertificateGenerationRequest(BaseModel):
    language: CertificateLanguage
    format: CertificateFormat
    event: SimpleEvent
    member: SimpleMember


class EmailLogs(BaseModel):
    id: int
    member_id: int | None
    event_id: int | None
    from_address: str
    sent_at: str
    recipient_count: int
    email_type: EmailLogsEmailType


class CertificateRequest(BaseModel):
    event: SimpleEvent
    member: SimpleMember
    language: CertificateLanguage
    provider: EmailProvider = EmailProvider.GOOGLE
    from_address: EmailLogsFromAddress | None = None


class CertificateEventEmailLog(BaseModel):
    id: int
    member_name: str
    member_email: str
    sent_at: datetime
    from_address: str


class EnrichedEmailLog(BaseModel):
    id: int
    email_type: EmailLogsEmailType
    from_address: str
    sent_at: datetime
    sent_by: int
    recipient_count: int
    data: Optional[dict] = None
    member_id: Optional[int] = None
    event_id: Optional[int] = None
    member_name: Optional[str] = None
    member_email: Optional[str] = None
    event_name: Optional[str] = None
    event_is_official: Optional[int] = None
    sender_name: Optional[str] = None


class DashboardStats(BaseModel):
    addresses: dict[str, dict[str, int]]
    by_type: dict[str, int]
    total_24h: int


class BlaseResponse(BaseModel):
    status: Literal["sent"]
    recipients: int


class ManualCertificateMember(BaseModel):
    member_id: int | None = None
    member: SimpleMember | None = None

    @model_validator(mode="after")
    def validate_member(self) -> "ManualCertificateMember":
        if (self.member is None) == (self.member_id is None):
            raise ValueError("Provide exactly one of 'member' or 'member_id'")
        return self


class ManualCertificateRequest(BaseModel):
    event: SimpleEvent | None = None
    event_id: int | None = None
    members: list[ManualCertificateMember]
    language: CertificateLanguage
    provider: EmailProvider = EmailProvider.GOOGLE

    @model_validator(mode="after")
    def validate_event(self) -> "ManualCertificateRequest":
        if (self.event is None) == (self.event_id is None):
            raise ValueError("Provide exactly one of 'event' or 'event_id'")
        return self


class CustomEmailAttachment(BaseModel):
    url: str
    filename: str
    content_type: str | None = None


class CustomEmailRequest(BaseModel):
    subject: str
    html_content: str
    members: list[ManualCertificateMember]
    attachments: list[CustomEmailAttachment] = []
    language: CertificateLanguage = CertificateLanguage.ARABIC


class CustomEmailTestRequest(BaseModel):
    subject: str
    html_content: str
    test_recipients: list[ManualCertificateMember]
    attachments: list[CustomEmailAttachment] = []
    language: CertificateLanguage = CertificateLanguage.ARABIC


class BlastAttachment(BaseModel):
    url: str
    filename: str
    content_type: str | None = None


class BlastGuaranteedRecipient(BaseModel):
    member_id: int | None = None
    email: EmailStr | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_recipient(self) -> "BlastGuaranteedRecipient":
        if self.member_id is None and self.email is None:
            raise ValueError("Provide either 'member_id' or 'email'")
        return self


class DirectEmailRequest(BaseModel):
    subject: str
    html_content: str
    recipients: list[BlastGuaranteedRecipient]
    attachments: list[CustomEmailAttachment] = []
    provider: EmailProvider = EmailProvider.GOOGLE


class BlastSendRequest(BaseModel):
    subject: str
    html_content: str
    preview_text: str | None = None
    count: int
    order_by: Literal["activity", "alphabetical"]
    guaranteed_recipients: list[BlastGuaranteedRecipient] = []
    attachments: list[BlastAttachment] = []
    provider: EmailProvider = EmailProvider.GOOGLE


class BlastTestRequest(BaseModel):
    subject: str
    html_content: str
    preview_text: str | None = None
    test_emails: list[EmailStr]
    attachments: list[BlastAttachment] = []
    provider: EmailProvider = EmailProvider.GOOGLE


class EmailTemplateIn(BaseModel):
    name: str
    subject: str
    html_content: str
    preview_text: str | None = None


class EmailTemplateOut(BaseModel):
    id: int
    name: str
    subject: str
    html_content: str
    preview_text: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
