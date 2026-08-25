"""Database engine and session factory.

Both are created lazily so that importing this module has no side effects and
requires no environment variables. Request-scoped sessions come from the
``get_db`` dependency in :mod:`app.dependencies`; background tasks, scripts and
other non-request code use :func:`db_session`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import config


DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 5
DB_POOL_TIMEOUT_SECONDS = 8
DB_POOL_RECYCLE_SECONDS = 600
DB_CONNECT_TIMEOUT_SECONDS = 5
DB_READ_TIMEOUT_SECONDS = 20
DB_WRITE_TIMEOUT_SECONDS = 20


def _build_connect_args(database_url: str) -> dict[str, int]:
    url = make_url(database_url)
    if not url.drivername.startswith("mysql"):
        return {}
    return {
        "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
        "read_timeout": DB_READ_TIMEOUT_SECONDS,
        "write_timeout": DB_WRITE_TIMEOUT_SECONDS,
    }


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine. Built on first use, not at import time."""
    return create_engine(
        config.DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=DB_POOL_RECYCLE_SECONDS,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT_SECONDS,
        pool_use_lifo=True,
        connect_args=_build_connect_args(config.DATABASE_URL),
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autocommit=False, expire_on_commit=False)


@contextmanager
def db_session() -> Iterator[Session]:
    """A standalone session for code outside the request cycle.

    Use this in background tasks and scripts. Request handlers should take the
    ``DB`` dependency instead so tests can override it.
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
