from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.DB.schema import EmailLogs, EmailLogsEmailType, EmailLogsFromAddress, Events, Members
from app.exceptions import EventNotFound, MemberNotFound


def get_email_address_usage(session: Session, days: int, address: EmailLogsFromAddress) -> int:
    # Returns the total number of recipients emailed from `address` in the last `days` days.
    # Each row's `recipient_count` is summed (since a single email log can represent a batch).
    from_address = EmailLogsFromAddress(address.value)
    cutoff = datetime.now() - timedelta(days=days)
    stmt = select(func.coalesce(func.sum(EmailLogs.recipient_count), 0)).where(
        EmailLogs.from_address == from_address, EmailLogs.sent_at >= cutoff
    )
    result = session.scalar(stmt)
    return result if isinstance(result, int) else 0

def get_members_who_received_certificate(session: Session, event_id: int):
    stmt = (
        select(Members.id, Members.name, Members.email)
        .join(EmailLogs, EmailLogs.member_id == Members.id)
        .where(EmailLogs.event_id == event_id, EmailLogs.email_type == EmailLogsEmailType.EVENT_CERTIFICATE)
    )
    return session.execute(stmt).mappings().all()

def get_event_certificate_email_log(session: Session, event_id: int, offset: int = 0, limit: int = 100):
    stmt = (
        select(
            EmailLogs.id,
            Members.name.label("member_name"),
            Members.email.label("member_email"),
            EmailLogs.sent_at,
            EmailLogs.from_address,
        )
        .join(Members, EmailLogs.member_id == Members.id)
        .where(EmailLogs.event_id == event_id, EmailLogs.email_type == EmailLogsEmailType.EVENT_CERTIFICATE)
        .order_by(EmailLogs.sent_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return session.execute(stmt).mappings().all()


def create_email_log(
    session: Session,
    *,
    sent_by: int,
    from_address: EmailLogsFromAddress,
    email_type: EmailLogsEmailType,
    member_id: Optional[int] = None,
    event_id: Optional[int] = None,
    recipient_count: int = 1,
    data: Optional[dict] = None,
) -> EmailLogs:
    log = EmailLogs(
        member_id=member_id,
        event_id=event_id,
        from_address=EmailLogsFromAddress(from_address.value),
        sent_at=datetime.now(),
        sent_by=sent_by,
        recipient_count=recipient_count,
        email_type=email_type,
        data=data,
    )
    session.add(log)
    session.flush()
    return log


def get_email_logs(session: Session, limit: int = 100, offset: int = 0):
    stmt = select(EmailLogs).order_by(EmailLogs.sent_at.desc()).offset(offset).limit(limit)
    return session.scalars(stmt).all()


def get_email_logs_by_event_id(session: Session, event_id: int):
    if not session.scalar(select(Events).where(Events.id == event_id)):
        raise EventNotFound(event_id)
    stmt = select(EmailLogs).where(EmailLogs.event_id == event_id).order_by(EmailLogs.sent_at.desc())
    return session.scalars(stmt).all()


def get_email_logs_by_member_id(session: Session, member_id: int):
    if not session.scalar(select(Members).where(Members.id == member_id)):
        raise MemberNotFound(member_id)
    stmt = select(EmailLogs).where(EmailLogs.member_id == member_id).order_by(EmailLogs.sent_at.desc())
    return session.scalars(stmt).all()
