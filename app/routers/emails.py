# region imports
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
import time
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, logs as log_queries
from app.DB import emails as email_queries
from app.DB.main import SessionLocal
from enum import Enum
from urllib.parse import quote
from app.DB import members as members_queries
import app.DB.submissions as submissions_queries
from app.DB.schema import EmailLogsEmailType, Events, EmailLogsFromAddress, MembersGender
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
from app.helpers import admin_guard, authenticated_guard, get_effective_date, get_uni_id_from_credentials
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
    from_address: EmailLogsFromAddress
    sent_at: str
    recipient_count: int
    email_type: EmailLogsEmailType


class CertificateRequest(BaseModel):
    event: SimpleEvent
    member: SimpleMember
    from_address: EmailLogsFromAddress
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
    from_address: EmailLogsFromAddress
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


# endregion

# region ============== Helper Functions ==============


async def read_html_body(request: Request) -> str:
    html_content = (await request.body()).decode("utf-8")
    if not html_content or not html_content.strip():
        raise EmptyBody()
    return html_content


async def call_acceptance_api(
    emails: list[str], subject: str, html_content: str, from_address: EmailLogsFromAddress
) -> BlaseResponse:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{config.CERTIFICATE_API_URL}/blasts",
                params={"emails": emails, "subject": subject, "from_address": from_address.value},
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


def format_event_date(event: Events) -> str:
    start_effective = get_effective_date(event.start_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    end_effective = get_effective_date(event.end_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    days = (end_effective - start_effective).days
    if days == 0:
        return start_effective.strftime("%Y-%m-%d")
    return f"{start_effective.strftime('%Y-%m-%d')} - {end_effective.strftime('%Y-%m-%d')}"


def get_from_address() -> EmailLogsFromAddress:
    """returns the address to be used based on last 24h usage of the club address."""
    with SessionLocal() as session:
        club_usage = email_queries.get_email_address_usage(session, 1, EmailLogsFromAddress.GDG_QASSIM)
        if club_usage < config.CLUB_EMAIL_THRESHOLD:
            return EmailLogsFromAddress.GDG_QASSIM
        return EmailLogsFromAddress.INFO_KERNELTICS


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
                        event=simple_event,
                        member=simple_member,
                        from_address=get_from_address(),
                        language=CertificateLanguage.ARABIC,
                    )
                    response_data = call_certificate_api(cert_request)
                    write_log(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=cert_request.from_address,
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

        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
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
                from_address = get_from_address()
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
                        event=simple_event,
                        member=simple_member,
                        from_address=from_address,
                        language=request_data.language,
                    )
                    call_certificate_api(cert_request)
                    write_log(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=from_address,
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
        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        background_tasks.add_task(send_manual_certificates_job, request.model_copy(deep=True), requesting_member.id)

    return {
        "message": f"Manual certificate generation initiated for [{len(request.members)}] recipient(s).",
        "recipient_count": len(request.members),
    }


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
            "eligible_members": [{"id": r.Member.id, "name": r.Member.name, "email": r.Member.email} for r in eligible],
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
    address: Annotated[
        EmailLogsFromAddress, Query(description="Email address to check usage for")
    ] = EmailLogsFromAddress.GDG_QASSIM,
    period: Annotated[int, Query(description="Time period in days to check usage for")] = 1,
):
    with SessionLocal() as session:
        club_usage = email_queries.get_email_address_usage(session, period, address)
        return {"usage": club_usage, "club_threshold": config.CLUB_EMAIL_THRESHOLD}


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
        addresses = {}
        for addr in EmailLogsFromAddress:
            usage = email_queries.get_email_address_usage(session, period, addr)
            addresses[addr.value] = {
                "usage": usage,
                "threshold": config.EMAIL_THRESHOLDS.get(addr.value, config.CLUB_EMAIL_THRESHOLD),
            }

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

        uni_id = get_uni_id_from_credentials(credentials)
        member = members_queries.get_member_by_uni_id(session, uni_id)

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
            requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))

            event = events_queries.get_event_by_id(session, event_id)

            html_content = await read_html_body(request)
            write_log(f"Received HTML body with {len(html_content)} characters")

            submissions = submissions_queries.get_accepted_not_invited_by_event(session, event.id)
            emails = [sub.email for sub in submissions if sub.email]
            write_log(f"Found [{len(submissions)}] submissions, [{len(emails)}] emails")

            write_log(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")
            write_log_json({"subject": subject, "email_count": len(emails), "emails": emails})

            from_addr = get_from_address()
            response_data = await call_acceptance_api(emails, subject, html_content, from_addr)
            write_log("Acceptance API responded successfully")
            email_queries.create_email_log(
                session,
                sent_by=requesting_member.id,
                from_address=from_addr,
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

            response_data = await call_acceptance_api(emails, subject, html_content, get_from_address())
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
