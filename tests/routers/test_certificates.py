from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.DB.schema import Logs, MembersLogs
from app.exceptions import GatewayTimeout, BadGateway, ServiceUnavailable
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden, assert_not_found


def helper_create_event(admin_client: TestClient) -> dict:
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    return event


def helper_seed_attendance(db_session, event_id: int, member_id: int, seed_refs) -> None:
    member_log = db_session.scalar(
        select(Logs).where(Logs.event_id == event_id, Logs.action_id == seed_refs.member_action.id)
    )
    member_log_entry = MembersLogs(member_id=member_id, log_id=member_log.id)
    db_session.add(member_log_entry)
    db_session.flush()


def test_send_certificates_success(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)
    helper_seed_attendance(db_session, event_id=event["id"], member_id=seed_refs.ahmed.id, seed_refs=seed_refs)

    mock_response = {
        "job_id": "abc-123",
        "event_name": event["name"],
        "folder_name": "certs",
        "status": "pending",
        "message": "queued",
    }
    with patch("app.routers.certificates.call_certificate_api", new_callable=AsyncMock, return_value=mock_response):
        response = admin_client.post(f"/certificates/{event['id']}")

    assert_2xx(response)
    body = response.json()
    assert body["job_id"] == "abc-123"
    assert body["status"] == "pending"


def test_send_certificates_event_not_found(admin_client: TestClient):
    response = admin_client.post("/certificates/9999")
    assert_not_found(response)


def test_send_certificates_empty_attendance(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)

    mock_response = {
        "job_id": "empty-1",
        "event_name": event["name"],
        "folder_name": "certs",
        "status": "pending",
        "message": "queued",
    }
    with patch(
        "app.routers.certificates.call_certificate_api", new_callable=AsyncMock, return_value=mock_response
    ) as mock_api:
        response = admin_client.post(f"/certificates/{event['id']}")

    assert_2xx(response)
    call_args = mock_api.call_args[0][0]
    assert len(call_args.members) == 0


def test_send_manual_certificates_success(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)

    members_payload = [{"name": "Ahmed Ali", "email": "ahmed@example.com", "gender": "Male"}]
    mock_response = {
        "job_id": "manual-1",
        "event_name": event["name"],
        "folder_name": "certs",
        "status": "pending",
        "message": "queued",
    }
    with patch("app.routers.certificates.call_certificate_api", new_callable=AsyncMock, return_value=mock_response):
        response = admin_client.post(f"/certificates/manual/{event['id']}", json={"members": members_payload})

    assert_2xx(response)
    body = response.json()
    assert body["job_id"] == "manual-1"


def test_send_manual_certificates_event_not_found(admin_client: TestClient):
    members_payload = [{"name": "Ahmed Ali", "email": "ahmed@example.com", "gender": "Male"}]
    response = admin_client.post("/certificates/manual/9999", json={"members": members_payload})
    assert_not_found(response)


def test_send_certificates_unauthorized(client: TestClient):
    response = client.post("/certificates/1")
    assert_forbidden(response)


def test_send_manual_certificates_unauthorized(client: TestClient):
    response = client.post(
        "/certificates/manual/1", json={"members": [{"name": "A", "email": "a@b.com", "gender": "Male"}]}
    )
    assert_forbidden(response)


def test_send_certificates_gateway_timeout(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)
    helper_seed_attendance(db_session, event_id=event["id"], member_id=seed_refs.ahmed.id, seed_refs=seed_refs)

    with patch(
        "app.routers.certificates.call_certificate_api",
        new_callable=AsyncMock,
        side_effect=GatewayTimeout(detail="Certificate API request timed out"),
    ):
        response = admin_client.post(f"/certificates/{event['id']}")

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()


def test_send_certificates_bad_gateway(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)
    helper_seed_attendance(db_session, event_id=event["id"], member_id=seed_refs.ahmed.id, seed_refs=seed_refs)

    with patch(
        "app.routers.certificates.call_certificate_api",
        new_callable=AsyncMock,
        side_effect=BadGateway(detail="Certificate API returned error: 500"),
    ):
        response = admin_client.post(f"/certificates/{event['id']}")

    assert response.status_code == 502
    assert "500" in response.json()["detail"]


def test_send_certificates_service_unavailable(admin_client: TestClient, db_session, seed_refs):
    event = helper_create_event(admin_client)
    helper_seed_attendance(db_session, event_id=event["id"], member_id=seed_refs.ahmed.id, seed_refs=seed_refs)

    with patch(
        "app.routers.certificates.call_certificate_api",
        new_callable=AsyncMock,
        side_effect=ServiceUnavailable(detail="Failed to connect to certificate API"),
    ):
        response = admin_client.post(f"/certificates/{event['id']}")

    assert response.status_code == 503
    assert "connect" in response.json()["detail"].lower()
