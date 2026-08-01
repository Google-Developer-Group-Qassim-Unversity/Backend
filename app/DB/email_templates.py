from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB.schema import EmailTemplates
from app.exceptions import EmailTemplateNotFound


def create_template(
    session: Session, *, name: str, subject: str, html_content: str, preview_text: Optional[str], created_by: int
) -> EmailTemplates:
    template = EmailTemplates(
        name=name, subject=subject, html_content=html_content, preview_text=preview_text, created_by=created_by
    )
    session.add(template)
    session.flush()
    return template


def list_templates(session: Session) -> Sequence[EmailTemplates]:
    stmt = select(EmailTemplates).order_by(EmailTemplates.updated_at.desc())
    return session.scalars(stmt).all()


def get_template_by_id(session: Session, template_id: int) -> EmailTemplates:
    template = session.scalar(select(EmailTemplates).where(EmailTemplates.id == template_id))
    if template is None:
        raise EmailTemplateNotFound(template_id)
    return template


def update_template(
    session: Session, template_id: int, *, name: str, subject: str, html_content: str, preview_text: Optional[str]
) -> EmailTemplates:
    template = get_template_by_id(session, template_id)
    template.name = name
    template.subject = subject
    template.html_content = html_content
    template.preview_text = preview_text
    template.updated_at = datetime.now()
    session.flush()
    return template


def delete_template(session: Session, template_id: int) -> None:
    template = get_template_by_id(session, template_id)
    session.delete(template)
    session.flush()
