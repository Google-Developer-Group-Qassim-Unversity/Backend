"""The long-running email sends, run through BackgroundTasks.

These were closures nested inside their route handlers, which made them
unreachable from a test. They are top-level functions now, with everything they
used to capture from the enclosing route passed in explicitly.

Each opens its own session through db_session(): a background task runs after
the response is sent, and the request-scoped session from `get_db` is closed by
then.

Known limitation, unchanged by this move: the three async jobs below call
synchronous SQLAlchemy, and Starlette runs async background tasks on the event
loop. Making them sync would need sync variants of the gateway calls, which is
a behaviour change rather than a move.
"""

import logging

from app.config import config
from app.DB import emails as email_queries
from app.DB import events as events_queries
from app.DB.schema import EmailLogsEmailType, EmailLogsFromAddress, EmailProvider, Events
from app.routers.email_models import (
    CustomEmailRequest,
    CertificateLanguage,
    CertificateRequest,
    ManualCertificateRequest,
    SimpleEvent,
    SimpleMember,
)
from app.services.email_recipients import _resolve_event, _resolve_member
from app.services.email_capacity import _personalize, get_from_address, get_send_capacity
from app.services.job_tracker import EMAIL_JOB_QUERIES, job_boundary
from app.services.email_gateway import (
    call_blast_api,
    call_certificate_api,
    call_custom_email_api,
    call_direct_email_api,
)

logger = logging.getLogger(__name__)


def _member_label(member_item) -> str:
    """A recipient identifier for the job record, before the member is resolved."""
    member = getattr(member_item, "member", None)
    if member is not None and getattr(member, "email", None):
        return str(member.email)
    member_id = getattr(member_item, "member_id", None)
    return f"member_id={member_id}" if member_id is not None else str(member_item)


def send_certificates_by_event_id(
    event: Events, attendance: list, date_str: str, sent_by_id: int, event_id, job_id: int | None = None
):
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        event = events_queries.get_event_by_id(session, event_id)
        simple_event = SimpleEvent(name=event.name, date=date_str, official=bool(event.is_official))
        logger.info(f"Processing certificate sending for event [{event.name}] with [{len(attendance)}] attendees")

        already_sent = email_queries.get_members_who_received_certificate(session, event_id)
        attendance = [
            record for record in attendance if record.Member.id not in {member["id"] for member in already_sent}
        ]
        logger.info(
            f"Filtered out [{len(already_sent)}] attendees who already received certificates, remaining attendees to process: [{len(attendance)}]"
        )
        for attendanceRecord in attendance:
            member = attendanceRecord.Member
            with tracker.recipient(member.email):
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
                call_certificate_api(cert_request)
                logger.info("Certificate API responded with 200 OK")
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


def send_manual_certificates_job(request_data: ManualCertificateRequest, sent_by_id: int, job_id: int | None = None):
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        from_address = get_from_address() if request_data.provider == EmailProvider.GOOGLE else None
        simple_event, event_id = _resolve_event(request_data, session)
        logger.info(
            f"Processing manual certificates for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
        )

        for member_item in request_data.members:
            with tracker.recipient(_member_label(member_item)):
                simple_member, member_id = _resolve_member(member_item, session)
                logger.info(f"Sending certificate for member [{simple_member.name}] with email [{simple_member.email}]")
                cert_request = CertificateRequest(
                    event=simple_event,
                    member=simple_member,
                    language=request_data.language,
                    provider=request_data.provider,
                    from_address=from_address,
                )
                call_certificate_api(cert_request)
                logger.info("Certificate API responded with 200 OK")
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


async def send_custom_email_job(
    request_data: CustomEmailRequest,
    simple_event: SimpleEvent,
    event_id: int,
    sent_by_id: int,
    job_id: int | None = None,
):
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        from_address = get_from_address()
        logger.info(
            f"Processing custom email for event [{simple_event.name}] with [{len(request_data.members)}] recipients"
        )

        for member_item in request_data.members:
            with tracker.recipient(_member_label(member_item)):
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
                            {"filename": a.filename, "content_type": a.content_type} for a in request_data.attachments
                        ],
                    },
                )
                session.commit()


async def send_direct_email_job(
    recipients: list[dict],
    sent_by_id: int,
    provider: EmailProvider,
    from_address: EmailLogsFromAddress | None,
    request,
    job_id: int | None = None,
):
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        logger.info(f"Sending direct email to [{len(recipients)}] recipients")
        for recipient in recipients:
            with tracker.recipient(recipient["email"]):
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


async def send_blast_job(
    recipients: list[dict],
    guaranteed_snapshot: list[dict],
    requested_count: int,
    sent_by_id: int,
    request,
    job_id: int | None = None,
):
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
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
                with tracker.recipient(f"chunk via {from_addr.value}"):
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
                    tracker.success(len(emails) - 1)
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
