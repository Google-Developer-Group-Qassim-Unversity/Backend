"""Exercise the application-wide exception handlers.

These mount a throwaway app rather than a real route, so each handler is tested
in isolation from whatever the routers happen to raise today.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError

from app.error_handlers import register_exception_handlers
from app.exceptions import Conflict, DataIntegrityError, GatewayTimeout, MemberNotFound


def make_client(exc: Exception) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "exc, expected_status, expected_detail",
    [
        (MemberNotFound(42), 404, "Member with id '42' not found or does not exist"),
        (Conflict("Semester", "fall"), 409, "Semester with id 'fall' already exists"),
        (GatewayTimeout(), 504, "Upstream request timed out"),
    ],
)
def test_known_http_exceptions_keep_their_status_and_detail(exc, expected_status, expected_detail):
    response = make_client(exc).get("/boom")
    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_data_integrity_error_is_a_500_with_its_message():
    response = make_client(DataIntegrityError("Event has no attendable logs")).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Event has no attendable logs"}


def test_integrity_error_becomes_409_without_leaking_the_driver_message():
    exc = IntegrityError("INSERT INTO members ...", {}, Exception("Duplicate entry 'a@b.com' for key 'email'"))
    response = make_client(exc).get("/boom")
    assert response.status_code == 409
    assert response.json() == {"detail": "This conflicts with data that already exists."}
    assert "a@b.com" not in response.text


def test_operational_error_becomes_503():
    exc = OperationalError("SELECT 1", {}, Exception("server has gone away"))
    response = make_client(exc).get("/boom")
    assert response.status_code == 503
    assert "Database temporarily unavailable" in response.json()["detail"]


def test_pool_timeout_becomes_503():
    response = make_client(SQLAlchemyTimeoutError("QueuePool limit reached")).get("/boom")
    assert response.status_code == 503
    assert "under heavy load" in response.json()["detail"]


def test_other_database_errors_become_a_generic_500():
    response = make_client(SQLAlchemyError("some mapper problem")).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "A database error occurred."}


def test_unexpected_exceptions_become_a_generic_500():
    response = make_client(ValueError("something nobody anticipated")).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "nobody anticipated" not in response.text
