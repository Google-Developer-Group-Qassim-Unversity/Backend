"""
Pytest configuration and fixtures for testing.
Fixture chain (scope):

    database_url (session)
        |
    engine (session) ─── runs Alembic migrations
        |
    seed_core_data (session) ─── seeds the DB
        |
    db_bind (function) ─── one connection + outer transaction, rolled back after each test
        |
        ├── db_session (function) ─── session for direct DB access in tests
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
from fastapi_clerk_auth import HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials

# Clerk still builds its JWKS client at import time (see Phase 3 of the refactor plan),
# so these must be set before any app module is imported.
required_env_vars = {
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
    "MEMBER_APP_URL": "http://localhost:3000",
    "MEMBER_APP_REVALIDATE_SECRET": "test_revalidate_secret",
}

for key, value in required_env_vars.items():
    os.environ[key] = value  # Always set, override any existing

# Safe to import at module level now: the engine is built lazily on first use,
# so importing the app no longer needs DATABASE_URL.
import app.DB.main as db_main  # noqa: E402
from app.config import reload_settings  # noqa: E402
from app.dependencies import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.DB.schema import (  # noqa: E402
    Actions,
    ActionsActionType,
    Departments,
    DepartmentsType,
    Members,
    MembersGender,
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Rebuild Settings for each test.

    get_settings() is lru_cached, so a test that monkeypatches an environment
    variable would otherwise read whatever the first test cached.
    """
    reload_settings()
    yield
    reload_settings()


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
        print("[conftest] Using provided DATABASE_URL")
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

    engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)

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
    # CAUTION: Don't update default unless you know what you're doing
    # a lot of tests assume these default values and changing them might break the tests
    with Session(engine) as session:
        session.add_all(
            [
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
                Departments(name="Business", type=DepartmentsType.PRACTICAL, ar_name="ريادة الأعمال"),
                Departments(name="Design", type=DepartmentsType.ADMINISTRATIVE, ar_name="التصميم"),
                Members(
                    name="Ahmed Ali",
                    email="ahmed@example.com",
                    phone_number="0501234567",
                    uni_id="111111111",
                    gender=MembersGender.MALE,
                    uni_level=4,
                    uni_college="Engineering",
                ),
                Members(
                    name="Sara Khalid",
                    email="sara@example.com",
                    phone_number="0509876543",
                    uni_id="222222222",
                    gender=MembersGender.FEMALE,
                    uni_level=3,
                    uni_college="Science",
                ),
            ]
        )
        session.commit()
        print("[conftest] ✓ Core data seeded")


@pytest.fixture(scope="function")
def db_bind(engine, seed_core_data) -> Generator:
    """Own the per-test connection and outer transaction.

    Sessions built from the yielded factory join that transaction using
    savepoints, so a route calling ``session.commit()`` behaves normally while
    every write is still undone by the rollback at the end of the test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

    # Background tasks and scripts call ``db_session()``, which builds its own
    # session from the real engine. Point it at the test connection too.
    original_sessionmaker = db_main.get_sessionmaker
    db_main.get_sessionmaker = lambda: factory

    yield factory

    db_main.get_sessionmaker = original_sessionmaker
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_bind) -> Generator:
    """FastAPI test client whose routes use the test-scoped session."""

    def override_get_db() -> Generator:
        session = db_bind()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.pop(get_db, None)


FAKE_CLERK_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-token",
    decoded={
        "sub": "clerk_test_member_sub",
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Member",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "test@example.com",
        },
    },
)

FAKE_ADMIN_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-admin-token",
    decoded={
        "sub": "clerk_test_admin_sub",
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Admin",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "admin@example.com",
            "is_admin": True,
        },
    },
)


FAKE_SUPER_ADMIN_CREDENTIALS = ClerkHTTPAuthorizationCredentials(
    scheme="Bearer",
    credentials="fake-super-admin-token",
    decoded={
        "sub": "clerk_test_super_admin_sub",
        "metadata": {
            "uni_id": "123456789",
            "fullArabicName": "Test Super Admin",
            "saudiPhone": "0501234567",
            "gender": "Male",
            "uniLevel": 4,
            "uniCollege": "Engineering",
            "personalEmail": "superadmin@example.com",
            "is_super_admin": True,
        },
    },
)


@pytest.fixture(scope="function")
def clerk_client(client) -> Generator:
    from app.helpers import authenticated_guard, optional_clerk_guard

    app.dependency_overrides[authenticated_guard] = lambda: FAKE_CLERK_CREDENTIALS
    app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_CLERK_CREDENTIALS
    yield client
    app.dependency_overrides.pop(authenticated_guard, None)
    app.dependency_overrides.pop(optional_clerk_guard, None)


@pytest.fixture(scope="function")
def super_admin_client(clerk_client) -> Generator:
    from app.helpers import super_admin_guard

    app.dependency_overrides[super_admin_guard] = lambda: FAKE_SUPER_ADMIN_CREDENTIALS
    yield clerk_client
    app.dependency_overrides.pop(super_admin_guard, None)


@pytest.fixture(scope="function")
def admin_client(clerk_client) -> Generator:
    from app.helpers import admin_guard, optional_clerk_guard

    app.dependency_overrides[admin_guard] = lambda: FAKE_ADMIN_CREDENTIALS
    app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_ADMIN_CREDENTIALS
    yield clerk_client
    app.dependency_overrides.pop(admin_guard, None)
    app.dependency_overrides.pop(optional_clerk_guard, None)


@pytest.fixture(scope="function")
def db_session(db_bind):
    """
    Provide a SQLAlchemy session bound to the test transaction.

    Use this fixture when tests need direct DB access (e.g., inserting test data).
    All changes will be rolled back after the test via db_bind's transaction.
    """
    session = db_bind()
    try:
        yield session
    finally:
        session.close()


class SeedRefs:
    """Dynamic references to seed data IDs.

    Queries the test DB for seeded entities so tests don't hardcode IDs.
    If seed data changes, these update automatically.
    """

    def __init__(self, session):
        from sqlalchemy import select

        self.dept_action = session.scalar(select(Actions).where(Actions.action_type == ActionsActionType.DEPARTMENT))
        self.member_action = session.scalar(select(Actions).where(Actions.action_type == ActionsActionType.MEMBER))
        self.dept_business = session.scalar(select(Departments).where(Departments.name == "Business"))
        self.dept_design = session.scalar(select(Departments).where(Departments.name == "Design"))
        self.ahmed = session.scalar(select(Members).where(Members.uni_id == "111111111"))
        self.sara = session.scalar(select(Members).where(Members.uni_id == "222222222"))


@pytest.fixture(scope="function")
def seed_refs(db_session):
    return SeedRefs(db_session)


def pytest_collection_modifyitems(items):
    """Force test_database_connection to always run first."""

    # Find the test by name, remove it from its current position, and re-insert at index 0
    db_test = next((i for i in items if i.name == "test_database_connection"), None)
    if db_test:
        items.remove(db_test)
        items.insert(0, db_test)


def pytest_assertrepr_compare(config, op, left, right):
    """Customize assertion comparison output for clearer failure messages."""
    if op == "==":
        return ["Assertion failed:", f"  Expected: {right!r}", f"  Actual:   {left!r}"]
    if op == "!=":
        return ["Assertion failed:", f"  Expected NOT: {right!r}", f"  Actual:        {left!r}"]
