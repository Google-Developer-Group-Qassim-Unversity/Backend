"""The Mail outbox: one row per job, with its log totals and recipient history."""

from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import JSON, Select, and_, func, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.orm import Session

from app.DB.schema import EmailJobs, EmailJobsType, EmailLogs, EmailLogsEmailType, Events, Members
from app.exceptions import NotFound
from app.routers.email_models import (
    CertificateFormat,
    CertificateGenerationRequest,
    CertificateLanguage,
    Send,
    SendAttachment,
    SendContent,
    SendDetail,
    SendEvent,
    SendFailure,
    SendFilter,
    SendFromAddress,
    SendKind,
    SendRecipient,
    SendSender,
    SendsPage,
)
from app.services.email_capacity import format_event_date

KINDS: dict[EmailJobsType, SendKind] = {
    EmailJobsType.BLAST: "campaign",
    EmailJobsType.EVENT_CERTIFICATE: "event-certificate",
    EmailJobsType.MANUAL_CERTIFICATE: "manual-certificate",
    EmailJobsType.ACCEPTANCE: "acceptance",
    EmailJobsType.CUSTOM_EMAIL: "event-message",
    EmailJobsType.DIRECT_EMAIL: "direct",
    EmailJobsType.TEAM_NOTIFICATION: "team-notification",
}


def get_certificate_sample_request(session: Session, job_id: int) -> tuple[CertificateGenerationRequest, int | None]:
    job = session.get(EmailJobs, job_id)
    if job is None:
        raise NotFound("Email send", job_id)
    if job.job_type not in (EmailJobsType.EVENT_CERTIFICATE, EmailJobsType.MANUAL_CERTIFICATE):
        raise HTTPException(status_code=422, detail="Email send is not a certificate send")

    # Certificate jobs write a log only after the gateway succeeds. Failures
    # live on the job and must never be candidates for a sample.
    log = session.scalar(
        select(EmailLogs)
        .where(
            EmailLogs.job_id == job_id,
            EmailLogs.email_type == EmailLogsEmailType(job.job_type.value),
            EmailLogs.recipient_count > 0,
        )
        .order_by(func.rand())
        .limit(1)
    )
    if log is None:
        raise HTTPException(status_code=422, detail="Email send has no successfully sent certificate recipients")

    data = log.data or {}
    member_data = dict(data.get("member") or {})
    event_data = dict(data.get("event") or {})
    # Prefer the snapshots, including manual sends with no member/event row.
    # Older logs may need fields filled from their linked rows.
    if not {"name", "email", "gender"} <= member_data.keys() and log.member_id is not None:
        member = session.get(Members, log.member_id)
        if member is not None:
            member_data = {"name": member.name, "email": member.email, "gender": member.gender, **member_data}
    event_id = log.event_id if log.event_id is not None else job.event_id
    if not {"name", "date", "official"} <= event_data.keys() and event_id is not None:
        event = session.get(Events, event_id)
        if event is not None:
            event_data = {
                "name": event.name,
                "date": format_event_date(event),
                "official": bool(event.is_official),
                **event_data,
            }
    try:
        request = CertificateGenerationRequest.model_validate(
            {
                "language": CertificateLanguage.ARABIC,
                "format": CertificateFormat.PNG,
                "event": event_data,
                "member": member_data,
            }
        )
    except ValidationError:
        raise HTTPException(status_code=422, detail="Certificate recipient or event data is unavailable") from None
    return request, log.member_id


def _send_query() -> Select[Any]:
    return (
        select(
            EmailJobs.id,
            EmailJobs.job_type,
            EmailJobs.status,
            EmailJobs.subject,
            EmailJobs.event_id,
            Events.name.label("event_name"),
            EmailJobs.created_by,
            Members.name.label("sender_name"),
            EmailJobs.total,
            EmailJobs.succeeded,
            EmailJobs.failed,
            EmailJobs.created_at,
            EmailJobs.finished_at,
        )
        .join(Members, Members.id == EmailJobs.created_by)
        .outerjoin(Events, Events.id == EmailJobs.event_id)
    )


def _as_send(row: RowMapping, addresses: list[SendFromAddress]) -> Send:
    return Send(
        id=row["id"],
        kind=KINDS[row["job_type"]],
        status=row["status"],
        subject=row["subject"],
        event=SendEvent(id=row["event_id"], name=row["event_name"]) if row["event_id"] is not None else None,
        sender=SendSender(id=row["created_by"], name=row["sender_name"]),
        total=row["total"],
        succeeded=row["succeeded"],
        failed=row["failed"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        from_addresses=sorted(addresses, key=lambda address: address.address),
    )


def _email_in_log(email: str | ColumnElement[str]) -> ColumnElement[bool]:
    # Extract addresses before lowercasing, so lookup never scans message HTML.
    # JSON_CONTAINS compares exact values, including literal % and _ characters.
    return or_(
        *[
            func.json_contains(func.lower(func.json_extract(EmailLogs.data, path)), func.json_quote(func.lower(email)))
            == 1
            for path in (
                "$.recipient.email",
                "$.member.email",
                "$.member[*].email",
                "$.recipients[*].email",
                "$.recipients",
            )
        ]
    )


def get_sends(
    session: Session,
    *,
    kind: SendFilter | None = None,
    member_id: int | None = None,
    email: str | None = None,
    before: int | None = None,
    limit: int = 30,
) -> SendsPage:
    stmt = _send_query()
    if kind == "certificate":
        stmt = stmt.where(EmailJobs.job_type.in_([EmailJobsType.EVENT_CERTIFICATE, EmailJobsType.MANUAL_CERTIFICATE]))
    elif kind is not None:
        stmt = stmt.where(EmailJobs.job_type.in_([job_type for job_type, value in KINDS.items() if value == kind]))
    else:
        stmt = stmt.where(EmailJobs.job_type != EmailJobsType.TEAM_NOTIFICATION)

    if member_id is not None or email is not None:
        matches = []
        if member_id is not None:
            member_email = select(Members.email).where(Members.id == member_id).scalar_subquery()
            matches.extend([EmailLogs.member_id == member_id, _email_in_log(member_email)])
        if email is not None:
            email = email.strip().lower()
            matching_members = select(Members.id).where(func.lower(Members.email) == email)
            matches.extend([EmailLogs.member_id.in_(matching_members), _email_in_log(email)])
        stmt = stmt.where(select(EmailLogs.id).where(EmailLogs.job_id == EmailJobs.id, or_(*matches)).exists())

    if before is not None:
        cursor_date = select(EmailJobs.created_at).where(EmailJobs.id == before).scalar_subquery()
        stmt = stmt.where(
            or_(EmailJobs.created_at < cursor_date, and_(EmailJobs.created_at == cursor_date, EmailJobs.id < before))
        )
    # Backfilled history has newer ids but older dates. The id cursor resolves
    # its creation date in this query, keeping pagination chronological.
    page = stmt.order_by(EmailJobs.created_at.desc(), EmailJobs.id.desc()).limit(limit + 1).cte("page")
    counts = (
        select(EmailLogs.job_id, EmailLogs.from_address, func.sum(EmailLogs.recipient_count).label("count"))
        .join(page, page.c.id == EmailLogs.job_id)
        .group_by(EmailLogs.job_id, EmailLogs.from_address)
        .cte("address_counts")
    )
    addresses = (
        select(
            counts.c.job_id,
            func.json_arrayagg(
                func.json_object("address", counts.c.from_address, "count", counts.c.count), type_=JSON
            ).label("from_addresses"),
        )
        .group_by(counts.c.job_id)
        .cte("addresses")
    )
    rows = (
        session.execute(
            select(page, addresses.c.from_addresses)
            .outerjoin(addresses, addresses.c.job_id == page.c.id)
            .order_by(page.c.created_at.desc(), page.c.id.desc())
        )
        .mappings()
        .all()
    )
    items = [
        _as_send(row, [SendFromAddress.model_validate(address) for address in (row["from_addresses"] or [])])
        for row in rows[:limit]
    ]
    return SendsPage(items=items, next_before=items[-1].id if len(rows) > limit else None)


def get_send(session: Session, job_id: int) -> SendDetail:
    row = session.execute(_send_query().where(EmailJobs.id == job_id)).mappings().one_or_none()
    if row is None:
        raise NotFound("Email send", job_id)
    logs = session.scalars(select(EmailLogs).where(EmailLogs.job_id == job_id).order_by(EmailLogs.id)).all()
    addresses: dict[str, int] = defaultdict(int)
    snapshots: list[dict[str, Any]] = []
    content: SendContent | None = None
    for log in logs:
        addresses[log.from_address] += log.recipient_count
        data = log.data or {}
        # Older acceptance sends use `member` for a list, certificates for an object.
        for key in ("recipients", "recipient", "member"):
            value = data.get(key)
            if value is None:
                continue
            for recipient in value if isinstance(value, list) else [value]:
                snapshot: dict[str, Any] = {"email": recipient} if isinstance(recipient, str) else dict(recipient)
                snapshot["member_id"] = snapshot.get("member_id") or log.member_id
                snapshots.append(snapshot)
        if log.member_id is not None and not any(s.get("member_id") == log.member_id for s in snapshots):
            snapshots.append({"member_id": log.member_id})
        if (
            content is None
            and log.email_type.value not in ("event-certificate", "manual-certificate")
            and any(data.get(key) is not None for key in ("subject", "preview_text", "html_content", "attachments"))
        ):
            content = SendContent(
                subject=data.get("subject"),
                preview_text=data.get("preview_text"),
                html_content=data.get("html_content"),
                attachments=[
                    SendAttachment(filename=a["filename"], url=a["url"])
                    for a in data.get("attachments", [])
                    if a.get("filename") and a.get("url")
                ],
            )
    # Resolve names and member ids once for all snapshots, including bulk logs
    # which saved only addresses. Snapshot addresses win if a member changed email.
    member_ids = {s["member_id"] for s in snapshots if s.get("member_id") is not None}
    emails = {s["email"].strip().lower() for s in snapshots if s.get("email")}
    members = session.scalars(
        select(Members).where(or_(Members.id.in_(member_ids), func.lower(Members.email).in_(emails)))
    ).all()
    by_id = {m.id: m for m in members}
    by_email = {m.email.lower(): m for m in members if m.email}
    recipients: dict[str, SendRecipient] = {}
    for snapshot in snapshots:
        snapshot_member_id = snapshot.get("member_id")
        member = (by_id.get(snapshot_member_id) if snapshot_member_id is not None else None) or by_email.get(
            str(snapshot.get("email", "")).strip().lower()
        )
        email_address = snapshot.get("email") or (member.email if member else None)
        if not email_address:
            continue
        recipients.setdefault(
            email_address.strip().lower(),
            SendRecipient(
                name=snapshot.get("name") or (member.name if member else None),
                email=email_address,
                member_id=member.id if member else snapshot.get("member_id"),
            ),
        )
    failures = session.scalar(select(EmailJobs.failures).where(EmailJobs.id == job_id)) or []
    send = _as_send(
        row, [SendFromAddress(address=address, count=count) for address, count in sorted(addresses.items())]
    )
    return SendDetail(
        **send.model_dump(),
        recipients=list(recipients.values()),
        failures=[SendFailure.model_validate(failure) for failure in failures],
        content=content,
    )
