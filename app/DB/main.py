from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker

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


engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=DB_POOL_RECYCLE_SECONDS,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=DB_POOL_TIMEOUT_SECONDS,
    pool_use_lifo=True,
    connect_args=_build_connect_args(config.DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, expire_on_commit=False)
