"""Shared FastAPI dependencies."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.DB.main import get_sessionmaker


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session.

    Rolls back if the handler raises, and always closes. It deliberately does
    *not* commit: a dependency's exit code runs after the response has been
    sent, so a failed commit there would be invisible to the client. Handlers
    that write must call ``db.commit()`` themselves.
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DB = Annotated[Session, Depends(get_db)]
