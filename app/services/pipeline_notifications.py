"""Telling departments what happened to a request: dashboard notifications and emails.

- ``notify`` writes a notification row for one department. Everyone who acts
  for that department sees it; each person marks their own read.
- ``department_email`` prepares the email for a department. Every current
  member of the department is a recipient, permission or not.

**Trial mode.** Until ``PIPELINE_EMAILS_LIVE`` is on, the department gets no
email at all. The same email goes only to the person whose action sent it -
a super admin testing the flow - marked as a trial copy and listing who would
have received it. Turning the setting on is the only change needed to go live.

Emails are sent after the transition commits, as one ``email_jobs`` row each,
through the same blast endpoint and job tracker as the other emails. A failed
email never undoes the step that sent it; it is recorded on its job.
"""

import html
import logging
from dataclasses import dataclass, field

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import config
from app.DB import club_structure as club_queries
from app.DB import email_jobs as job_queries
from app.DB import pipeline_teams as team_queries
from app.DB import emails as email_queries
from app.DB.schema import (
    EmailJobsType,
    EmailLogsEmailType,
    EmailProvider,
    EventRequests,
    Members,
    PipelineNotificationKind,
    PipelineNotificationReads,
    PipelineNotifications,
)
from app.semesters import current_semester
from app.services.email_capacity import get_from_address
from app.services.email_gateway import call_blast_api
from app.services.job_tracker import EMAIL_JOB_QUERIES, job_boundary

logger = logging.getLogger(__name__)


def notify(
    session: Session,
    department_id: int,
    request: EventRequests,
    kind: PipelineNotificationKind,
    payload: dict | None = None,
) -> PipelineNotifications:
    notification = PipelineNotifications(
        department_id=department_id, request_id=request.id, kind=kind, payload=payload or {}
    )
    session.add(notification)
    session.flush()
    return notification


def department_members(session: Session, department_id: int) -> list[Members]:
    """Everyone in the department's current roster, once each, whatever their role."""
    semester = current_semester(session)
    if semester is None:
        return []
    people: dict[int, Members] = {}
    for row in club_queries.get_memberships(session, semester.id, department_id):
        people.setdefault(row.member_id, row.member)
    return list(people.values())


@dataclass
class PendingEmail:
    """An email ready to send once the transition that caused it has committed."""

    emails: list[str]
    subject: str
    html: str
    sent_by_id: int
    data: dict = field(default_factory=dict)


# (subject, heading) per kind, Arabic then English.
_COPY = {
    PipelineNotificationKind.REQUEST_RECEIVED: (
        "طلب فعالية جديد لقسمكم · New event request",
        "وصل لقسمكم طلب فعالية جديد",
        "Your department received a new event request",
    ),
    PipelineNotificationKind.MEDIA_RECEIVED: (
        "طلب فعالية للإعلام · Event request for Media",
        "انتهى التصميم، والطلب الآن عند الإعلام",
        "Design is done; the request is now with Media",
    ),
    PipelineNotificationKind.RETURNED: (
        "أُعيد طلبكم للتعديل · Your request was returned",
        "أعاد قسم التصميم طلبكم مع ملاحظات. لديكم ١٢ ساعة للتعديل.",
        "Design returned your request with notes. You have 12 hours to fix it.",
    ),
    PipelineNotificationKind.READY_TO_PUBLISH: (
        "فعاليتكم جاهزة للنشر · Your event is ready to publish",
        "أنهت كل الأقسام عملها، ويمكنكم نشر الفعالية الآن",
        "Every team is done; you can publish the event now",
    ),
    PipelineNotificationKind.HOLD_EXPIRED: (
        "انتهت مهلة حجز تواريخكم · Your date hold ran out",
        "انتهت مهلة الـ٢٤ ساعة فأُلغي حجز التواريخ. البيانات محفوظة؛ احجزوا تواريخ جديدة.",
        "The 24-hour hold ran out and the dates were released. Everything is kept; book new dates.",
    ),
}


def _render(request: EventRequests, kind: PipelineNotificationKind, department_name: str, note: str | None) -> str:
    _, ar_heading, en_heading = _COPY[kind]
    link = f"{config.ADMIN_APP_URL}/pipeline/requests/{request.id}"
    title = html.escape(request.title or f"#{request.id}")
    dates = f"{request.start_date} → {request.end_date}" if request.start_date else "-"
    requester = html.escape(request.department.ar_name if request.department else "")
    note_html = f"<p><b>ملاحظات · Notes:</b><br>{html.escape(note)}</p>" if note else ""
    return (
        f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif">'
        f"<h2>{ar_heading}</h2>"
        f"<p><b>الفعالية:</b> {title}<br><b>القسم الطالب:</b> {requester}<br><b>التواريخ:</b> {dates}</p>"
        f"{note_html}"
        f'<p><a href="{link}">فتح الطلب</a></p></div><hr>'
        f'<div dir="ltr" style="font-family:Arial,sans-serif">'
        f"<h2>{en_heading}</h2>"
        f"<p><b>Event:</b> {title}<br><b>To:</b> {html.escape(department_name)}<br><b>Dates:</b> {dates}</p>"
        f'<p><a href="{link}">Open the request</a></p></div>'
    )


def department_email(
    session: Session,
    department_id: int,
    request: EventRequests,
    kind: PipelineNotificationKind,
    triggered_by: Members,
    note: str | None = None,
) -> PendingEmail | None:
    """The email for ``department_id`` - or, in trial mode, its copy for ``triggered_by``."""
    departments = team_queries.get_departments(session, {department_id})
    department_name = departments[0].name if departments else str(department_id)
    members = [m for m in department_members(session, department_id) if m.email]
    subject, _, _ = _COPY[kind]
    body = _render(request, kind, department_name, note)
    data = {"pipeline": {"request_id": request.id, "kind": kind.value, "department_id": department_id}}

    if config.PIPELINE_EMAILS_LIVE:
        emails = [m.email for m in members if m.email]
    else:
        if not triggered_by.email:
            logger.info("Trial pipeline email for request %s skipped: the trigger has no email", request.id)
            return None
        emails = [triggered_by.email]
        would_have = "".join(f"<li>{html.escape(m.name)} &lt;{html.escape(m.email or '')}&gt;</li>" for m in members)
        banner = (
            '<div style="background:#fef7e0;border:1px solid #fbbc04;padding:12px;margin-bottom:16px;'
            'font-family:Arial,sans-serif">'
            "<b>نسخة تجريبية · Trial copy.</b> "
            f"This email goes to you because you did this step. When the pipeline is live it goes to every "
            f"member of {html.escape(department_name)} ({len(members)}):"
            f"<ul>{would_have or '<li>(no members with an email this semester)</li>'}</ul></div>"
        )
        subject = f"[تجربة · Trial] {subject}"
        body = banner + body
        data["pipeline"]["trial"] = True
        data["pipeline"]["would_have_sent_to"] = [m.email for m in members]

    if not emails:
        return None
    return PendingEmail(emails=emails, subject=subject, html=body, sent_by_id=triggered_by.id, data=data)


async def send_pipeline_email_job(pending: PendingEmail, job_id: int | None = None) -> None:
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        from_address = get_from_address()
        await call_blast_api(
            pending.emails, pending.subject, pending.html, EmailProvider.GOOGLE, from_address, None, []
        )
        email_queries.create_email_log(
            session,
            sent_by=pending.sent_by_id,
            from_address=from_address.value,
            email_type=EmailLogsEmailType.BLAST,
            recipient_count=len(pending.emails),
            data={"subject": pending.subject, "recipients": pending.emails, **pending.data},
        )
        session.commit()
        tracker.success(len(pending.emails))


def send_after_commit(session: Session, background_tasks: BackgroundTasks, pending: list[PendingEmail | None]) -> None:
    """Queue the emails of a transition that has already committed.

    Nothing here may undo that transition, so a failure to queue is logged
    rather than raised: the step happened, only its email did not.
    """
    for email in pending:
        if email is None:
            continue
        try:
            job = job_queries.create_job(session, EmailJobsType.BLAST, email.sent_by_id, total=len(email.emails))
            session.commit()
            background_tasks.add_task(send_pipeline_email_job, email, job.id)
        except Exception:
            session.rollback()
            logger.exception("Could not queue a pipeline email (%s)", email.subject)


def list_for(
    session: Session, department_ids: set[int] | None, member_id: int, unread_only: bool, limit: int, offset: int
):
    read_ids = select(PipelineNotificationReads.notification_id).where(PipelineNotificationReads.member_id == member_id)
    statement = select(PipelineNotifications)
    if department_ids is not None:
        statement = statement.where(PipelineNotifications.department_id.in_(department_ids))
    unread = statement.where(PipelineNotifications.id.not_in(read_ids))
    if unread_only:
        statement = unread
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    unread_count = session.scalar(select(func.count()).select_from(unread.subquery())) or 0
    rows = session.scalars(
        statement.order_by(PipelineNotifications.created_at.desc(), PipelineNotifications.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    read = set(
        session.scalars(read_ids.where(PipelineNotificationReads.notification_id.in_([r.id for r in rows]))).all()
    )
    return total, unread_count, rows, read


def mark_read(session: Session, member_id: int, notification_ids: list[int]) -> int:
    if not notification_ids:
        return 0
    already = set(
        session.scalars(
            select(PipelineNotificationReads.notification_id).where(
                PipelineNotificationReads.member_id == member_id,
                PipelineNotificationReads.notification_id.in_(notification_ids),
            )
        ).all()
    )
    new = [n for n in notification_ids if n not in already]
    for notification_id in new:
        session.add(PipelineNotificationReads(notification_id=notification_id, member_id=member_id))
    session.flush()
    return len(new)
