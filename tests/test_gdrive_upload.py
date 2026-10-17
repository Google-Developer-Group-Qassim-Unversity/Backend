"""Integration tests for the Google Drive slides upload endpoint."""

import io
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.clients import get_gdrive_client
from app.DB.schema import EventContentLinks, EventsContentLinkKind
from app.main import app
from app.services.gdrive_client import GDriveClient
from tests.routers.test_events import assert_2xx, make_create_event_payload


def _make_http_error(status: int, reason: str = "") -> HttpError:
    return HttpError(Response({"status": str(status), "reason": reason}), b"{}")


@pytest.fixture
def mock_drive_service():
    service = MagicMock()
    files = service.files.return_value
    # Default: list returns empty, create returns dummy file
    files.list.return_value.execute.return_value = {"files": []}
    files.create.return_value.execute.return_value = {
        "id": "file_id_mock_123",
        "name": "slides.pdf",
        "webViewLink": "https://drive.google.com/file/d/file_id_mock_123/view",
    }
    perms = service.permissions.return_value
    perms.create.return_value.execute.return_value = {"id": "perm_123"}
    return service


@pytest.fixture
def drive_client_override(mock_drive_service):
    client = GDriveClient(service=mock_drive_service)
    app.dependency_overrides[get_gdrive_client] = lambda: client
    yield client, mock_drive_service
    app.dependency_overrides.pop(get_gdrive_client, None)


@pytest.fixture
def test_event_id(admin_client: TestClient) -> int:
    create_payload = make_create_event_payload(name="Intro to Antigravity")
    res = admin_client.post("/events", json=create_payload)
    assert_2xx(res)
    return res.json()["id"]


def test_upload_slides_success(admin_client: TestClient, test_event_id: int, drive_client_override, db_session):
    client, mock_service = drive_client_override
    files_mock = mock_service.files.return_value
    perms_mock = mock_service.permissions.return_value

    # First list: search for root folder GDG Website -> not found
    # Second list: search for event subfolder -> not found
    # Create calls: 1) create GDG Website, 2) create event folder, 3) create file
    files_mock.list.return_value.execute.side_effect = [
        {"files": []},  # root folder GDG Website
        {"files": []},  # event subfolder
    ]
    files_mock.create.return_value.execute.side_effect = [
        {"id": "root_folder_id_1"},
        {"id": "event_folder_id_1"},
        {
            "id": "uploaded_file_id_1",
            "name": "workshop_presentation.pdf",
            "webViewLink": "https://drive.google.com/file/d/uploaded_file_id_1/view",
        },
    ]

    pdf_content = b"%PDF-1.4 test slide content"
    files = {"file": ("workshop_presentation.pdf", io.BytesIO(pdf_content), "application/pdf")}
    res = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files)
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["label"] == "workshop presentation"
    assert data["kind"] == "slides"
    assert data["published"] is True
    assert data["url"] == "https://drive.google.com/file/d/uploaded_file_id_1/view"
    assert data["download_url"] == "https://drive.usercontent.google.com/download?id=uploaded_file_id_1&export=download"

    # Verify public permission was set
    perms_mock.create.assert_called_once_with(fileId="uploaded_file_id_1", body={"role": "reader", "type": "anyone"})

    # Verify database row
    row = db_session.query(EventContentLinks).filter_by(id=data["id"]).first()
    assert row is not None
    assert row.event_id == test_event_id
    assert row.drive_file_id == "uploaded_file_id_1"
    assert row.kind == EventsContentLinkKind.SLIDES
    assert row.published == 1


def test_upload_slides_reuses_existing_folders(admin_client: TestClient, test_event_id: int, drive_client_override):
    _, mock_service = drive_client_override
    files_mock = mock_service.files.return_value

    # Root folder exists, event folder exists
    files_mock.list.return_value.execute.side_effect = [
        {"files": [{"id": "existing_root_id", "name": "GDG Website"}]},
        {"files": [{"id": "existing_event_folder_id", "name": f"Intro to Antigravity (event #{test_event_id})"}]},
    ]
    files_mock.create.return_value.execute.side_effect = [
        {
            "id": "uploaded_file_id_2",
            "name": "slides_part2.pdf",
            "webViewLink": "https://drive.google.com/file/d/uploaded_file_id_2/view",
        }
    ]

    files = {"file": ("slides_part2.pdf", io.BytesIO(b"content"), "application/pdf")}
    res = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files)
    assert res.status_code == 201

    # Should only create the file, NOT the root or event folders
    assert files_mock.create.call_count == 1
    call_kwargs = files_mock.create.call_args[1]
    assert call_kwargs["body"]["parents"] == ["existing_event_folder_id"]


def test_upload_slides_403_org_policy_blocks_without_db_row(
    admin_client: TestClient, test_event_id: int, drive_client_override, db_session
):
    _, mock_service = drive_client_override
    files_mock = mock_service.files.return_value
    perms_mock = mock_service.permissions.return_value

    files_mock.list.return_value.execute.side_effect = [
        {"files": [{"id": "root_id"}]},
        {"files": [{"id": "event_folder_id"}]},
    ]
    files_mock.create.return_value.execute.return_value = {
        "id": "file_blocked_id",
        "name": "blocked.pdf",
        "webViewLink": "https://drive.google.com/file/d/file_blocked_id/view",
    }
    # Simulate 403 org policy error
    perms_mock.create.return_value.execute.side_effect = _make_http_error(403, "Workspace policy blocked sharing")

    files = {"file": ("blocked.pdf", io.BytesIO(b"content"), "application/pdf")}
    res = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files)
    assert res.status_code == 403
    assert res.json()["detail"] == "The workspace policy blocked public sharing — contact the board"

    # Verify file was cleaned up / deleted from Drive
    files_mock.delete.assert_called_once_with(fileId="file_blocked_id")

    # Verify NO database row was created
    row = db_session.query(EventContentLinks).filter_by(drive_file_id="file_blocked_id").first()
    assert row is None


def test_upload_slides_unconfigured_returns_503(admin_client: TestClient, test_event_id: int, db_session):
    # Override client with an unconfigured mock
    unconfigured_client = MagicMock(spec=GDriveClient)
    unconfigured_client.is_configured.return_value = False
    app.dependency_overrides[get_gdrive_client] = lambda: unconfigured_client

    try:
        files = {"file": ("slides.pdf", io.BytesIO(b"content"), "application/pdf")}
        res = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files)
        assert res.status_code == 503
        assert res.json()["detail"] == "Drive integration not configured — contact a super admin"

        # Verify NO row in DB
        rows = db_session.query(EventContentLinks).filter_by(event_id=test_event_id).all()
        assert len(rows) == 0
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)


def test_upload_slides_event_not_found(admin_client: TestClient, drive_client_override):
    files = {"file": ("slides.pdf", io.BytesIO(b"content"), "application/pdf")}
    res = admin_client.post("/upload/drive-slides/?event_id=999999", files=files)
    assert res.status_code == 404


def test_upload_slides_duplicate_label_auto_increments(
    admin_client: TestClient, test_event_id: int, drive_client_override
):
    _, mock_service = drive_client_override
    files_mock = mock_service.files.return_value

    files_mock.list.return_value.execute.return_value = {"files": [{"id": "root_id"}]}
    files_mock.create.return_value.execute.side_effect = [
        {"id": "file_1", "webViewLink": "https://drive.google.com/file/d/file_1/view"},
        {"id": "file_2", "webViewLink": "https://drive.google.com/file/d/file_2/view"},
    ]

    files = {"file": ("presentation.pdf", io.BytesIO(b"content 1"), "application/pdf")}
    res1 = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files)
    assert res1.status_code == 201
    assert res1.json()["label"] == "presentation"

    # Upload second file with same name
    files2 = {"file": ("presentation.pdf", io.BytesIO(b"content 2"), "application/pdf")}
    res2 = admin_client.post(f"/upload/drive-slides/?event_id={test_event_id}", files=files2)
    assert res2.status_code == 201
    assert res2.json()["label"] == "presentation (2)"


def test_drive_storage_quota_endpoint_success(admin_client: TestClient, drive_client_override):
    _, mock_service = drive_client_override
    about_mock = mock_service.about.return_value
    about_mock.get.return_value.execute.return_value = {
        "storageQuota": {
            "limit": "10000000000",
            "usage": "8500000000",
            "usageInDrive": "7000000000",
            "usageInDriveTrash": "50000000",
        }
    }

    res = admin_client.get("/upload/drive-storage-quota")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert data["limit"] == 10000000000
    assert data["usage"] == 8500000000
    assert data["usage_in_drive"] == 7000000000
    assert data["usage_in_drive_trash"] == 50000000
    assert data["usage_percentage"] == 85.0


def test_drive_storage_quota_endpoint_fail_soft_on_error(admin_client: TestClient, drive_client_override):
    _, mock_service = drive_client_override
    about_mock = mock_service.about.return_value
    about_mock.get.return_value.execute.side_effect = _make_http_error(500, "Google Drive internal error")

    res = admin_client.get("/upload/drive-storage-quota")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert data["usage"] is None
    assert data["limit"] is None
    assert data["usage_percentage"] is None


def test_drive_storage_quota_endpoint_unconfigured(admin_client: TestClient):
    unconfigured_client = GDriveClient()
    unconfigured_client.is_configured = lambda: False  # type: ignore[method-assign]
    app.dependency_overrides[get_gdrive_client] = lambda: unconfigured_client
    try:
        res = admin_client.get("/upload/drive-storage-quota")
        assert res.status_code == 503
        assert res.json()["code"] == "drive_not_configured"
    finally:
        app.dependency_overrides.pop(get_gdrive_client, None)
