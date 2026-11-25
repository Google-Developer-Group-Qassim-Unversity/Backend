"""Database queries for encrypted Google OAuth integration credentials."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.DB.schema import GoogleOAuthCredentials


def get_oauth_credential(session: Session, service: str) -> GoogleOAuthCredentials | None:
    stmt = (
        select(GoogleOAuthCredentials)
        .options(joinedload(GoogleOAuthCredentials.connector))
        .where(GoogleOAuthCredentials.service == service)
    )
    return session.execute(stmt).scalar_one_or_none()


def upsert_oauth_credential(
    session: Session,
    *,
    service: str,
    encrypted_refresh_token: str,
    account_email: str,
    connected_by: int | None,
    now: datetime,
) -> GoogleOAuthCredentials:
    existing = get_oauth_credential(session, service)
    if existing is None:
        row = GoogleOAuthCredentials(
            service=service,
            encrypted_refresh_token=encrypted_refresh_token,
            account_email=account_email,
            connected_by=connected_by,
            connected_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    existing.encrypted_refresh_token = encrypted_refresh_token
    existing.account_email = account_email
    existing.connected_by = connected_by
    existing.connected_at = now
    existing.updated_at = now
    session.flush()
    return existing
