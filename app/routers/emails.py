from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, logs as log_queries
from app.DB import emails as email_queries
from app.DB.main import SessionLocal
from enum import Enum
from app.routers.models import Member_model
from app.DB import members as members_queries
from app.DB.schema import EmailLogsEmailType, Events, EmailLogsFromAddress, MembersGender
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from app.config import config
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_json,
    write_log_exception,
    write_log_traceback,
    write_log_title,
)
from app.helpers import admin_guard, get_effective_date, get_uni_id_from_credentials
from app.exceptions import GatewayTimeout, BadGateway, ServiceUnavailable
import httpx
import json
from typing import Annotated, Literal

router = APIRouter()


# ============== Data Models ==============


class CertificateLanguage(str, Enum):
    ARABIC = "ar"
    ENGLISH = "en"


class SimpleMember(BaseModel):
    name: str
    email: EmailStr
    gender: MembersGender


class SimpleEvent(BaseModel):
    name: str
    date: str
    official: bool


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


# this for this app's manual endpoint not be synced with the external Email service
class ManualCertificateRequest(BaseModel):
    event: SimpleEvent | None = None
    event_id: int | None = None
    member_id: int | None = None
    member: SimpleMember | None = None
    language: CertificateLanguage

    @model_validator(mode="after")
    def validate_event_and_member(self) -> "ManualCertificateRequest":
        if (self.event is None) == (self.event_id is None):
            raise ValueError("Provide exactly one of 'event' or 'event_id'")

        if (self.member is None) == (self.member_id is None):
            raise ValueError("Provide exactly one of 'member' or 'member_id'")

        return self


# ============== Helper Functions ==============


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
            print(e.response.text)
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


# ============== API Endpoints ==============


@router.post("/{event_id:int}", status_code=status.HTTP_200_OK)
def send_certificates(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
    background_tasks: BackgroundTasks,
):
    # Background task definition
    def send_certificates_by_event_id(event: Events, attendance: list, date_str: str):
        with LogFile("send certificates"), SessionLocal() as session:
            try:
                for attendanceRecord in attendance:
                    member = attendanceRecord.Member
                    write_log(f"Sending certificate for member [{member.name}] with email [{member.email}]")
                    cert_request = CertificateRequest(
                        event_name=event.name,
                        date=date_str,
                        official=bool(event.is_official),
                        member=member,
                        from_address=get_from_address(),
                    )
                    response_data = call_certificate_api(cert_request)
                    write_log(f"Certificate API responded with job_id: [{response_data.get('job_id')}]")
                    email_queries.create_email_log(
                        session,
                        from_address=cert_request.from_address,
                        email_type=EmailLogsEmailType.EVENT_CERTIFICATE,
                        member_id=member.id,
                        event_id=event.id,
                        recipient_count=1,
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

        background_tasks.add_task(send_certificates_by_event_id, event, attendance, date_str)

        return {
            "message": f"Certificate generation initiated for event [{event.name}] with [{len(attendance)}] attendees."
        }


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


@router.post("/manual-certificate", status_code=status.HTTP_200_OK)
def send_manual_certificate(
    request: ManualCertificateRequest, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with SessionLocal() as session:
        requesting_member = members_queries.get_member_by_uni_id(session, get_uni_id_from_credentials(credentials))
        # 2. call certificate API to send eamil
        # a. get from address based on usage
        from_address = get_from_address()
        # b. enrich request with event and member data if ids are provided, else use the provided data as is.
        if request.event_id:
            event = events_queries.get_event_by_id(session, request.event_id)
            request.event = SimpleEvent(
                name=event.name, date=format_event_date(event), official=bool(event.is_official)
            )
        if request.member_id:
            member = members_queries.get_member_by_id(session, request.member_id)
            request.member = SimpleMember(
                name=member.name,
                email=member.email,  # ignore pylance # type: ignore
                gender=member.gender,
            )
        # c. call certificate API
        cert_request = CertificateRequest(
            event=request.event, member=request.member, language=request.language, from_address=from_address # ignore pylance because its stupid # type: ignore
        )
        response_data = call_certificate_api(cert_request)
        # 3. log the email in the database
        email_queries.create_email_log(
            session,
            sent_by=requesting_member.id,
            from_address=from_address,
            email_type=EmailLogsEmailType.MANUAL_CERTIFICATE,
            member_id=request.member_id if request.member_id else None,
            event_id=request.event_id if request.event_id else None,
            recipient_count=1,
            data={"member": request.member.model_dump(mode="json"), "event": request.event.model_dump(mode="json")}, # ignore mypy because its stupid # type: ignore
        )
        session.commit()
    return {"message": "Manual certificate generation initiated", "job_id": response_data.get("job_id")}
