"""
Tests for attendance endpoints.

NOTE: This module patches get_attendable_logs because the production code
hardcodes action IDs [76, 77, 78, 79, 87, 89] instead of making them configurable.
This monkey-patching is a temporary workaround - the production code should be
refactored to use configurable action IDs (e.g., from config or database).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.DB.schema import MembersLogs, Members, MembersGender, Logs, Submissions
from app.DB import logs as log_queries
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden, assert_not_found, assert_bad_request

JWT_SECRET = "test_jwt_secret_for_testing_only"

_original_get_attendable_logs = log_queries.get_attendable_logs


def _patched_get_attendable_logs(session, event_id):
    stmt = select(Logs).where(Logs.event_id == event_id, Logs.action_id == 2)
    result = session.scalar(stmt)
    if result:
        return result
    stmt = select(Logs).where(Logs.event_id == event_id)
    return session.scalar(stmt)


@pytest.fixture(scope="module", autouse=True)
def patch_get_attendable_logs():
    log_queries.get_attendable_logs = _patched_get_attendable_logs
    yield
    log_queries.get_attendable_logs = _original_get_attendable_logs


def make_attendance_token(event_id: int, secret: str = JWT_SECRET, expires_in_hours: int = 1) -> str:
    payload = {"eventId": event_id, "exp": datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)}
    return jwt.encode(payload, secret, algorithm="HS256")


def create_attendance_ready_event(client: TestClient, form_type: str = "none", **event_overrides) -> int:
    payload = make_create_event_payload(form_type=form_type)
    if event_overrides:
        payload["event"].update(event_overrides)
    response = client.post("/events", json=payload)
    assert_2xx(response)
    event_id = response.json()["id"]
    status_response = client.put(f"/events/{event_id}/status", json={"status": "open"})
    assert_2xx(status_response)
    return event_id


def create_ongoing_event(client: TestClient, form_type: str = "none", days: int = 3) -> int:
    """Create an event that spans from yesterday to days from now, ensuring it includes today."""
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=days)
    return create_attendance_ready_event(
        client, form_type=form_type, start_datetime=start.isoformat(), end_datetime=end.isoformat()
    )


def create_submission(
    db_session: Session, form_id: int, member_id: int, is_accepted: int = 0, submission_type: str = "registration"
):
    submission = Submissions(
        form_id=form_id, member_id=member_id, is_accepted=is_accepted, is_invited=0, submission_type=submission_type
    )
    db_session.add(submission)
    db_session.commit()
    db_session.refresh(submission)
    return submission


def get_form_id(client: TestClient, event_id: int) -> int:
    form_response = client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    return form_response.json()["id"]


def create_test_member(db_session: Session) -> Members:
    member = Members(
        name="Test Member",
        email="test@example.com",
        phone_number="0501234567",
        uni_id="123456789",
        gender=MembersGender.MALE,
        uni_level=4,
        uni_college="Engineering",
    )
    db_session.add(member)
    db_session.commit()
    db_session.refresh(member)
    return member


def get_member_log_id(db_session: Session, event_id: int) -> int | None:
    logs = db_session.query(Logs).filter(Logs.event_id == event_id).all()
    for log in logs:
        if log.action_id == 2:
            return log.id
    return logs[0].id if logs else None


# === get_event_attendance tests ===


def test_attendance_count_public(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.get(f"/attendance/{event_id}?type=count")
    assert_2xx(response)
    data = response.json()
    assert "attendance_count" in data
    assert data["attendance_count"] == 0
    assert data["attendance"] is None


def test_attendance_detailed_forbidden_for_member(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    from app.main import app
    from app.helpers import optional_clerk_guard
    from tests.conftest import FAKE_CLERK_CREDENTIALS

    app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_CLERK_CREDENTIALS
    response = admin_client.get(f"/attendance/{event_id}?type=detailed")
    assert_forbidden(response)


def test_attendance_me_authenticated(admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client)
    create_test_member(db_session)
    response = admin_client.get(f"/attendance/{event_id}?type=me")
    assert_2xx(response)


def test_attendance_me_returns_own_attendance(admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client)
    member = create_test_member(db_session)
    log_id = get_member_log_id(db_session, event_id)
    member_log = MembersLogs(member_id=member.id, log_id=log_id, date=datetime.now())
    db_session.add(member_log)
    db_session.commit()
    response = admin_client.get(f"/attendance/{event_id}?type=me")
    assert_2xx(response)
    data = response.json()
    assert data["attendance_count"] == 1
    assert len(data["attendance"]) == 1


def test_attendance_count_with_day_param(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.get(f"/attendance/{event_id}?type=count&day=1")
    assert_2xx(response)
    assert response.json()["attendance_count"] == 0


def test_attendance_day_out_of_range(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.get(f"/attendance/{event_id}?type=count&day=999")
    assert_not_found(response)


# === remove_attendance_manual tests ===


def test_remove_attendance_success(admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client)
    log_id = get_member_log_id(db_session, event_id)
    member_log = MembersLogs(member_id=1, log_id=log_id, date=datetime.now())
    db_session.add(member_log)
    db_session.commit()
    response = admin_client.request(
        "DELETE", f"/attendance/{event_id}/manual", content=json.dumps({"member_ids": [1], "day": None})
    )
    assert_2xx(response)
    data = response.json()
    assert data["success"] == 1
    assert data["failed"] == 0


def test_remove_attendance_member_not_found(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.request(
        "DELETE", f"/attendance/{event_id}/manual", content=json.dumps({"member_ids": [9999], "day": None})
    )
    assert_2xx(response)
    data = response.json()
    assert data["success"] == 0
    assert data["failed"] == 1


def test_remove_attendance_event_not_found(admin_client: TestClient):
    response = admin_client.request(
        "DELETE", "/attendance/9999/manual", content=json.dumps({"member_ids": [1], "day": None})
    )
    assert_not_found(response)


# === mark_attendance_manual tests ===


def test_manual_mark_success(admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client)
    log_id = get_member_log_id(db_session, event_id)
    assert log_id is not None, "Event should have a member log"
    response = admin_client.post(f"/attendance/{event_id}/manual", json={"member_ids": [1, 2], "day": 1})
    assert_2xx(response)
    data = response.json()
    assert data["success"] == 2
    assert data["failed"] == 0


def test_manual_mark_event_closed(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    close_response = admin_client.put(f"/events/{event_id}/status", json={"status": "closed"})
    assert_2xx(close_response)
    response = admin_client.post(f"/attendance/{event_id}/manual", json={"member_ids": [1]})
    assert_bad_request(response)


def test_manual_mark_member_not_found(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.post(f"/attendance/{event_id}/manual", json={"member_ids": [9999]})
    assert_2xx(response)
    data = response.json()
    assert data["success"] == 0
    assert data["failed"] == 1


def test_manual_mark_day_out_of_range(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.post(f"/attendance/{event_id}/manual", json={"member_ids": [1], "day": 999})
    assert_2xx(response)
    data = response.json()
    assert data["success"] == 0
    assert data["failed"] == 1


# === backfill_attendance tests ===


def test_backfill_existing_members(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    members_payload = [
        {
            "name": "Test",
            "email": "test@test.com",
            "phone_number": "0501111111",
            "uni_id": "111111111",
            "gender": "Male",
            "uni_level": 3,
            "uni_college": "Engineering",
        },
        {
            "name": "Test2",
            "email": "test2@test.com",
            "phone_number": "0502222222",
            "uni_id": "222222222",
            "gender": "Female",
            "uni_level": 4,
            "uni_college": "Science",
        },
    ]
    response = admin_client.post(f"/attendance/{event_id}/backfill", json={"members": members_payload, "day": 1})
    assert_2xx(response)
    data = response.json()
    assert data["existing_count"] == 2
    assert data["created_count"] == 0
    assert data["marked_count"] == 2


def test_backfill_creates_new_members(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    members_payload = [
        {
            "name": "New Member",
            "email": "new@test.com",
            "phone_number": "0503333333",
            "uni_id": "333333333",
            "gender": "Male",
            "uni_level": 2,
            "uni_college": "Business",
        }
    ]
    response = admin_client.post(f"/attendance/{event_id}/backfill", json={"members": members_payload, "day": 1})
    assert_2xx(response)
    data = response.json()
    assert data["existing_count"] == 0
    assert data["created_count"] == 1
    assert data["marked_count"] == 1


def test_backfill_day_out_of_range(admin_client: TestClient):
    event_id = create_attendance_ready_event(admin_client)
    response = admin_client.post(f"/attendance/{event_id}/backfill", json={"members": [], "day": 999})
    assert_bad_request(response)


def test_backfill_event_not_found(admin_client: TestClient):
    response = admin_client.post("/attendance/9999/backfill", json={"members": [], "day": 1})
    assert_not_found(response)


# === mark_attendance tests ===


def test_mark_attendance_success(admin_client: TestClient, clerk_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="none")
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_2xx(response)
    attendance_response = clerk_client.get(f"/attendance/{event_id}?type=me")
    assert_2xx(attendance_response)
    assert attendance_response.json()["attendance_count"] == 1


def test_mark_attendance_no_token(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client, form_type="none")
    create_test_member(db_session)
    response = clerk_client.post(f"/attendance/{event_id}")
    assert_bad_request(response)


def test_mark_attendance_invalid_token(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client, form_type="none")
    create_test_member(db_session)
    response = clerk_client.post(f"/attendance/{event_id}?token=invalid_token")
    assert response.status_code == 400


def test_mark_attendance_wrong_event_in_token(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client, form_type="none")
    create_test_member(db_session)
    token = make_attendance_token(9999)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)


def test_mark_attendance_expired_token(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_attendance_ready_event(admin_client, form_type="none")
    create_test_member(db_session)
    token = make_attendance_token(event_id, expires_in_hours=-1)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert response.status_code == 401


def test_mark_attendance_already_marked_today(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="none")
    member = create_test_member(db_session)
    log_id = get_member_log_id(db_session, event_id)
    existing_log = MembersLogs(member_id=member.id, log_id=log_id, date=datetime.now())
    db_session.add(existing_log)
    db_session.commit()
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)


def test_mark_attendance_event_not_found(clerk_client: TestClient):
    token = make_attendance_token(9999)
    response = clerk_client.post(f"/attendance/9999?token={token}")
    assert_not_found(response)


# === mark_attendance form type tests ===


def test_mark_form_registration_not_submitted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="registration")
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "ما عبيت فورم الحدث" in response.json()["detail"]


def test_mark_form_registration_not_accepted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="registration")
    member = create_test_member(db_session)
    form_id = get_form_id(admin_client, event_id)
    create_submission(db_session, form_id=form_id, member_id=member.id, is_accepted=0)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "ما انقبلت في الحدث" in response.json()["detail"]


def test_mark_form_registration_accepted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="registration")
    member = create_test_member(db_session)
    form_id = get_form_id(admin_client, event_id)
    create_submission(db_session, form_id=form_id, member_id=member.id, is_accepted=1)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_2xx(response)
    attendance_response = clerk_client.get(f"/attendance/{event_id}?type=me")
    assert_2xx(attendance_response)
    assert attendance_response.json()["attendance_count"] == 1


def test_mark_form_google_not_submitted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="google")
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "ما عبيت فورم الحدث" in response.json()["detail"]


def test_mark_form_google_not_accepted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="google")
    member = create_test_member(db_session)
    form_id = get_form_id(admin_client, event_id)
    create_submission(db_session, form_id=form_id, member_id=member.id, is_accepted=0, submission_type="google")
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "ما انقبلت في الحدث" in response.json()["detail"]


def test_mark_form_google_accepted(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    event_id = create_ongoing_event(admin_client, form_type="google")
    member = create_test_member(db_session)
    form_id = get_form_id(admin_client, event_id)
    create_submission(db_session, form_id=form_id, member_id=member.id, is_accepted=1, submission_type="google")
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_2xx(response)
    attendance_response = clerk_client.get(f"/attendance/{event_id}?type=me")
    assert_2xx(attendance_response)
    assert attendance_response.json()["attendance_count"] == 1


# === mark_attendance event date validation tests ===


def test_mark_attendance_event_in_progress(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=1)
    event_id = create_attendance_ready_event(
        admin_client, form_type="none", start_datetime=start.isoformat(), end_datetime=end.isoformat()
    )
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_2xx(response)


def test_mark_attendance_event_ended_yesterday(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    start = "2025-01-01T00:00:00"
    end = "2025-01-02T23:59:59"
    event_id = create_attendance_ready_event(admin_client, form_type="none", start_datetime=start, end_datetime=end)
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "خارج فترة الحدث" in response.json()["detail"]


def test_mark_attendance_event_starts_tomorrow(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    now = datetime.now()
    start = (now + timedelta(days=2)).isoformat()
    end = (now + timedelta(days=3)).isoformat()
    event_id = create_attendance_ready_event(admin_client, form_type="none", start_datetime=start, end_datetime=end)
    create_test_member(db_session)
    token = make_attendance_token(event_id)
    response = clerk_client.post(f"/attendance/{event_id}?token={token}")
    assert_bad_request(response)
    assert "خارج فترة الحدث" in response.json()["detail"]


def test_mark_attendance_early_hours_last_day(clerk_client: TestClient, admin_client: TestClient, db_session: Session):
    now = datetime.now()
    event_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    event_end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    event_id = create_attendance_ready_event(
        admin_client, form_type="none", start_datetime=event_start.isoformat(), end_datetime=event_end.isoformat()
    )
    create_test_member(db_session)
    # Simulate marking at 2 AM - the effective date should still be yesterday (last day of event)
    early_am = datetime(now.year, now.month, now.day, 2, 0, 0)
    with patch("app.routers.attendance.datetime") as mock_dt:
        mock_dt.now.return_value = early_am
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        token = make_attendance_token(event_id)
        response = clerk_client.post(f"/attendance/{event_id}?token={token}")
        assert_2xx(response)


def test_mark_attendance_early_hours_past_event(
    clerk_client: TestClient, admin_client: TestClient, db_session: Session
):
    now = datetime.now()
    event_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=3)
    event_end = now.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
    event_id = create_attendance_ready_event(
        admin_client, form_type="none", start_datetime=event_start.isoformat(), end_datetime=event_end.isoformat()
    )
    create_test_member(db_session)
    # Simulate marking at 2 AM today when event ended yesterday
    # Effective date via threshold (2 AM → yesterday) = last day of event → should succeed
    early_am_today = datetime(now.year, now.month, now.day, 2, 0, 0)
    with patch("app.routers.attendance.datetime") as mock_dt:
        mock_dt.now.return_value = early_am_today
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        token = make_attendance_token(event_id)
        response = clerk_client.post(f"/attendance/{event_id}?token={token}")
        assert_2xx(response)
