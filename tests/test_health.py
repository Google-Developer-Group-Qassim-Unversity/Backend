"""
Basic health check tests to verify testing infrastructure works.
"""

from fastapi.testclient import TestClient


def test_app_loads(client: TestClient):
    """Test that the FastAPI app loads successfully."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_root_redirects_to_docs(client: TestClient):
    """Test that root path redirects to docs."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307  # Temporary redirect
    assert response.headers["location"] == "/docs"


def test_database_connection(engine):
    """Test that database connection works."""
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1