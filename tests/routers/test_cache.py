import httpx
import pytest
from fastapi.testclient import TestClient

from tests.utils import assert_2xx, assert_forbidden


@pytest.fixture
def leaderboard(monkeypatch):
    """Stand in for the leaderboard app's /api/revalidate."""
    calls = []

    def fake_reset():
        calls.append(True)
        return {"revalidated": True}

    monkeypatch.setattr("app.routers.cache.reset_leaderboard_cache", fake_reset)
    return calls


def test_reset_calls_the_leaderboard_app(admin_client: TestClient, leaderboard):
    response = admin_client.post("/cache/reset")

    assert_2xx(response)
    body = response.json()
    assert body["success"] is True
    assert body["result"] == {"revalidated": True}
    assert len(leaderboard) == 1


def test_upstream_error_becomes_502(admin_client: TestClient, monkeypatch):
    def boom():
        raise httpx.HTTPStatusError(
            "nope", request=httpx.Request("POST", "http://x"), response=httpx.Response(500, request=httpx.Request("POST", "http://x"))
        )

    monkeypatch.setattr("app.routers.cache.reset_leaderboard_cache", boom)
    response = admin_client.post("/cache/reset")

    assert response.status_code == 502
    assert "500" in response.json()["detail"]


def test_unreachable_leaderboard_becomes_503(admin_client: TestClient, monkeypatch):
    def boom():
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("app.routers.cache.reset_leaderboard_cache", boom)
    response = admin_client.post("/cache/reset")

    assert response.status_code == 503
    assert "Failed to connect" in response.json()["detail"]


def test_reset_requires_admin(clerk_client: TestClient):
    assert_forbidden(clerk_client.post("/cache/reset"))
