"""Telling departments what happened to a request: dashboard notifications and emails.

- ``notify`` writes a notification row for one department. Everyone who acts
  for that department sees it; each person marks their own read.
- ``department_email`` prepares the email for a department. Every current
  member of the department is a recipient, permission or not.

Staging and local development never reach the department: every send
there goes to whoever clicked instead (``app/services/email_redirect.py``).

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
    # False when nobody clicked: the sweep attributes its emails to whoever
    # booked the event, a real member, not to someone testing the app.
    clicked: bool = True


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
    PipelineNotificationKind.DATES_CHANGED: (
        "تغيّرت تواريخ فعاليتكم · Your event's dates changed",
        "نقل قسم اللوجستيات فعاليتكم إلى تواريخ أخرى حسب الحجز المتاح",
        "Logistics moved your event to other dates, the ones it could book",
    ),
    PipelineNotificationKind.CONFIRMATION_CHANGED: (
        "تغيّرت تفاصيل فعاليتكم · Your event's details changed",
        "أكّد قسم اللوجستيات فعاليتكم بتفاصيل تختلف عمّا طلبتم، حسب الحجز المتاح",
        "Logistics confirmed your event with details that differ from what you asked for",
    ),
    PipelineNotificationKind.CANCELLED: (
        "أُلغي طلب فعالية · An event request was cancelled",
        "ألغى القسم الطالب هذا الطلب، فلا حاجة لإكمال العمل عليه",
        "The requesting department cancelled this request; there is nothing left to do on it",
    ),
    PipelineNotificationKind.HOLD_EXPIRED: (
        "انتهت مهلة حجز تواريخكم · Your date hold ran out",
        "انتهت مهلة الـ٢٤ ساعة فأُلغي حجز التواريخ. البيانات محفوظة؛ احجزوا تواريخ جديدة.",
        "The 24-hour hold ran out and the dates were released. Everything is kept; book new dates.",
    ),
    PipelineNotificationKind.OVERRIDDEN: (
        "حُجزت تواريخ فعاليتكم لفعالية أخرى · Your event's dates were taken",
        "حجز مشرف عام تواريخ فعاليتكم لفعالية أخرى",
        "A super admin booked your event's dates for another event",
    ),
}


# What Logistics can confirm differently (app/services/event_deliverables.CONFIRMED_FIELDS), Arabic then English.
_FIELD_LABELS = {
    "start_date": ("اليوم الأول", "First day"),
    "end_date": ("اليوم الأخير", "Last day"),
    "day_modes": ("نوع كل يوم", "Each day's mode"),
    "daily_start_time": ("وقت البداية", "Start time"),
    "daily_end_time": ("وقت النهاية", "End time"),
    "venue": ("المكان", "Venue"),
    "room": ("القاعة", "Room"),
    "event_type": ("نوع الفعالية", "Event type"),
    "description": ("الوصف", "Description"),
}
_MODE_LABELS = {"on_site": ("حضوري", "On site"), "online": ("عن بُعد", "Online")}


def _shown(field: str, value, language: int) -> str:
    """A changed field's value as the email shows it: blanks as a dash, times without seconds, modes per day."""
    if value is None or value == "":
        return "—"
    if field in ("daily_start_time", "daily_end_time") and isinstance(value, str):
        return value[:5]
    if field == "day_modes" and isinstance(value, dict):
        return ", ".join(
            f"{day}: {_MODE_LABELS.get(str(mode), (str(mode), str(mode)))[language]}" for day, mode in value.items()
        )
    return str(value)


def _changes_html(changes: list[dict], reason: str | None, language: int) -> str:
    """What changed, each as "label: from → to", then the reason. ``language`` is 0 for Arabic, 1 for English."""
    if not changes and not reason:
        return ""
    heading = ("ما تغيّر عمّا طلبتم", "What changed from your request")[language]
    reason_label = ("سبب التغيير", "Why")[language]
    items = "".join(
        f"<li><b>{html.escape(_FIELD_LABELS.get(c['field'], (c['field'], c['field']))[language])}:</b> "
        f"<bdi>{html.escape(_shown(c['field'], c.get('from'), language))}</bdi> → "
        f"<bdi>{html.escape(_shown(c['field'], c.get('to'), language))}</bdi></li>"
        for c in changes
    )
    parts = [f"<p><b>{heading}:</b></p><ul>{items}</ul>" if items else ""]
    if reason:
        parts.append(f"<p><b>{reason_label}:</b><br>{html.escape(reason).replace(chr(10), '<br>')}</p>")
    return "".join(parts)


def _render(
    request: EventRequests,
    kind: PipelineNotificationKind,
    department_name: str,
    note: str | None,
    changes: list[dict] | None = None,
    reason: str | None = None,
) -> str:
    _, ar_heading, en_heading = _COPY[kind]
    link = f"{config.ADMIN_APP_URL}/pipeline/requests/{request.id}"
    title = html.escape(request.title or f"#{request.id}")
    dates = f"{request.start_date} → {request.end_date}" if request.start_date else "-"
    requester = html.escape(request.department.ar_name if request.department else "")
    note_html = f"<p><b>ملاحظات · Notes:</b><br>{html.escape(note).replace(chr(10), '<br>')}</p>" if note else ""
    return (
        f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif">'
        f"<h2>{ar_heading}</h2>"
        f"<p><b>الفعالية:</b> {title}<br><b>القسم الطالب:</b> {requester}<br><b>التواريخ:</b> {dates}</p>"
        f"{note_html}{_changes_html(changes or [], reason, 0)}"
        f'<p><a href="{link}">فتح الطلب</a></p></div><hr>'
        f'<div dir="ltr" style="font-family:Arial,sans-serif">'
        f"<h2>{en_heading}</h2>"
        f"<p><b>Event:</b> {title}<br><b>To:</b> {html.escape(department_name)}<br><b>Dates:</b> {dates}</p>"
        f"{_changes_html(changes or [], reason, 1)}"
        f'<p><a href="{link}">Open the request</a></p></div>'
    )


def department_email(
    session: Session,
    department_id: int,
    request: EventRequests,
    kind: PipelineNotificationKind,
    triggered_by: Members,
    note: str | None = None,
    changes: list[dict] | None = None,
    reason: str | None = None,
) -> PendingEmail | None:
    """The email for every current member of ``department_id``, sent on ``triggered_by``'s behalf.

    ``changes`` and ``reason`` are what Logistics confirmed differently and why
    (``event_deliverables.changes``), listed in both languages.
    """
    departments = team_queries.get_departments(session, {department_id})
    department_name = departments[0].name if departments else str(department_id)
    members = [m for m in department_members(session, department_id) if m.email]
    subject, _, _ = _COPY[kind]
    body = _render(request, kind, department_name, note, changes, reason)
    data = {"pipeline": {"request_id": request.id, "kind": kind.value, "department_id": department_id}}

    emails = [m.email for m in members if m.email]
    if not emails:
        return None
    return PendingEmail(emails=emails, subject=subject, html=body, sent_by_id=triggered_by.id, data=data)


async def send_pipeline_email_job(pending: PendingEmail, job_id: int | None = None) -> None:
    with job_boundary(job_id, EMAIL_JOB_QUERIES) as (tracker, session):
        from_address = get_from_address()
        await call_blast_api(
            pending.emails,
            pending.subject,
            pending.html,
            EmailProvider.GOOGLE,
            from_address,
            None,
            [],
            on_behalf_of=pending.sent_by_id if pending.clicked else None,
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


def mark_read(session: Session, member_id: int, notification_ids: list[str]) -> int:
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
