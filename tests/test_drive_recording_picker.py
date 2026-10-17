"""Unit and integration tests for Meet recording candidate discovery and publishing."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.clients import get_gdrive_client
from app.DB.schema import EventContentLinks, EventsContentLinkKind
from app.main import app
from app.services.gdrive_client import GDriveClient, extract_meet_code
from tests.routers.test_events import assert_2xx, make_create_event_payload


def _make_http_error(status: int, reason: str = "") -> HttpError:
    return HttpError(Response({"status": str(status), "reason": reason}), b"{}")


# ==========================================
# 1. Meeting Code Parsing Unit Tests
# ==========================================


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("https://meet.google.com/abc-defg-hij", "abc-defg-hij"),
        ("http://meet.google.com/abc-defg-hij", "abc-defg-hij"),
        ("meet.google.com/abc-defg-hij", "abc-defg-hij"),
        ("https://meet.google.com/abc-defg-hij?authuser=0&hs=179", "abc-defg-hij"),
        ("https://meet.google.com/abc-defg-hij#some-hash", "abc-defg-hij"),
        ("abc-defg-hij", "abc-defg-hij"),
        ("ABC-DEFG-HIJ", "abc-defg-hij"),
        ("  abc-defg-hij  ", "abc-defg-hij"),
        ("https://zoom.us/j/123456789", None),
        ("https://teams.microsoft.com/l/meetup-join/123", None),
        ("not-a-code", None),
        ("invalid", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_meet_code(input_val, expected):
    assert extract_meet_code(input_val) == expected


# ==========================================
# 2. GDriveClient 3-State & Fallback Unit Tests
# ==========================================


def test_find_recording_candidates_processing():
    """When a recording is currently STARTED, state is 'processing'."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    # spaces.get -> space name
    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    # conferenceRecords.list -> 1 record
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {
        "conferenceRecords": [{"name": "conferenceRecords/conf1"}]
    }
    # recordings.list -> recording state STARTED
    meet_mock.conferenceRecords.return_value.recordings.return_value.list.return_value.execute.return_value = {
        "recordings": [{"state": "STARTED", "name": "rec1"}]
    }

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates("abc-defg-hij", start_dt, end_dt)
    assert state == "processing"
    assert candidates == []


def test_find_recording_candidates_ready():
    """When a recording is FILE_GENERATED, state is 'ready' with candidates."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {
        "conferenceRecords": [{"name": "conferenceRecords/conf1"}]
    }
    meet_mock.conferenceRecords.return_value.recordings.return_value.list.return_value.execute.return_value = {
        "recordings": [
            {
                "state": "FILE_GENERATED",
                "driveDestination": {"file": "drive_file_id_rec1"},
                "startTime": "2026-10-15T10:05:00Z",
            }
        ]
    }

    # Drive file metadata
    drive_mock.files.return_value.get.return_value.execute.return_value = {
        "id": "drive_file_id_rec1",
        "name": "abc-defg-hij (2026-10-15 at 10:05).mp4",
        "webViewLink": "https://drive.google.com/file/d/drive_file_id_rec1/view",
        "createdTime": "2026-10-15T10:05:00Z",
    }
    # File is not public yet
    drive_mock.permissions.return_value.list.return_value.execute.return_value = {"permissions": []}

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates("abc-defg-hij", start_dt, end_dt)
    assert state == "ready"
    assert len(candidates) == 1
    assert candidates[0]["file_id"] == "drive_file_id_rec1"
    assert candidates[0]["is_public"] is False
    assert candidates[0]["web_view_link"] == "https://drive.google.com/file/d/drive_file_id_rec1/view"


def test_find_recording_candidates_excludes_already_attached():
    """File IDs in exclude_file_ids must not appear in the candidate list."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {
        "conferenceRecords": [{"name": "conferenceRecords/conf1"}]
    }
    meet_mock.conferenceRecords.return_value.recordings.return_value.list.return_value.execute.return_value = {
        "recordings": [{"state": "FILE_GENERATED", "driveDestination": {"file": "already_attached_file_id"}}]
    }

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates(
        "abc-defg-hij", start_dt, end_dt, exclude_file_ids={"already_attached_file_id"}
    )
    # Excluded, none remaining, no recording was started -> state is absent
    assert state == "absent"
    assert candidates == []


def test_find_recording_candidates_absent_when_zero_recordings():
    """When conference records exist but 0 recordings were made, state is 'absent'."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {
        "conferenceRecords": [{"name": "conferenceRecords/conf1"}]
    }
    meet_mock.conferenceRecords.return_value.recordings.return_value.list.return_value.execute.return_value = {
        "recordings": []
    }

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates("abc-defg-hij", start_dt, end_dt)
    assert state == "absent"
    assert candidates == []


def test_find_recording_candidates_absent_on_zero_conference_records():
    """When Meet API returns 0 conference records, state is absent."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {"conferenceRecords": []}

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates("abc-defg-hij", start_dt, end_dt)
    assert state == "absent"
    assert candidates == []


def test_find_recording_candidates_absent_on_missing_url():
    """When meeting_url is None, returns absent without searching Drive."""
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    start_dt = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    state, candidates = client.find_recording_candidates(None, start_dt, end_dt)
    assert state == "absent"
    assert candidates == []


# ==========================================
# 3. HTTP Endpoint Integration Tests
# ==========================================


@pytest.fixture
def recording_test_event_id(admin_client: TestClient) -> int:
    payload = make_create_event_payload(name="Recording Picker Test Event")
    res = admin_client.post("/events", json=payload)
    assert_2xx(res)
    event_id = res.json()["id"]

    # Set meeting url
    res = admin_client.put(
        f"/events/{event_id}/meeting-url", json={"meeting_url": "https://meet.google.com/abc-defg-hij"}
    )
    assert_2xx(res)
    return event_id


def test_get_recording_candidates_endpoint_success(admin_client: TestClient, recording_test_event_id: int):
    drive_mock = MagicMock()
    meet_mock = MagicMock()

    meet_mock.spaces.return_value.get.return_value.execute.return_value = {"name": "spaces/xyz"}
    meet_mock.conferenceRecords.return_value.list.return_value.execute.return_value = {
        "conferenceRecords": [{"name": "conferenceRecords/conf1"}]
    }
    meet_mock.conferenceRecords.return_value.recordings.return_value.list.return_value.execute.return_value = {
        "recordings": [
            {
                "state": "FILE_GENERATED",
                "driveDestination": {"file": "file_rec_endpoint_1"},
                "startTime": "2026-10-15T10:00:00Z",
            }
        ]
    }

    drive_mock.files.return_value.get.return_value.execute.return_value = {
        "id": "file_rec_endpoint_1",
        "name": "Meeting 1.mp4",
        "webViewLink": "https://drive.google.com/file/d/file_rec_endpoint_1/view",
        "createdTime": "2026-10-15T10:00:00Z",
    }
    drive_mock.permissions.return_value.list.return_value.execute.return_value = {"permissions": []}

    client = GDriveClient(service=drive_mock, meet_service=meet_mock)
    app.dependency_overrides[get_gdrive_client] = lambda: client

    try:
        res = admin_client.get(f"/events/{recording_test_event_id}/drive-recording-candidates")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["state"] == "ready"
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["file_id"] == "file_rec_endpoint_1"
        assert data["candidates"][0]["is_public"] is False
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)


def test_get_recording_candidates_endpoint_unconfigured(admin_client: TestClient, recording_test_event_id: int):
    client = GDriveClient(service=None)
    # Force is_configured -> False
    client.is_configured = lambda: False
    app.dependency_overrides[get_gdrive_client] = lambda: client

    try:
        res = admin_client.get(f"/events/{recording_test_event_id}/drive-recording-candidates")
        assert res.status_code == 503
        assert "not configured" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)


def test_make_recording_candidate_public_success(admin_client: TestClient, recording_test_event_id: int):
    drive_mock = MagicMock()
    drive_mock.permissions.return_value.create.return_value.execute.return_value = {"id": "new_perm_id"}

    client = GDriveClient(service=drive_mock)
    app.dependency_overrides[get_gdrive_client] = lambda: client

    try:
        res = admin_client.post(
            f"/events/{recording_test_event_id}/drive-recording-candidates/file_candidate_123/make-public"
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["file_id"] == "file_candidate_123"
        assert data["is_public"] is True
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)


def test_make_recording_candidate_public_403_policy_blocked(admin_client: TestClient, recording_test_event_id: int):
    drive_mock = MagicMock()
    drive_mock.permissions.return_value.create.return_value.execute.side_effect = _make_http_error(
        403, "Workspace policy forbids external sharing"
    )

    client = GDriveClient(service=drive_mock)
    app.dependency_overrides[get_gdrive_client] = lambda: client

    try:
        res = admin_client.post(
            f"/events/{recording_test_event_id}/drive-recording-candidates/file_candidate_123/make-public"
        )
        assert res.status_code == 403, res.text
        assert "workspace policy blocked public sharing" in res.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)


def test_approve_candidate_creates_content_link_with_drive_file_id(
    admin_client: TestClient, recording_test_event_id: int, db_session
):
    """When a candidate is approved via POST /content-links, drive_file_id is stored in the DB row."""
    payload = {
        "label": "Session Recording",
        "url": "https://drive.google.com/file/d/approved_drive_file_999/view",
        "kind": "recording",
        "published": True,
        "drive_file_id": "approved_drive_file_999",
    }
    res = admin_client.post(f"/events/{recording_test_event_id}/content-links", json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["label"] == "Session Recording"
    assert created["drive_file_id"] == "approved_drive_file_999"

    # Verify DB row
    row = db_session.query(EventContentLinks).filter_by(id=created["id"]).first()
    assert row is not None
    assert row.drive_file_id == "approved_drive_file_999"
    assert row.kind == EventsContentLinkKind.RECORDING
