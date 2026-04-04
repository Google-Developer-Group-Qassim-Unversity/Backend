"""
Pytest configuration and fixtures for testing.
Fixture chain (scope):

    database_url (session)
        |
    engine (session) ─── runs Alembic migrations
        |
    seed_core_data (session) ─── seeds the DB
        |
        ├── db_session (function) ─── per-test session with rollback
        |
        └── client (function) ─── FastAPI TestClient (no auth overrides → 403 on guarded endpoints)
                |
                └── clerk_client (function) ─── bypasses authenticated_guard (Clerk credentials with member metadata)
                        |
                        ├── admin_client (function) ─── also bypasses admin_guard
                        |
                        └── super_admin_client (function) ─── also bypasses super_admin_guard
"""

import os
import pytest
from typing import Generator

from testcontainers.mysql import MySqlContainer

from sqlalchemy import create_engine
from alembic.config import Config
from alembic import command
from sqlalchemy.orm import Session, sessionmaker

from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from fastapi_clerk_auth import (
    HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials,
)
# A bunch more import are done insdie fixtures to avoid the problimatic pattern in the code which evaluates sessions and envirnoment varibles at import time,
# so we have to delay importing those modules until after the environment variables are set and the database is ready, otherwise we will get errors about missing env vars

# Set environment variables BEFORE importing app
# These must be set before any app modules are imported
required_env_vars = {
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
}

for key, value in required_env_vars.items():
    os.environ[key] = value  # Always set, override any existing


@pytest.fixture(scope="session")
def database_url():
    """
    Provide the test database URL.
    
    - If DATABASE_URL env var is set (CI mode), use it
    - Otherwise, start a MySQL testcontainer
    
    Yields the database URL string.
    """
    existing_url = os.getenv("DATABASE_URL")
    
    if existing_url:
        print(f"[conftest] Using provided DATABASE_URL")
        yield existing_url
        return
    
    print("[conftest] Starting MySQL testcontainer...")
    container = MySqlContainer("mysql:8.0", dbname="test")
    container.start()
    
    url = container.get_connection_url()
    url = url.replace("mysql://", "mysql+pymysql://")
    print(f"[conftest] MySQL testcontainer started: {url}")
    
    os.environ["DATABASE_URL"] = url
    
    yield url
    
    print("[conftest] Stopping MySQL testcontainer...")
    container.stop()


@pytest.fixture(scope="session")
def engine(database_url):
    """
    Create SQLAlchemy engine and run migrations.
    This ensures tests run against the same database structure as production.
    """
    
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    
    print("[conftest] Running Alembic migrations...")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception as e:
        print(f"[conftest] ❌ Migration failed: {e}")
        raise RuntimeError(
            f"Database migrations failed. "
            f"This is NOT a test failure - migrations are broken.\n"
            f"Fix your migration files first.\n"
            f"Original error: {e}"
        ) from e
    
    print("[conftest] ✓ Migrations completed")
    
    yield engine


@pytest.fixture(scope="session")
def seed_core_data(engine):
    from app.DB.schema import Actions, Departments, ActionsActionType, DepartmentsType

    # CAUTION: Don't update default unless you know what you're doing
    # a lot of tests assume these default values and changing them might break the tests
    with Session(engine) as session:
        session.add_all([
            Actions(
                action_name="organized an on-site course",
                points=10,
                action_type=ActionsActionType.DEPARTMENT,
                ar_action_name="تنظيم دورة حضورية",
            ),
            Actions(
                action_name="on-site course attendance",
                points=5,
                action_type=ActionsActionType.MEMBER,
                ar_action_name="حضور دورة حضورية",
            ),
            Departments(
                name="Business",
                type=DepartmentsType.PRACTICAL,
                ar_name="ريادة الأعمال",
            ),
            Departments(
                name="Design",
                type=DepartmentsType.ADMINISTRATIVE,
                ar_name="التصميم",
            ),
        ])
        session.commit()
        print("[conftest] ✓ Core data seeded")


@pytest.fixture(scope="function")
def client(engine, seed_core_data) -> Generator:
    """
    Provide a FastAPI test client with transaction rollback.

    Reconfigures SessionLocal **in-place** so every module that imported it
    (via ``from app.DB.main import SessionLocal``) will create sessions bound
    to a single test-scoped connection.  After the test the transaction is
    rolled back, undoing every INSERT/UPDATE/DELETE the routes committed.
    """
    import app.DB.main as db_main
    from app.main import app

    connection = engine.connect()
    transaction = connection.begin()

    original_bind = db_main.SessionLocal.kw["bind"]
    db_main.SessionLocal.configure(bind=connection)

    yield TestClient(app)

    db_main.SessionLocal.configure(bind=original_bind)
    transaction.rollback()
    connection.close()


FAKE_CLERK_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-token",
    decoded={
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Member",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "test@example.com",
        }
    },
)


@pytest.fixture(scope="function")
def clerk_client(client) -> Generator:
    from app.main import app
    from app.helpers import authenticated_guard

    app.dependency_overrides[authenticated_guard] = lambda: FAKE_CLERK_CREDENTIALS
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def super_admin_client(clerk_client) -> Generator:
    from app.main import app
    from app.helpers import super_admin_guard

    def override_super_admin_guard():
        return HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="fake-token",
        )

    app.dependency_overrides[super_admin_guard] = override_super_admin_guard
    yield clerk_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_client(clerk_client) -> Generator:
    from app.main import app
    from app.helpers import admin_guard

    def override_admin_guard():
        return HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials="fake-token",
        )

    app.dependency_overrides[admin_guard] = override_admin_guard
    yield clerk_client
    app.dependency_overrides.clear()


def pytest_assertrepr_compare(config, op, left, right):
    """Customize assertion comparison output for clearer failure messages."""
    if op == "==":
        return [
            "Assertion failed:",
            f"  Expected: {right!r}",
            f"  Actual:   {left!r}",
        ]
    if op == "!=":
        return [
            "Assertion failed:",
            f"  Expected NOT: {right!r}",
            f"  Actual:        {left!r}",
        ]