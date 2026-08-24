# region imports
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
import time
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, logs as log_queries
from app.DB import emails as email_queries
from app.DB import email_templates as email_template_queries
from app.DB.main import SessionLocal
from enum import Enum
from urllib.parse import quote
from app.DB import members as members_queries
import app.DB.submissions as submissions_queries
from app.DB.schema import EmailLogsEmailType, Events, MembersGender
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from app.config import config
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_exception,
    write_log_json,
    write_log_traceback,
    write_log_title,
)
from app.helpers import (
    admin_guard,
    authenticated_guard,
    get_effective_date,
    get_uni_id_from_credentials,
    resolve_member,
)
from app.exceptions import EmptyBody, GatewayTimeout, BadGateway, ServiceUnavailable
import httpx
import json
from datetime import datetime
from typing import Annotated, Literal, Optional
# endregion


router = APIRouter()


# region ============== Data Models ==============


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


class BlastSendRequest(BaseModel):
    subject: str
    html_content: str
    preview_text: str | None = None
    count: int
    order_by: Literal["activity", "alphabetical"]
    guaranteed_recipients: list[BlastGuaranteedRecipient] = []
    attachments: list[BlastAttachment] = []


class BlastTestRequest(BaseModel):
    subject: str
    html_content: str
    preview_text: str | None = None
    test_emails: list[EmailStr]
    attachments: list[BlastAttachment] = []


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


# endregion

# region ============== Helper Functions ==============


async def read_html_body(request: Request) -> str:
    html_content = (await request.body()).decode("utf-8")
    if not html_content or not html_content.strip():
        raise EmptyBody()
    return html_content


async def call_acceptance_api(emails: list[str], subject: str, html_content: str) -> BlaseResponse:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{config.CERTIFICATE_API_URL}/blasts",
                params={"emails": emails, "subject": subject},
                content=html_content,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
            response.raise_for_status()
            response_data = BlaseResponse.model_validate(response.json())
            return response_data
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Acceptance API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Acceptance API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to acceptance API")


async def call_blast_api(
    emails: list[str], subject: str, html_content: str, preview_text: str | None, attachments: list[BlastAttachment]
) -> BlaseResponse:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{config.CERTIFICATE_API_URL}/blasts",
                params={
                    "emails": emails,
                    "subject": subject,
                    "preview_text": preview_text,
                    "attachments": json.dumps([a.model_dump(mode="json") for a in attachments]),
                },
                content=html_content,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
            response.raise_for_status()
            response_data = BlaseResponse.model_validate(response.json())
            return response_data
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Blast API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Blast API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to blast API")


def call_certificate_api(cert_request: CertificateRequest) -> dict:
    with httpx.Client(timeout=120.0) as client:
        try:
            response = client.post(
                f"{config.CERTIFICATE_API_URL}/emails/certificate",
                json=cert_request.model_dump(mode="json"),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Certificate API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Certificate API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to certificate API")


async def call_custom_email_api(
    recipient_email: str,
    subject: str,
    html_content: str,
    attachments: list[CustomEmailAttachment],
    event: SimpleEvent,
    member: SimpleMember,
    language: CertificateLanguage,
) -> dict:
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(
                f"{config.CERTIFICATE_API_URL}/emails/custom",
                json={
                    "recipient_email": recipient_email,
                    "subject": subject,
                    "html_content": html_content,
                    "event": event.model_dump(mode="json"),
                    "member": member.model_dump(mode="json"),
                    "language": language.value,
                    "attachments": [a.model_dump(mode="json") for a in attachments],
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Custom email API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Custom email API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to custom email API")


def _personalize(text: str, name: str, event_name: str) -> str:
    return text.replace("[Name]", name).replace("[Event Name]", event_name)


def format_event_date(event: Events) -> str:
    start_effective = get_effective_date(event.start_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    end_effective = get_effective_date(event.end_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    days = (end_effective - start_effective).days
    if days == 0:
        return start_effective.strftime("%Y-%m-%d")
    return f"{start_effective.strftime('%Y-%m-%d')} - {end_effective.strftime('%Y-%m-%d')}"


# endregion

# region ============== API Endpoints ==============


@router.post("/{event_id:int}", status_code=status.HTTP_200_OK)
def send_certificates(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    background_tasks: BackgroundTasks,
):
    # Background task definition
    def send_certificates_by_event_id(event: Events, attendance: list, date_str: str, sent_by_id: int):
        with LogFile("send certificates"), SessionLocal() as session:
            try:
                event = events_queries.get_event_by_id(session, event_id)
                simple_event = SimpleEvent(name=event.name, date=date_str, official=bool(event.is_official))
                write_log(f"Processing certificate sending for event [{event.name}] with [{len(attendance)}] attendees")

                already_sent = email_queries.get_members_who_received_certificate(session, event_id)
                attendance = [
                    record for record in attendance if record.Member.id not in {member["id"] for member in already_sent}
                ]
                write_log(
                    f"Filtered out [{len(already_sent)}] attendees who already received certificates, remaining attendees to process: [{len(attendance)}]"
                )
                for attendanceRecord in attendance:
                    member = attendanceRecord.Member
                    simple_member = SimpleMember(name=member.name, email=member.email, gender=member.gender)
                    write_log(f"Sending certificate for member [{member.name}] with email [{member.email}]")
                    cert_request = CertificateRequest(
                        event=simple_event, member=simple_member, language=CertificateLanguage.ARABIC
                    )
                    response_data = call_certificate_api(cert_request)
                    write_log(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=config.SES_FROM_ADDRESS,
                        email_type=EmailLogsEmailType.EVENT_CERTIFICATE,
                        member_id=member.id,
                        event_id=event_id,
                        recipient_count=1,
                        data={
                            "member": simple_member.model_dump(mode="json"),
                            "event": simple_event.model_dump(mode="json"),
                        },
                    )
                    session.commit()

            # TODO - These exception don't make sense this is a background task
            # we generally need better job management (job start message, job failed message, job finished message) in the email
            except HTTPException:
                session.rollback()
                raise
            except Exception as e:
                session.rollback()
                write_log_exception(e)
                write_log_traceback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An error occurred while sending certificates",
                )

    # Actual endpoint logic
    with LogFile("send certificates [JOB]"), SessionLocal() as session:
        write_log_title(f"Sending certificates for event [{event_id}]")

        event = events_queries.get_event_by_id(session, event_id)
        write_log(f"Found event: [{event.name}]")

        attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
        write_log(f"Found [{len(attendance)}] attendees who attended all days for event [{event.name}]")

        date_str = format_event_date(event)
        write_log(f"Event date formatted as: [{date_str}]")

        requesting_member = resolve_member(session, credentials)
        background_tasks.add_task(send_certificates_by_event_id, event, attendance, date_str, requesting_member.id)

        return {
            "message": f"Certificate generation initiated for event [{event.name}] with [{len(attendance)}] attendees."
        }


def _resolve_event(request: ManualCertificateRequest, session) -> tuple[SimpleEvent, int | None]:
    if request.event_id:
        event = events_queries.get_event_by_id(session, request.event_id)
        return (
            SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official)),
            request.event_id,
        )
    assert request.event is not None
    return request.event, None


def _resolve_member(member_item: ManualCertificateMember, session) -> tuple[SimpleMember, int | None]:
    if member_item.member_id:
        member = members_queries.get_member_by_id(session, member_item.member_id)
        return (SimpleMember(name=member.name, email=member.email, gender=member.gender), member_item.member_id)
    assert member_item.member is not None
    return member_item.member, None


@router.post("/manual-certificate", status_code=status.HTTP_200_OK)
def send_manual_certificate(
    request: ManualCertificateRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    background_tasks: BackgroundTasks,
):
    def send_manual_certificates_job(request_data: ManualCertificateRequest, sent_by_id: int):
        with LogFile("manual certificates"), SessionLocal() as session:
            try:
                simple_event, event_id = _resolve_event(request_data, session)
                write_log(
                    f"Processing manual certificates for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
                )

                for member_item in request_data.members:
                    simple_member, member_id = _resolve_member(member_item, session)
                    write_log(
                        f"Sending certificate for member [{simple_member.name}] with email [{simple_member.email}]"
                    )
                    cert_request = CertificateRequest(
                        event=simple_event, member=simple_member, language=request_data.language
                    )
                    call_certificate_api(cert_request)
                    write_log(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=config.SES_FROM_ADDRESS,
                        email_type=EmailLogsEmailType.MANUAL_CERTIFICATE,
                        member_id=member_id,
                        event_id=event_id,
                        recipient_count=1,
                        data={
                            "member": simple_member.model_dump(mode="json"),
                            "event": simple_event.model_dump(mode="json"),
                        },
                    )
                    session.commit()

            except HTTPException:
                session.rollback()
                raise
            except Exception as e:
                session.rollback()
                write_log_exception(e)
                write_log_traceback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An error occurred while sending manual certificates",
                )

    with LogFile("manual certificates [JOB]"), SessionLocal() as session:
        requesting_member = resolve_member(session, credentials)
        background_tasks.add_task(send_manual_certificates_job, request.model_copy(deep=True), requesting_member.id)

    return {
        "message": f"Manual certificate generation initiated for [{len(request.members)}] recipient(s).",
        "recipient_count": len(request.members),
    }


@router.post("/custom/{event_id:int}", status_code=status.HTTP_200_OK)
def send_custom_email(
    event_id: int,
    request: CustomEmailRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    background_tasks: BackgroundTasks,
):
    async def send_custom_email_job(
        request_data: CustomEmailRequest, simple_event: SimpleEvent, event_id: int, sent_by_id: int
    ):
        with LogFile("custom email"), SessionLocal() as session:
            try:
                write_log(
                    f"Processing custom email for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
                )

                for member_item in request_data.members:
                    simple_member, member_id = _resolve_member(member_item, session)
                    write_log(f"Sending custom email to [{simple_member.name}] at [{simple_member.email}]")
                    subject = _personalize(request_data.subject, simple_member.name, simple_event.name)
                    html_content = _personalize(request_data.html_content, simple_member.name, simple_event.name)
                    await call_custom_email_api(
                        simple_member.email,
                        subject,
                        html_content,
                        request_data.attachments,
                        simple_event,
                        simple_member,
                        request_data.language,
                    )
                    write_log("Custom email API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=config.SES_FROM_ADDRESS,
                        email_type=EmailLogsEmailType.EVENT_ANNOUNCEMENT,
                        member_id=member_id,
                        event_id=event_id,
                        recipient_count=1,
                        data={
                            "subject": request_data.subject,
                            "html_content": request_data.html_content,
                            "member": simple_member.model_dump(mode="json"),
                            "certificate_attached": True,
                            "attachments": [
                                {"filename": a.filename, "content_type": a.content_type}
                                for a in request_data.attachments
                            ],
                        },
                    )
                    session.commit()

            except HTTPException:
                session.rollback()
                raise
            except Exception as e:
                session.rollback()
                write_log_exception(e)
                write_log_traceback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="An error occurred while sending custom emails",
                )

    with LogFile("custom email [JOB]"), SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))
        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        background_tasks.add_task(
            send_custom_email_job, request.model_copy(deep=True), simple_event, event_id, requesting_member.id
        )

    return {
        "message": f"Custom email sending initiated for [{len(request.members)}] recipient(s).",
        "recipient_count": len(request.members),
    }


@router.post("/custom/{event_id:int}/test", status_code=status.HTTP_200_OK)
async def send_custom_email_test(
    event_id: int,
    request: CustomEmailTestRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send custom email test"), SessionLocal() as session:
        try:
            write_log_title(f"Sending custom email test for event [{event_id}]")
            event = events_queries.get_event_by_id(session, event_id)
            simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))

            emails: list[str] = []
            for member_item in request.test_recipients:
                simple_member, _ = _resolve_member(member_item, session)
                subject = _personalize(request.subject, simple_member.name, simple_event.name)
                html_content = _personalize(request.html_content, simple_member.name, simple_event.name)
                write_log(f"Sending test custom email to [{simple_member.name}] at [{simple_member.email}]")
                await call_custom_email_api(
                    simple_member.email,
                    subject,
                    html_content,
                    request.attachments,
                    simple_event,
                    simple_member,
                    request.language,
                )
                emails.append(simple_member.email)

            write_log("Custom email API responded successfully for all test recipients")
            return {"sent_count": len(emails), "emails": emails}

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending test custom emails",
            )


@router.get("/certificate-event/eligible-count/{event_id:int}", status_code=status.HTTP_200_OK)
def get_certificate_eligible_count(
    event_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
        already_sent = email_queries.get_members_who_received_certificate(session, event_id)
        already_sent_ids = {m["id"] for m in already_sent}
        eligible = [r for r in attendance if r.Member.id not in already_sent_ids]
        return {
            "eligible_count": len(eligible),
            "eligible_members": [
                {"id": r.Member.id, "name": r.Member.name, "email": r.Member.email, "gender": r.Member.gender}
                for r in eligible
            ],
            "sent_count": len(already_sent),
        }


@router.get(
    "/certificate-event/logs/stream/{event_id:int}", status_code=status.HTTP_200_OK, response_class=EventSourceResponse
)
def get_certificate_event_logs(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    last_event_id: Annotated[int | None, Header()] = None,
):
    last_id = 0

    def get_logs_batch(after_id: int, batch_size: int = 10):
        with SessionLocal() as session:
            logs = email_queries.get_event_certificate_email_log(session, event_id, after_id=after_id, limit=batch_size)
            return logs

    # initial fetch to get the last logs and then start streaming new ones
    logs = get_logs_batch(0, 1000)
    if not logs:
        yield ServerSentEvent(data=json.dumps({"message": "No new logs"}), event="no_logs", id=str(last_id))
    for log in logs:
        yield ServerSentEvent(
            data=CertificateEventEmailLog.model_validate(log).model_dump(mode="json"), event="log", id=str(log["id"])
        )
        if log["id"] > last_id:
            last_id = log["id"]

    while True:
        logs = get_logs_batch(last_id)
        if not logs:
            yield ServerSentEvent(data=json.dumps({"message": "No new logs"}), event="no_logs", id=str(last_id))
        else:
            for log in logs:
                yield ServerSentEvent(
                    data=CertificateEventEmailLog.model_validate(log).model_dump(mode="json"),
                    event="log",
                    id=str(log["id"]),
                )
                if log["id"] > last_id:
                    last_id = log["id"]
        time.sleep(1)  # Wait before checking for new logs


@router.get("/stats", status_code=status.HTTP_200_OK)
def get_email_stats(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    period: Annotated[int, Query(description="Time period in days to check usage for")] = 1,
):
    with SessionLocal() as session:
        usage = email_queries.get_email_address_usage(session, period, config.SES_FROM_ADDRESS)
        return {"usage": usage}


@router.get("/logs", status_code=status.HTTP_200_OK, response_model=list[EmailLogs])
def get_email_logs(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    offset: Annotated[int, Query(description="Number of logs to skip for pagination")] = 0,
    limit: Annotated[int, Query(description="Maximum number of logs to return")] = 100,
):
    with SessionLocal() as session:
        logs = email_queries.get_email_logs(session, limit, offset)
        return logs


@router.get("/logs/event/{event_id:int}", status_code=status.HTTP_200_OK, response_model=list[EmailLogs])
def get_email_logs_by_event_id(
    event_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with SessionLocal() as session:
        logs = email_queries.get_email_logs_by_event_id(session, event_id)
        return logs


@router.get("/logs/member/{member_id:int}", status_code=status.HTTP_200_OK, response_model=list[EmailLogs])
def get_email_logs_by_member_id(
    member_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with SessionLocal() as session:
        logs = email_queries.get_email_logs_by_member_id(session, member_id)
        return logs


@router.get("/logs/enriched", status_code=status.HTTP_200_OK, response_model=list[EnrichedEmailLog])
def get_enriched_email_logs(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    email_type: Annotated[Optional[EmailLogsEmailType], Query(description="Filter by email type")] = None,
    event_id: Annotated[Optional[int], Query(description="Filter by event ID")] = None,
    member_id: Annotated[Optional[int], Query(description="Filter by member ID")] = None,
    start_date: Annotated[Optional[datetime], Query(description="Filter from date")] = None,
    end_date: Annotated[Optional[datetime], Query(description="Filter to date")] = None,
    offset: Annotated[int, Query(description="Number of logs to skip")] = 0,
    limit: Annotated[int, Query(description="Maximum number of logs to return")] = 100,
):
    with SessionLocal() as session:
        rows = email_queries.get_enriched_email_logs(
            session,
            email_type=email_type,
            event_id=event_id,
            member_id=member_id,
            start_date=start_date,
            end_date=end_date,
            offset=offset,
            limit=limit,
        )
        return [EnrichedEmailLog.model_validate(dict(r)) for r in rows]


@router.get("/logs/enriched/stream", status_code=status.HTTP_200_OK, response_class=EventSourceResponse)
def stream_enriched_email_logs(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    last_event_id: Annotated[int | None, Header()] = None,
    email_type: Annotated[Optional[EmailLogsEmailType], Query(description="Filter by email type")] = None,
    event_id: Annotated[Optional[int], Query(description="Filter by event ID")] = None,
    member_id: Annotated[Optional[int], Query(description="Filter by member ID")] = None,
    start_date: Annotated[Optional[datetime], Query(description="Filter from date")] = None,
    end_date: Annotated[Optional[datetime], Query(description="Filter to date")] = None,
):
    last_id = int(last_event_id) if last_event_id else 0

    def get_batch(after_id: int, batch_size: int = 50, order_asc: bool = False):
        with SessionLocal() as session:
            return email_queries.get_enriched_email_logs(
                session,
                email_type=email_type,
                event_id=event_id,
                member_id=member_id,
                start_date=start_date,
                end_date=end_date,
                after_id=after_id,
                limit=batch_size,
                order_asc=order_asc,
            )

    if last_id == 0:
        initial = get_batch(0, 200, order_asc=True)
        if not initial:
            yield ServerSentEvent(data=json.dumps({"message": "No logs found"}), event="no_logs", id=str(last_id))
        for row in initial:
            log = EnrichedEmailLog.model_validate(dict(row))
            yield ServerSentEvent(data=log.model_dump(mode="json"), event="log", id=str(log.id))
            if log.id > last_id:
                last_id = log.id

    while True:
        batch = get_batch(last_id, 50, order_asc=True)
        if not batch:
            yield ServerSentEvent(data=json.dumps({"message": "No new logs"}), event="no_logs", id=str(last_id))
        else:
            for row in batch:
                log = EnrichedEmailLog.model_validate(dict(row))
                yield ServerSentEvent(data=log.model_dump(mode="json"), event="log", id=str(log.id))
                if log.id > last_id:
                    last_id = log.id
        time.sleep(1.5)


@router.get("/stats/dashboard", status_code=status.HTTP_200_OK, response_model=DashboardStats)
def get_dashboard_stats(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    period: Annotated[int, Query(description="Time period in days")] = 1,
):
    with SessionLocal() as session:
        usage = email_queries.get_email_address_usage(session, period, config.SES_FROM_ADDRESS)
        addresses = {config.SES_FROM_ADDRESS: {"usage": usage}}

        by_type = email_queries.get_email_usage_by_type(session, period)
        total_24h = sum(by_type.values())

        return DashboardStats(addresses=addresses, by_type=by_type, total_24h=total_24h)


@router.post("/download-certificate/{event_id:int}", status_code=status.HTTP_200_OK)
def download_certificate(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)],
    lang: Annotated[CertificateLanguage, Query(description="Certificate language")] = CertificateLanguage.ARABIC,
    format: Annotated[CertificateFormat, Query(description="Certificate format")] = CertificateFormat.PDF,
):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)

        member = resolve_member(session, credentials)

        attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
        attended_member_ids = {r.Member.id for r in attendance}

        if member.id not in attended_member_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You did not attend all days of this event"
            )

        simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))
        simple_member = SimpleMember(name=member.name, email=member.email, gender=member.gender)  # type: ignore

        cert_request = CertificateGenerationRequest(
            language=lang, format=format, event=simple_event, member=simple_member
        )

        with httpx.Client(timeout=120.0) as client:
            try:
                response = client.post(
                    f"{config.CERTIFICATE_API_URL}/generations/certificate",
                    json=cert_request.model_dump(mode="json"),
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
            except httpx.TimeoutException:
                raise GatewayTimeout(detail="Certificate generation API request timed out")
            except httpx.HTTPStatusError as e:
                raise BadGateway(detail=f"Certificate generation API returned error: {e.response.status_code}")
            except httpx.RequestError:
                raise ServiceUnavailable(detail="Failed to connect to certificate generation API")

        file_url = data if isinstance(data, str) else data.get("url", data.get("key", str(data)))
        filename = f"certificate-{event.name}-{member.name}.{format.value}"

        file_response = httpx.get(file_url, timeout=60.0, follow_redirects=True)
        file_response.raise_for_status()

        content_type = file_response.headers.get(
            "content-type", f"image/{format.value}" if format == CertificateFormat.PNG else "application/pdf"
        )

        encoded_filename = quote(filename)
        return StreamingResponse(
            iter([file_response.content]),
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename=\"{encoded_filename}\"; filename*=UTF-8''{encoded_filename}"
            },
        )


# endregion

# region ============== Acceptance API Endpoints ==============


@router.post("/acceptance/blasts/{event_id:int}", status_code=status.HTTP_200_OK)
async def send_acceptance_blasts(
    event_id: int,
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send acceptance blasts"), SessionLocal() as session:
        try:
            write_log_title(f"Sending acceptance blasts for event [{event_id}]")
            requesting_member = resolve_member(session, credentials)

            event = events_queries.get_event_by_id(session, event_id)

            html_content = await read_html_body(request)
            write_log(f"Received HTML body with {len(html_content)} characters")

            submissions = submissions_queries.get_accepted_not_invited_by_event(session, event.id)
            emails = [sub.email for sub in submissions if sub.email]
            write_log(f"Found [{len(submissions)}] submissions, [{len(emails)}] emails")

            write_log(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")
            write_log_json({"subject": subject, "email_count": len(emails), "emails": emails})

            response_data = await call_acceptance_api(emails, subject, html_content)
            write_log("Acceptance API responded successfully")
            email_queries.create_email_log(
                session,
                sent_by=requesting_member.id,
                from_address=config.SES_FROM_ADDRESS,
                email_type=EmailLogsEmailType.ACCEPTANCE,
                event_id=event.id,
                recipient_count=len(emails),
                data={
                    "subject": subject,
                    "html_content": html_content,
                    "event": {
                        "name": event.name,
                        "date": format_event_date(event),
                        "official": bool(event.is_official),
                    },
                    "member": [{"name": sub.name, "email": sub.email} for sub in submissions],
                },
            )

            submission_ids = [sub.submission_id for sub in submissions]
            submissions_queries.mark_submissions_as_invited(session, submission_ids)
            session.commit()
            write_log(f"Marked [{len(submission_ids)}] submissions as invited")

            return {"sent_count": len(emails), "emails": emails}

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending acceptance emails",
            )


@router.post("/acceptance/test", status_code=status.HTTP_200_OK)
async def send_acceptance_test(
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    emails: Annotated[list[str], Query(description="Email addresses to send to")],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send acceptance test"):
        try:
            write_log_title("Sending acceptance test emails")

            html_content = await read_html_body(request)
            write_log(f"Received HTML body with {len(html_content)} characters")

            write_log(f"Parsed [{len(emails)}] test emails")
            write_log_json({"emails": emails})
            write_log(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")

            response_data = await call_acceptance_api(emails, subject, html_content)
            write_log("Acceptance API responded successfully")

            return {"sent_count": len(emails), "emails": emails}

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending test acceptance emails",
            )


# endregion

# region ============== Blast API Endpoints ==============


@router.post("/blast", status_code=status.HTTP_200_OK)
async def send_blast(
    request: BlastSendRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    background_tasks: BackgroundTasks,
):
    async def send_blast_job(
        recipients: list[dict], guaranteed_snapshot: list[dict], requested_count: int, sent_by_id: int
    ):
        with LogFile("send blast [JOB]"), SessionLocal() as session:
            try:
                write_log_title(f"Sending blast to [{len(recipients)}] recipients")

                emails = [r["email"] for r in recipients]
                # SES itself caps recipients per raw message and chunks accordingly (see
                # send-certificates' SES_MAX_RECIPIENTS_PER_MESSAGE); this call is one logical
                # blast regardless of how many SES messages it turns into under the hood.
                await call_blast_api(
                    emails, request.subject, request.html_content, request.preview_text, request.attachments
                )
                write_log(f"Blast API responded successfully for [{len(emails)}] recipients")

                email_queries.create_email_log(
                    session,
                    sent_by=sent_by_id,
                    from_address=config.SES_FROM_ADDRESS,
                    email_type=EmailLogsEmailType.BLAST,
                    recipient_count=len(emails),
                    data={
                        "subject": request.subject,
                        "html_content": request.html_content,
                        "preview_text": request.preview_text,
                        "order_by": request.order_by,
                        "requested_count": requested_count,
                        "guaranteed_recipients": guaranteed_snapshot,
                        "recipients": recipients,
                        "attachments": [{"filename": a.filename, "url": a.url} for a in request.attachments],
                    },
                )
                session.commit()

            except Exception as e:
                session.rollback()
                write_log_exception(e)
                write_log_traceback()

    with LogFile("send blast [SETUP]"), SessionLocal() as session:
        write_log_title("Preparing blast email")
        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))

        guaranteed_member_ids = [r.member_id for r in request.guaranteed_recipients if r.member_id is not None]
        resolved_members = (
            members_queries.get_members_by_id(session, guaranteed_member_ids) if guaranteed_member_ids else []
        )
        members_by_id = {m.id: m for m in resolved_members}

        guaranteed: dict[str, dict] = {}
        for recipient in request.guaranteed_recipients:
            if recipient.member_id is not None:
                member = members_by_id.get(recipient.member_id)
                if member is None or not member.email:
                    continue
                guaranteed[member.email.lower()] = {"name": member.name, "email": member.email}
            elif recipient.email is not None:
                guaranteed[recipient.email.lower()] = {"name": recipient.name, "email": recipient.email}

        write_log(f"Resolved [{len(guaranteed)}] guaranteed recipients")

        if request.order_by == "activity":
            pool = members_queries.get_blast_recipients_by_activity(
                session, limit=request.count, exclude_ids=list(members_by_id.keys())
            )
        else:
            pool = members_queries.get_blast_recipients_alphabetical(
                session, limit=request.count, exclude_ids=list(members_by_id.keys())
            )
        write_log(f"Selected [{len(pool)}] recipients via [{request.order_by}] ordering")

        all_recipients = dict(guaranteed)
        for member in pool:
            if member.email:
                all_recipients.setdefault(member.email.lower(), {"name": member.name, "email": member.email})

        recipients = list(all_recipients.values())
        write_log(f"Queuing blast to [{len(recipients)}] total recipients")

        background_tasks.add_task(
            send_blast_job, recipients, list(guaranteed.values()), request.count, requesting_member.id
        )

    return {
        "message": f"Blast email queued for [{len(recipients)}] recipient(s).",
        "recipient_count": len(recipients),
        "guaranteed_count": len(guaranteed),
        "algorithmic_count": len(pool),
    }


@router.post("/blast/test", status_code=status.HTTP_200_OK)
async def send_blast_test(
    request: BlastTestRequest, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with LogFile("send blast test"):
        try:
            write_log_title("Sending blast test email")
            write_log(f"Sending test blast to [{len(request.test_emails)}] test emails")

            await call_blast_api(
                list(request.test_emails),
                request.subject,
                request.html_content,
                request.preview_text,
                request.attachments,
            )
            write_log("Blast API responded successfully")

            return {"sent_count": len(request.test_emails), "emails": request.test_emails}

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending the blast test email",
            )


@router.get("/blast/eligible-count", status_code=status.HTTP_200_OK)
def get_blast_eligible_count(credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        eligible_count = members_queries.get_blast_eligible_count(session)
    return {"eligible_count": eligible_count}


# endregion

# region ============== Email Template Endpoints ==============


@router.get("/blast/templates", status_code=status.HTTP_200_OK)
def list_email_templates(credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        templates = email_template_queries.list_templates(session)
        return [EmailTemplateOut.model_validate(t, from_attributes=True) for t in templates]


@router.post("/blast/templates", status_code=status.HTTP_201_CREATED)
def create_email_template(
    request: EmailTemplateIn, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with SessionLocal() as session:
        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        template = email_template_queries.create_template(
            session,
            name=request.name,
            subject=request.subject,
            html_content=request.html_content,
            preview_text=request.preview_text,
            created_by=requesting_member.id,
        )
        session.commit()
        return EmailTemplateOut.model_validate(template, from_attributes=True)


@router.put("/blast/templates/{template_id:int}", status_code=status.HTTP_200_OK)
def update_email_template(
    template_id: int,
    request: EmailTemplateIn,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with SessionLocal() as session:
        template = email_template_queries.update_template(
            session,
            template_id,
            name=request.name,
            subject=request.subject,
            html_content=request.html_content,
            preview_text=request.preview_text,
        )
        session.commit()
        return EmailTemplateOut.model_validate(template, from_attributes=True)


@router.delete("/blast/templates/{template_id:int}", status_code=status.HTTP_200_OK)
def delete_email_template(template_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with SessionLocal() as session:
        email_template_queries.delete_template(session, template_id)
        session.commit()
        return {"message": f"Template [{template_id}] deleted."}


# endregion
