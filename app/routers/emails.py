import logging

# region imports
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, BackgroundTasks, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
import time

from app.DB import events as events_queries, logs as log_queries
from app.DB import emails as email_queries
from app.DB import email_templates as email_template_queries
from app.DB.main import db_session
from urllib.parse import quote
from app.DB import members as members_queries
import app.DB.submissions as submissions_queries
from app.DB.schema import EmailLogsEmailType, EmailLogsFromAddress, EmailProvider, Events
from app.config import config
from app.helpers import CurrentMember, admin_guard
from app.routers.responses import MessageResponse
from app.routers.email_models import (
    BlastEligibleCountResponse,
    BlastQueuedResponse,
    BlastSendRequest,
    BlastTestRequest,
    CertificateEligibleCountResponse,
    CertificateEventEmailLog,
    CertificateFormat,
    CertificateGenerationRequest,
    CertificateLanguage,
    CertificateRequest,
    CustomEmailRequest,
    CustomEmailTestRequest,
    DashboardStats,
    DirectEmailRequest,
    EmailJobResponse,
    EmailLogs,
    EmailStatsResponse,
    EmailTemplateIn,
    EmailTemplateOut,
    EmailTestResponse,
    EnrichedEmailLog,
    ManualCertificateMember,
    ManualCertificateRequest,
    SimpleEvent,
    SimpleMember,
)
from app.services.email_capacity import (
    _personalize,
    format_event_date,
    get_from_address,
    get_send_capacity,
    get_total_remaining_send_capacity,
)
from app.services.email_gateway import (
    call_acceptance_api,
    call_blast_api,
    call_certificate_api,
    call_custom_email_api,
    call_direct_email_api,
)

from app.exceptions import EmptyBody, GatewayTimeout, BadGateway, ServiceUnavailable
from collections.abc import Sequence

import httpx
import json
from datetime import datetime
from typing import Annotated, Optional, Any
from app.dependencies import DB


# endregion


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/emails", tags=["emails"])


# region ============== Data Models ==============


# endregion

# region ============== Helper Functions ==============


async def read_html_body(request: Request) -> str:
    html_content = (await request.body()).decode("utf-8")
    if not html_content or not html_content.strip():
        raise EmptyBody()
    return html_content


# endregion

# region ============== API Endpoints ==============


@router.post(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=MessageResponse,
)
def send_certificates(event_id: int, requesting_member: CurrentMember, background_tasks: BackgroundTasks, session: DB):
    # Background task definition
    def send_certificates_by_event_id(event: Events, attendance: list, date_str: str, sent_by_id: int):
        with db_session() as session:
            try:
                event = events_queries.get_event_by_id(session, event_id)
                simple_event = SimpleEvent(name=event.name, date=date_str, official=bool(event.is_official))
                logger.info(
                    f"Processing certificate sending for event [{event.name}] with [{len(attendance)}] attendees"
                )

                already_sent = email_queries.get_members_who_received_certificate(session, event_id)
                attendance = [
                    record for record in attendance if record.Member.id not in {member["id"] for member in already_sent}
                ]
                logger.info(
                    f"Filtered out [{len(already_sent)}] attendees who already received certificates, remaining attendees to process: [{len(attendance)}]"
                )
                for attendanceRecord in attendance:
                    member = attendanceRecord.Member
                    simple_member = SimpleMember(name=member.name, email=member.email, gender=member.gender)
                    logger.info(f"Sending certificate for member [{member.name}] with email [{member.email}]")
                    from_address = get_from_address()
                    cert_request = CertificateRequest(
                        event=simple_event,
                        member=simple_member,
                        language=CertificateLanguage.ARABIC,
                        provider=EmailProvider.GOOGLE,
                        from_address=from_address,
                    )
                    response_data = call_certificate_api(cert_request)
                    logger.info(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=from_address.value,
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
            except Exception as e:
                logger.exception(e)
                raise

    # Actual endpoint logic
    logger.info(f"Sending certificates for event [{event_id}]")

    event = events_queries.get_event_by_id(session, event_id)
    logger.info(f"Found event: [{event.name}]")

    attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
    logger.info(f"Found [{len(attendance)}] attendees who attended all days for event [{event.name}]")

    date_str = format_event_date(event)
    logger.info(f"Event date formatted as: [{date_str}]")

    background_tasks.add_task(send_certificates_by_event_id, event, attendance, date_str, requesting_member.id)

    return {"message": f"Certificate generation initiated for event [{event.name}] with [{len(attendance)}] attendees."}


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


@router.post(
    "/manual-certificate",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailJobResponse,
)
def send_manual_certificate(
    request: ManualCertificateRequest, requesting_member: CurrentMember, background_tasks: BackgroundTasks, session: DB
):
    def send_manual_certificates_job(request_data: ManualCertificateRequest, sent_by_id: int):
        with db_session() as session:
            try:
                from_address = get_from_address() if request_data.provider == EmailProvider.GOOGLE else None
                simple_event, event_id = _resolve_event(request_data, session)
                logger.info(
                    f"Processing manual certificates for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
                )

                for member_item in request_data.members:
                    simple_member, member_id = _resolve_member(member_item, session)
                    logger.info(
                        f"Sending certificate for member [{simple_member.name}] with email [{simple_member.email}]"
                    )
                    cert_request = CertificateRequest(
                        event=simple_event,
                        member=simple_member,
                        language=request_data.language,
                        provider=request_data.provider,
                        from_address=from_address,
                    )
                    call_certificate_api(cert_request)
                    logger.info(f"Certificate API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=from_address.value if from_address else config.SES_FROM_ADDRESS,
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

            except Exception as e:
                logger.exception(e)
                raise

    background_tasks.add_task(send_manual_certificates_job, request.model_copy(deep=True), requesting_member.id)

    return {
        "message": f"Manual certificate generation initiated for [{len(request.members)}] recipient(s).",
        "recipient_count": len(request.members),
    }


@router.post(
    "/custom/{event_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailJobResponse,
)
def send_custom_email(
    event_id: int,
    request: CustomEmailRequest,
    requesting_member: CurrentMember,
    background_tasks: BackgroundTasks,
    session: DB,
):
    async def send_custom_email_job(
        request_data: CustomEmailRequest, simple_event: SimpleEvent, event_id: int, sent_by_id: int
    ):
        with db_session() as session:
            try:
                from_address = get_from_address()
                logger.info(
                    f"Processing custom email for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
                )

                for member_item in request_data.members:
                    simple_member, member_id = _resolve_member(member_item, session)
                    logger.info(f"Sending custom email to [{simple_member.name}] at [{simple_member.email}]")
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
                        EmailProvider.GOOGLE,
                        from_address,
                    )
                    logger.info("Custom email API responded with 200 OK")
                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=from_address.value,
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

            except Exception as e:
                logger.exception(e)
                raise

    event = events_queries.get_event_by_id(session, event_id)
    simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))
    background_tasks.add_task(
        send_custom_email_job, request.model_copy(deep=True), simple_event, event_id, requesting_member.id
    )

    return {
        "message": f"Custom email sending initiated for [{len(request.members)}] recipient(s).",
        "recipient_count": len(request.members),
    }


@router.post(
    "/custom/{event_id:int}/test",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailTestResponse,
)
async def send_custom_email_test(event_id: int, request: CustomEmailTestRequest, session: DB):
    logger.info(f"Sending custom email test for event [{event_id}]")

    def prepare() -> tuple[SimpleEvent, EmailLogsFromAddress, list[tuple[SimpleMember, str, str]]]:
        """All the synchronous database work, in one hop off the event loop."""
        event = events_queries.get_event_by_id(session, event_id)
        simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))
        from_address = get_from_address()
        prepared = []
        for member_item in request.test_recipients:
            simple_member, _ = _resolve_member(member_item, session)
            prepared.append(
                (
                    simple_member,
                    _personalize(request.subject, simple_member.name, simple_event.name),
                    _personalize(request.html_content, simple_member.name, simple_event.name),
                )
            )
        return simple_event, from_address, prepared

    simple_event, from_address, prepared = await run_in_threadpool(prepare)

    emails: list[str] = []
    for simple_member, subject, html_content in prepared:
        logger.info(f"Sending test custom email to [{simple_member.name}] at [{simple_member.email}]")
        await call_custom_email_api(
            simple_member.email,
            subject,
            html_content,
            request.attachments,
            simple_event,
            simple_member,
            request.language,
            EmailProvider.GOOGLE,
            from_address,
        )
        emails.append(simple_member.email)

    logger.info("Custom email API responded successfully for all test recipients")
    return {"sent_count": len(emails), "emails": emails}


@router.post(
    "/direct", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)], response_model=EmailJobResponse
)
def send_direct_email(
    request: DirectEmailRequest, requesting_member: CurrentMember, background_tasks: BackgroundTasks, session: DB
):
    async def send_direct_email_job(
        recipients: list[dict], sent_by_id: int, provider: EmailProvider, from_address: EmailLogsFromAddress | None
    ):
        with db_session() as session:
            try:
                logger.info(f"Sending direct email to [{len(recipients)}] recipients")
                for recipient in recipients:
                    logger.info(
                        f"Sending direct email to [{recipient['name'] or recipient['email']}] at [{recipient['email']}]"
                    )
                    await call_direct_email_api(
                        recipient["email"],
                        request.subject,
                        request.html_content,
                        request.attachments,
                        provider,
                        from_address,
                    )
                    logger.info("Direct email API responded with 200 OK")

                    email_queries.create_email_log(
                        session,
                        sent_by=sent_by_id,
                        from_address=from_address.value if from_address else config.SES_FROM_ADDRESS,
                        email_type=EmailLogsEmailType.DIRECT,
                        member_id=recipient["member_id"],
                        recipient_count=1,
                        data={
                            "subject": request.subject,
                            "html_content": request.html_content,
                            "recipient": {"name": recipient["name"], "email": recipient["email"]},
                            "attachments": [{"filename": a.filename, "url": a.url} for a in request.attachments],
                        },
                    )
                    session.commit()

            except HTTPException:
                session.rollback()
                raise
            except Exception as e:
                session.rollback()
                logger.exception(e)

    logger.info("Preparing direct email")

    recipient_member_ids = [r.member_id for r in request.recipients if r.member_id is not None]
    resolved_members = members_queries.get_members_by_id(session, recipient_member_ids) if recipient_member_ids else []
    members_by_id = {m.id: m for m in resolved_members}

    recipients: dict[str, dict] = {}
    for r in request.recipients:
        if r.member_id is not None:
            member = members_by_id.get(r.member_id)
            if member is None or not member.email:
                continue
            recipients[member.email.lower()] = {"name": member.name, "email": member.email, "member_id": member.id}
        elif r.email is not None:
            recipients[r.email.lower()] = {"name": r.name, "email": r.email, "member_id": None}

    recipient_list = list(recipients.values())
    logger.info(f"Resolved [{len(recipient_list)}] recipients")

    from_address = get_from_address() if request.provider == EmailProvider.GOOGLE else None

    background_tasks.add_task(
        send_direct_email_job, recipient_list, requesting_member.id, request.provider, from_address
    )

    return {
        "message": f"Direct email queued for [{len(recipient_list)}] recipient(s).",
        "recipient_count": len(recipient_list),
    }


@router.get(
    "/certificate-event/eligible-count/{event_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=CertificateEligibleCountResponse,
)
def get_certificate_eligible_count(event_id: int, session: DB):
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
    "/certificate-event/logs/stream/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_class=EventSourceResponse,
    dependencies=[Depends(admin_guard)],
)
def get_certificate_event_logs(event_id: int, last_event_id: Annotated[int | None, Header()] = None):
    last_id = 0

    def get_logs_batch(after_id: int, batch_size: int = 10):
        with db_session() as session:
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


@router.get(
    "/stats", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)], response_model=EmailStatsResponse
)
def get_email_stats(
    session: DB,
    address: Annotated[
        EmailLogsFromAddress, Query(description="Email address to check usage for")
    ] = EmailLogsFromAddress.GDG_QASSIM,
    period: Annotated[int, Query(description="Time period in days to check usage for")] = 1,
):
    usage = email_queries.get_email_address_usage(session, period, address.value)
    return {"usage": usage, "club_threshold": config.CLUB_EMAIL_THRESHOLD}


@router.get(
    "/logs", status_code=status.HTTP_200_OK, response_model=list[EmailLogs], dependencies=[Depends(admin_guard)]
)
def get_email_logs(
    session: DB,
    offset: Annotated[int, Query(description="Number of logs to skip for pagination")] = 0,
    limit: Annotated[int, Query(description="Maximum number of logs to return")] = 100,
):
    logs = email_queries.get_email_logs(session, limit, offset)
    return logs


@router.get(
    "/logs/event/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=list[EmailLogs],
    dependencies=[Depends(admin_guard)],
)
def get_email_logs_by_event_id(event_id: int, session: DB):
    logs = email_queries.get_email_logs_by_event_id(session, event_id)
    return logs


@router.get(
    "/logs/member/{member_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=list[EmailLogs],
    dependencies=[Depends(admin_guard)],
)
def get_email_logs_by_member_id(member_id: int, session: DB):
    logs = email_queries.get_email_logs_by_member_id(session, member_id)
    return logs


@router.get(
    "/logs/enriched",
    status_code=status.HTTP_200_OK,
    response_model=list[EnrichedEmailLog],
    dependencies=[Depends(admin_guard)],
)
def get_enriched_email_logs(
    session: DB,
    email_type: Annotated[Optional[EmailLogsEmailType], Query(description="Filter by email type")] = None,
    event_id: Annotated[Optional[int], Query(description="Filter by event ID")] = None,
    member_id: Annotated[Optional[int], Query(description="Filter by member ID")] = None,
    start_date: Annotated[Optional[datetime], Query(description="Filter from date")] = None,
    end_date: Annotated[Optional[datetime], Query(description="Filter to date")] = None,
    offset: Annotated[int, Query(description="Number of logs to skip")] = 0,
    limit: Annotated[int, Query(description="Maximum number of logs to return")] = 100,
):
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


@router.get(
    "/logs/enriched/stream",
    status_code=status.HTTP_200_OK,
    response_class=EventSourceResponse,
    dependencies=[Depends(admin_guard)],
)
def stream_enriched_email_logs(
    last_event_id: Annotated[int | None, Header()] = None,
    email_type: Annotated[Optional[EmailLogsEmailType], Query(description="Filter by email type")] = None,
    event_id: Annotated[Optional[int], Query(description="Filter by event ID")] = None,
    member_id: Annotated[Optional[int], Query(description="Filter by member ID")] = None,
    start_date: Annotated[Optional[datetime], Query(description="Filter from date")] = None,
    end_date: Annotated[Optional[datetime], Query(description="Filter to date")] = None,
):
    last_id = int(last_event_id) if last_event_id else 0

    def get_batch(after_id: int, batch_size: int = 50, order_asc: bool = False):
        with db_session() as session:
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


@router.get(
    "/stats/dashboard",
    status_code=status.HTTP_200_OK,
    response_model=DashboardStats,
    dependencies=[Depends(admin_guard)],
)
def get_dashboard_stats(session: DB, period: Annotated[int, Query(description="Time period in days")] = 1):
    addresses = {}
    for addr in EmailLogsFromAddress:
        usage = email_queries.get_email_address_usage(session, period, addr.value)
        addresses[addr.value] = {
            "usage": usage,
            "threshold": config.EMAIL_THRESHOLDS.get(addr.value, config.CLUB_EMAIL_THRESHOLD),
        }

    # SES is optional/admin-selected and has no daily cap of its own, so it's
    # reported usage-only -- no "threshold" key, unlike the two Gmail addresses above.
    ses_usage = email_queries.get_email_address_usage(session, period, config.SES_FROM_ADDRESS)
    addresses[config.SES_FROM_ADDRESS] = {"usage": ses_usage}

    by_type = email_queries.get_email_usage_by_type(session, period)
    total_24h = sum(by_type.values())

    return DashboardStats(addresses=addresses, by_type=by_type, total_24h=total_24h)


@router.post("/download-certificate/{event_id:int}", status_code=status.HTTP_200_OK, response_class=StreamingResponse)
def download_certificate(
    event_id: int,
    member: CurrentMember,
    session: DB,
    lang: Annotated[CertificateLanguage, Query(description="Certificate language")] = CertificateLanguage.ARABIC,
    format: Annotated[CertificateFormat, Query(description="Certificate format")] = CertificateFormat.PDF,
):
    event = events_queries.get_event_by_id(session, event_id)

    attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
    attended_member_ids = {r.Member.id for r in attendance}

    if member.id not in attended_member_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You did not attend all days of this event")

    simple_event = SimpleEvent(name=event.name, date=format_event_date(event), official=bool(event.is_official))
    simple_member = SimpleMember(name=member.name, email=member.email, gender=member.gender)  # type: ignore

    cert_request = CertificateGenerationRequest(language=lang, format=format, event=simple_event, member=simple_member)

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


@router.post(
    "/acceptance/blasts/{event_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailTestResponse,
)
async def send_acceptance_blasts(
    event_id: int,
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    requesting_member: CurrentMember,
    session: DB,
):
    logger.info(f"Sending acceptance blasts for event [{event_id}]")

    html_content = await read_html_body(request)
    logger.info(f"Received HTML body with {len(html_content)} characters")

    def load() -> tuple[Events, Sequence[Any], list[str], EmailLogsFromAddress]:
        event = events_queries.get_event_by_id(session, event_id)
        submissions = submissions_queries.get_accepted_not_invited_by_event(session, event.id)
        # get_from_address opens its own session, so it belongs in this phase too
        return event, submissions, [sub.email for sub in submissions if sub.email], get_from_address()

    event, submissions, emails, from_addr = await run_in_threadpool(load)
    logger.info(f"Found [{len(submissions)}] submissions, [{len(emails)}] emails")

    logger.info(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")
    logger.debug("request body: %s", {"subject": subject, "email_count": len(emails), "emails": emails})
    response_data = await call_acceptance_api(emails, subject, html_content, from_addr)
    logger.info("Acceptance API responded successfully")

    def record() -> None:
        email_queries.create_email_log(
            session,
            sent_by=requesting_member.id,
            from_address=from_addr.value,
            email_type=EmailLogsEmailType.ACCEPTANCE,
            event_id=event.id,
            recipient_count=len(emails),
            data={
                "subject": subject,
                "html_content": html_content,
                "event": {"name": event.name, "date": format_event_date(event), "official": bool(event.is_official)},
                "member": [{"name": sub.name, "email": sub.email} for sub in submissions],
            },
        )
        submissions_queries.mark_submissions_as_invited(session, [sub.submission_id for sub in submissions])
        session.commit()

    await run_in_threadpool(record)
    logger.info(f"Marked [{len(submissions)}] submissions as invited")

    return {"sent_count": len(emails), "emails": emails}


@router.post(
    "/acceptance/test",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailTestResponse,
)
async def send_acceptance_test(
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    emails: Annotated[list[str], Query(description="Email addresses to send to")],
):
    logger.info("Sending acceptance test emails")

    html_content = await read_html_body(request)
    logger.info(f"Received HTML body with {len(html_content)} characters")

    logger.info(f"Parsed [{len(emails)}] test emails")
    logger.debug("request body: %s", {"emails": emails})
    logger.info(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")

    from_addr = await run_in_threadpool(get_from_address)
    response_data = await call_acceptance_api(emails, subject, html_content, from_addr)
    logger.info("Acceptance API responded successfully")

    return {"sent_count": len(emails), "emails": emails}


# endregion

# region ============== Blast API Endpoints ==============


@router.post(
    "/blast", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)], response_model=BlastQueuedResponse
)
def send_blast(
    request: BlastSendRequest, requesting_member: CurrentMember, background_tasks: BackgroundTasks, session: DB
):
    async def send_blast_job(
        recipients: list[dict], guaranteed_snapshot: list[dict], requested_count: int, sent_by_id: int
    ):
        with db_session() as session:
            try:
                logger.info(f"Sending blast to [{len(recipients)}] recipients")

                if request.provider == EmailProvider.GOOGLE:
                    gdg_capacity = get_send_capacity(EmailLogsFromAddress.GDG_QASSIM)
                    info_capacity = get_send_capacity(EmailLogsFromAddress.INFO_KERNELTICS)

                    gdg_chunk = recipients[:gdg_capacity]
                    info_chunk = recipients[gdg_capacity : gdg_capacity + info_capacity]
                    overflow = recipients[gdg_capacity + info_capacity :]
                    if overflow:
                        logger.info(
                            f"[{len(overflow)}] recipients exceed today's combined capacity; "
                            f"sending them via [{EmailLogsFromAddress.INFO_KERNELTICS.value}] anyway to honor guaranteed delivery"
                        )
                        info_chunk = info_chunk + overflow

                    guaranteed_emails = {g["email"] for g in guaranteed_snapshot}

                    for from_addr, chunk in (
                        (EmailLogsFromAddress.GDG_QASSIM, gdg_chunk),
                        (EmailLogsFromAddress.INFO_KERNELTICS, info_chunk),
                    ):
                        if not chunk:
                            continue
                        emails = [r["email"] for r in chunk]
                        logger.info(f"Sending [{len(emails)}] recipients via [{from_addr.value}]")

                        await call_blast_api(
                            emails,
                            request.subject,
                            request.html_content,
                            EmailProvider.GOOGLE,
                            from_addr,
                            request.preview_text,
                            request.attachments,
                        )
                        logger.info(f"Blast API responded successfully for [{from_addr.value}]")

                        email_queries.create_email_log(
                            session,
                            sent_by=sent_by_id,
                            from_address=from_addr.value,
                            email_type=EmailLogsEmailType.BLAST,
                            recipient_count=len(emails),
                            data={
                                "subject": request.subject,
                                "html_content": request.html_content,
                                "preview_text": request.preview_text,
                                "order_by": request.order_by,
                                "requested_count": requested_count,
                                "guaranteed_recipients": [r for r in chunk if r["email"] in guaranteed_emails],
                                "recipients": chunk,
                                "attachments": [{"filename": a.filename, "url": a.url} for a in request.attachments],
                            },
                        )
                        session.commit()
                else:
                    emails = [r["email"] for r in recipients]
                    # SES itself caps recipients per raw message and chunks accordingly (see
                    # send-certificates' SES_MAX_RECIPIENTS_PER_MESSAGE); this call is one logical
                    # blast regardless of how many SES messages it turns into under the hood.
                    await call_blast_api(
                        emails,
                        request.subject,
                        request.html_content,
                        EmailProvider.SES,
                        None,
                        request.preview_text,
                        request.attachments,
                    )
                    logger.info(f"Blast API responded successfully for [{len(emails)}] recipients")

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
                logger.exception(e)

    logger.info("Preparing blast email")

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

    logger.info(f"Resolved [{len(guaranteed)}] guaranteed recipients")

    if request.provider == EmailProvider.GOOGLE:
        capped_count = min(request.count, get_total_remaining_send_capacity())
        if capped_count < request.count:
            logger.info(
                f"Capping requested count [{request.count}] down to [{capped_count}] based on remaining send capacity"
            )
    else:
        capped_count = request.count

    if request.order_by == "activity":
        pool = members_queries.get_blast_recipients_by_activity(
            session, limit=capped_count, exclude_ids=list(members_by_id.keys())
        )
    else:
        pool = members_queries.get_blast_recipients_alphabetical(
            session, limit=capped_count, exclude_ids=list(members_by_id.keys())
        )
    logger.info(f"Selected [{len(pool)}] recipients via [{request.order_by}] ordering")

    all_recipients = dict(guaranteed)
    for member in pool:
        if member.email:
            all_recipients.setdefault(member.email.lower(), {"name": member.name, "email": member.email})

    recipients = list(all_recipients.values())
    logger.info(f"Queuing blast to [{len(recipients)}] total recipients")

    background_tasks.add_task(
        send_blast_job, recipients, list(guaranteed.values()), request.count, requesting_member.id
    )

    return {
        "message": f"Blast email queued for [{len(recipients)}] recipient(s).",
        "recipient_count": len(recipients),
        "guaranteed_count": len(guaranteed),
        "algorithmic_count": len(pool),
    }


@router.post(
    "/blast/test", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)], response_model=EmailTestResponse
)
async def send_blast_test(request: BlastTestRequest):
    logger.info("Sending blast test email")
    logger.info(f"Sending test blast to [{len(request.test_emails)}] test emails")

    from_addr = await run_in_threadpool(get_from_address) if request.provider == EmailProvider.GOOGLE else None
    await call_blast_api(
        list(request.test_emails),
        request.subject,
        request.html_content,
        request.provider,
        from_addr,
        request.preview_text,
        request.attachments,
    )
    logger.info("Blast API responded successfully")

    return {"sent_count": len(request.test_emails), "emails": request.test_emails}


@router.get(
    "/blast/eligible-count",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=BlastEligibleCountResponse,
)
def get_blast_eligible_count(
    session: DB,
    provider: Annotated[
        EmailProvider, Query(description="Sending provider to compute remaining capacity for")
    ] = EmailProvider.GOOGLE,
):
    eligible_count = members_queries.get_blast_eligible_count(session)
    if provider == EmailProvider.GOOGLE:
        return {"eligible_count": eligible_count, "remaining_capacity": get_total_remaining_send_capacity()}
    return {"eligible_count": eligible_count, "remaining_capacity": None}


# endregion

# region ============== Email Template Endpoints ==============


@router.get(
    "/blast/templates",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=list[EmailTemplateOut],
)
def list_email_templates(session: DB):
    templates = email_template_queries.list_templates(session)
    return [EmailTemplateOut.model_validate(t, from_attributes=True) for t in templates]


@router.post(
    "/blast/templates",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(admin_guard)],
    response_model=EmailTemplateOut,
)
def create_email_template(request: EmailTemplateIn, requesting_member: CurrentMember, session: DB):
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


@router.put(
    "/blast/templates/{template_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=EmailTemplateOut,
)
def update_email_template(template_id: int, request: EmailTemplateIn, session: DB):
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


@router.delete(
    "/blast/templates/{template_id:int}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=MessageResponse,
)
def delete_email_template(template_id: int, session: DB):
    email_template_queries.delete_template(session, template_id)
    session.commit()
    return {"message": f"Template [{template_id}] deleted."}


# endregion
