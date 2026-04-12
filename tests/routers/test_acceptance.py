from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden, assert_bad_request
from app.DB.schema import Submissions, SubmissionsSubmissionType

from app.exceptions import ServiceUnavailable, BadGateway, GatewayTimeout


def helper_create_event_with_form(admin_client: TestClient) -> tuple[dict, int]:
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    form = admin_client.get(f"/events/{event['id']}/form").json()
    return event, form["id"]


def helper_insert_accepted_submission(db_session, form_id: int, member_id: int = 1, is_accepted: bool = True) -> int:
    sub = Submissions(
        form_id=form_id,
        member_id=member_id,
        is_accepted=1 if is_accepted else 0,
        is_invited=0,
        submission_type=SubmissionsSubmissionType.REGISTRATION,
    )
    db_session.add(sub)
    db_session.flush()
    return sub.id


def test_send_acceptance_blasts_success(admin_client: TestClient, db_session):
    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    mock_response = {"message": "sent", "count": 1}
    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value=mock_response):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test+Subject",
            content="<html><body>Hello</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert_2xx(response)
    body = response.json()
    assert body["sent_count"] == 1
    assert "ahmed@example.com" in body["emails"]


def test_send_acceptance_blasts_marks_invited(admin_client: TestClient, db_session):
    event, form_id = helper_create_event_with_form(admin_client)
    sub_id = helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    assert_2xx(response)

    db_session.expire_all()
    sub = db_session.get(Submissions, sub_id)
    assert sub.is_invited == 1, f"Expected is_invited=1 after blast, got {sub.is_invited}"


def test_send_acceptance_blasts_empty_body(admin_client: TestClient, db_session):
    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    assert_bad_request(response)
    assert "HTML content" in response.json()["detail"]


def test_send_acceptance_blasts_no_accepted_submissions(admin_client: TestClient, db_session):
    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1, is_accepted=False)

    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    assert_2xx(response)
    body = response.json()
    assert body["sent_count"] == 0, f"Expected 0 sent_count with no accepted submissions, got {body['sent_count']}"


def test_send_acceptance_test_success(admin_client: TestClient):
    mock_response = {"message": "sent", "count": 2}
    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value=mock_response):
        response = admin_client.post(
            "/acceptance/test?subject=Test+Subject&emails=a%40b.com&emails=c%40d.com",
            content="<html><body>Hello</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert_2xx(response)
    body = response.json()
    assert body["sent_count"] == 2
    assert "a@b.com" in body["emails"]
    assert "c@d.com" in body["emails"]


def test_send_acceptance_test_empty_body(admin_client: TestClient):
    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}):
        response = admin_client.post(
            "/acceptance/test?subject=Test&emails=a%40b.com",
            content="",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    assert_bad_request(response)
    assert "HTML content" in response.json()["detail"]


def test_send_acceptance_test_no_emails(admin_client: TestClient):
    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}):
        response = admin_client.post(
            "/acceptance/test?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    from tests.utils import assert_unprocessable

    assert_unprocessable(response)


def test_send_acceptance_blasts_unauthorized(client: TestClient):
    response = client.post(
        "/acceptance/blasts/1?subject=Test",
        content="<html><body>Hi</body></html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert_forbidden(response)


def test_send_acceptance_test_unauthorized(client: TestClient):
    response = client.post(
        "/acceptance/test?subject=Test&emails=a%40b.com",
        content="<html><body>Hi</body></html>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    assert_forbidden(response)


def test_send_acceptance_blasts_skips_already_invited(admin_client: TestClient, db_session):
    event, form_id = helper_create_event_with_form(admin_client)
    sub1 = Submissions(
        form_id=form_id,
        member_id=1,
        is_accepted=1,
        is_invited=0,
        submission_type=SubmissionsSubmissionType.REGISTRATION,
    )
    sub2 = Submissions(
        form_id=form_id,
        member_id=2,
        is_accepted=1,
        is_invited=1,
        submission_type=SubmissionsSubmissionType.REGISTRATION,
    )
    db_session.add_all([sub1, sub2])
    db_session.flush()

    with patch("app.routers.acceptance.call_acceptance_api", new_callable=AsyncMock, return_value={}) as mock_api:
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert_2xx(response)
    body = response.json()
    assert body["sent_count"] == 1, f"Expected only 1 email (skipping already-invited), got {body['sent_count']}"
    assert "sara@example.com" not in body["emails"]
    mock_api.assert_called_once()


def test_send_acceptance_blasts_gateway_timeout(admin_client: TestClient, db_session):

    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    with patch(
        "app.routers.acceptance.call_acceptance_api",
        new_callable=AsyncMock,
        side_effect=GatewayTimeout(detail="Acceptance API request timed out"),
    ):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()


def test_send_acceptance_blasts_bad_gateway(admin_client: TestClient, db_session):

    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    with patch(
        "app.routers.acceptance.call_acceptance_api",
        new_callable=AsyncMock,
        side_effect=BadGateway(detail="Acceptance API returned error: 500"),
    ):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert response.status_code == 502
    assert "500" in response.json()["detail"]


def test_send_acceptance_blasts_service_unavailable(admin_client: TestClient, db_session):

    event, form_id = helper_create_event_with_form(admin_client)
    helper_insert_accepted_submission(db_session, form_id=form_id, member_id=1)

    with patch(
        "app.routers.acceptance.call_acceptance_api",
        new_callable=AsyncMock,
        side_effect=ServiceUnavailable(detail="Failed to connect to acceptance API"),
    ):
        response = admin_client.post(
            f"/acceptance/blasts/{event['id']}?subject=Test",
            content="<html><body>Hi</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    assert response.status_code == 503
    assert "connect" in response.json()["detail"].lower()
