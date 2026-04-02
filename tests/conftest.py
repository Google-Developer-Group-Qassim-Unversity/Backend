"""
Pytest configuration and fixtures for testing.

This module sets up:
- Environment variables for testing
- Testcontainers MySQL (if DATABASE_URL not provided)
- Database session fixtures with transaction rollback
- FastAPI test client
"""

import os
import pytest
from typing import Generator

# Set environment variables BEFORE importing app
# These must be set before any app modules are imported
_required_env_vars = {
    "ENV": "testing",
    "CLERK_JWKS_URL": "https://test.clerk.dev/.well-known/jwks.json",
    "GOOGLE_CLIENT_ID": "test_client_id",
    "GOOGLE_CLIENT_SECRET": "test_client_secret",
    "JWT_SECRET": "test_jwt_secret_for_testing_only",
    "CERTIFICATE_API_URL": "http://localhost:8000",
}

for key, value in _required_env_vars.items():
    if not os.getenv(key):
        os.environ[key] = value

# Container reference - kept at module level to prevent garbage collection
_test_container = None


@pytest.fixture(scope="session")
def database_url():
    """
    Provide the test database URL.
    
    - If DATABASE_URL env var is set (CI mode), use it
    - Otherwise, start a MySQL testcontainer
    
    Yields the database URL string.
    """
    global _test_container
    
    existing_url = os.getenv("DATABASE_URL")
    
    if existing_url:
        print(f"[conftest] Using provided DATABASE_URL: {existing_url}")
        yield existing_url
        return
    
    # Start testcontainers
    print("[conftest] Starting MySQL testcontainer...")
    from testcontainers.mysql import MySqlContainer
    import time
    
    _test_container = MySqlContainer("mysql:8.0", dbname="test")
    _test_container.start()
    
    # Wait for MySQL to be ready
    time.sleep(3)
    
    # Get connection URL and convert to pymysql driver
    url = _test_container.get_connection_url()
    url = url.replace("mysql://", "mysql+pymysql://")
    print(f"[conftest] MySQL testcontainer started: {url}")
    
    # Set env var for app to use
    os.environ["DATABASE_URL"] = url
    
    yield url
    
    # Cleanup
    print("[conftest] Stopping MySQL testcontainer...")
    _test_container.stop()


@pytest.fixture(scope="session")
def engine(database_url):
    """
    Create SQLAlchemy engine and database tables.
    
    Runs once per test session.
    Yields the engine instance.
    """
    # Import here after DATABASE_URL is set
    from sqlalchemy import create_engine
    from app.DB.schema import Base
    
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    
    # Create tables (excluding views which are marked with info={"is_view": True})
    print("[conftest] Creating database tables...")
    tables_to_create = [
        table for name, table in Base.metadata.tables.items()
        if not table.info.get("is_view")
    ]
    
    # Create tables in dependency order
    for table in Base.metadata.sorted_tables:
        if not table.info.get("is_view"):
            table.create(engine, checkfirst=True)
    
    yield engine
    
    # Cleanup: drop all tables
    print("[conftest] Dropping database tables...")
    for table in reversed(Base.metadata.sorted_tables):
        if not table.info.get("is_view"):
            table.drop(engine, checkfirst=True)


@pytest.fixture(scope="function")
def db_session(engine) -> Generator:
    """
    Provide a database session with transaction rollback.
    
    Each test gets a fresh transaction that is rolled back after the test.
    This ensures tests are isolated from each other.
    """
    from sqlalchemy.orm import Session
    
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(database_url) -> Generator:
    """
    Provide a FastAPI test client.
    
    Note: This fixture requires database_url to ensure the database
    is set up before importing the app.
    """
    # Import app here after DATABASE_URL is set
    from fastapi.testclient import TestClient
    from app.main import app
    
    yield TestClient(app)