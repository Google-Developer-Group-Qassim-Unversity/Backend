"""Unit tests for the Google Drive client helper and configuration."""

from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.clients import GDriveClient as AppGDriveClient, get_gdrive_client as app_get_gdrive_client
from app.config import MissingSettingError, config, reload_settings
from app.exceptions import DriveIntegrationNotConfigured, DrivePolicyBlocked, NotFound
from app.services.gdrive_client import GDriveClient, get_drive_credentials, get_gdrive_client, is_drive_configured


def _make_http_error(status: int, reason: str = "") -> HttpError:
    return HttpError(Response({"status": str(status), "reason": reason}), b"{}")


class TestGDriveConfig:
    def test_is_configured_true_when_all_credentials_set(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "mock_client_id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "mock_client_secret")
        monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "mock_refresh_token")
        reload_settings()

        assert is_drive_configured() is True
        client = GDriveClient()
        assert client.is_configured() is True

    @pytest.mark.parametrize("missing_var", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"])
    def test_is_configured_false_when_any_credential_missing(self, monkeypatch, missing_var):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "mock_client_id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "mock_client_secret")
        monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "mock_refresh_token")
        monkeypatch.delenv(missing_var, raising=False)
        reload_settings()

        assert is_drive_configured() is False
        client = GDriveClient()
        assert client.is_configured() is False

    def test_is_configured_false_when_empty_string(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "mock_client_secret")
        monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "mock_refresh_token")
        reload_settings()

        assert is_drive_configured() is False
        client = GDriveClient()
        assert client.is_configured() is False

    def test_injected_service_is_always_configured(self):
        mock_service = MagicMock()
        client = GDriveClient(service=mock_service)
        assert client.is_configured() is True

    def test_calendar_and_organizer_config_properties(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CALENDAR_ID", "primary_cal_id")
        monkeypatch.setenv("GOOGLE_ORGANIZER_EMAIL", "organizer@gdg-q.com")
        monkeypatch.setenv("APP_TIMEZONE", "Asia/Riyadh")
        reload_settings()

        assert config.GOOGLE_CALENDAR_ID == "primary_cal_id"
        assert config.GOOGLE_ORGANIZER_EMAIL == "organizer@gdg-q.com"
        assert config.APP_TIMEZONE == "Asia/Riyadh"

    def test_calendar_missing_raises_missing_setting_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
        reload_settings()
        with pytest.raises(MissingSettingError, match="GOOGLE_CALENDAR_ID"):
            _ = config.GOOGLE_CALENDAR_ID

    def test_organizer_email_missing_raises_missing_setting_error(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_ORGANIZER_EMAIL", raising=False)
        reload_settings()
        with pytest.raises(MissingSettingError, match="GOOGLE_ORGANIZER_EMAIL"):
            _ = config.GOOGLE_ORGANIZER_EMAIL

    def test_app_timezone_defaults_to_asia_riyadh(self, monkeypatch):
        monkeypatch.delenv("APP_TIMEZONE", raising=False)
        reload_settings()
        assert config.APP_TIMEZONE == "Asia/Riyadh"


class TestGDriveUnconfiguredDegradation:
    @pytest.fixture(autouse=True)
    def unconfigured_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
        reload_settings()

    def test_get_drive_credentials_raises_503(self):
        with pytest.raises(DriveIntegrationNotConfigured) as exc_info:
            get_drive_credentials()
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Drive integration not configured — contact a super admin"
        assert exc_info.value.code == "drive_not_configured"

    def test_client_methods_raise_503_when_unconfigured(self):
        client = GDriveClient()
        with pytest.raises(DriveIntegrationNotConfigured):
            client.get_file_link("file_123")

        with pytest.raises(DriveIntegrationNotConfigured):
            client.make_public("file_123")

        with pytest.raises(DriveIntegrationNotConfigured):
            client.is_file_public("file_123")


class TestGDriveCredentialsRefresh:
    def test_credentials_flow_never_passes_scope(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test_id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test_secret")
        monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "test_refresh")
        reload_settings()

        with patch("app.services.gdrive_client.Credentials") as mock_creds_cls:
            mock_creds = MagicMock()
            mock_creds.valid = False
            mock_creds_cls.return_value = mock_creds

            creds = get_drive_credentials()
            assert creds == mock_creds

            mock_creds_cls.assert_called_once_with(
                None,
                refresh_token="test_refresh",
                token_uri="https://oauth2.googleapis.com/token",
                client_id="test_id",
                client_secret="test_secret",
            )
            # HARD RULE: NEVER pass scope= when refreshing
            mock_creds.refresh.assert_called_once()
            args, kwargs = mock_creds.refresh.call_args
            assert len(args) == 1  # GoogleRequest instance
            assert "scope" not in kwargs
            assert "scopes" not in kwargs

    def test_refresh_error_raises_drive_not_configured(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test_id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test_secret")
        monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "test_refresh")
        reload_settings()

        with patch("app.services.gdrive_client.Credentials") as mock_creds_cls:
            mock_creds = MagicMock()
            mock_creds.valid = False
            mock_creds.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
            mock_creds_cls.return_value = mock_creds

            with pytest.raises(DriveIntegrationNotConfigured) as exc_info:
                get_drive_credentials()
            assert exc_info.value.status_code == 503


class TestGDriveClientOperations:
    @pytest.fixture
    def mock_service(self):
        return MagicMock()

    @pytest.fixture
    def client(self, mock_service):
        return GDriveClient(service=mock_service)

    def test_get_file_link_returns_web_view_link(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_get = mock_files.get.return_value
        mock_get.execute.return_value = {"webViewLink": "https://drive.google.com/file/d/abc123xyz/view?usp=drivesdk"}

        link = client.get_file_link("abc123xyz")
        assert link == "https://drive.google.com/file/d/abc123xyz/view?usp=drivesdk"
        mock_files.get.assert_called_once_with(fileId="abc123xyz", fields="webViewLink")

    def test_get_file_link_falls_back_when_missing_key(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_get = mock_files.get.return_value
        mock_get.execute.return_value = {}

        link = client.get_file_link("abc123xyz")
        assert link == "https://drive.google.com/file/d/abc123xyz/view"

    def test_get_file_link_not_found(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.get.return_value.execute.side_effect = _make_http_error(404, "File not found")

        with pytest.raises(NotFound, match="Google Drive file with id 'abc123xyz' not found"):
            client.get_file_link("abc123xyz")

    def test_make_public_success(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_create = mock_perms.create.return_value
        mock_create.execute.return_value = {"id": "anyoneWithLink", "role": "reader", "type": "anyone"}

        result = client.make_public("abc123xyz")
        assert result is True
        mock_perms.create.assert_called_once_with(fileId="abc123xyz", body={"role": "reader", "type": "anyone"})

    def test_make_public_org_policy_403_returns_false(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.create.return_value.execute.side_effect = _make_http_error(403, "Sharing blocked by admin policy")

        result = client.make_public("abc123xyz")
        assert result is False

    def test_make_public_org_policy_403_raises_when_requested(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.create.return_value.execute.side_effect = _make_http_error(403, "Sharing blocked by admin policy")

        with pytest.raises(DrivePolicyBlocked) as exc_info:
            client.make_public("abc123xyz", raise_on_blocked=True)
        assert exc_info.value.status_code == 403
        assert exc_info.value.code == "drive_policy_blocked"
        assert exc_info.value.detail == "The workspace policy blocked public sharing — contact the board"

    def test_make_public_not_found(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.create.return_value.execute.side_effect = _make_http_error(404, "File not found")

        with pytest.raises(NotFound):
            client.make_public("abc123xyz")

    def test_is_file_public_true(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.list.return_value.execute.return_value = {
            "permissions": [
                {"id": "user1", "type": "user", "role": "owner"},
                {"id": "anyoneWithLink", "type": "anyone", "role": "reader"},
            ]
        }

        assert client.is_file_public("abc123xyz") is True
        mock_perms.list.assert_called_once_with(fileId="abc123xyz", fields="permissions(type,role)")

    def test_is_file_public_false_when_no_anyone_perm(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.list.return_value.execute.return_value = {
            "permissions": [
                {"id": "user1", "type": "user", "role": "owner"},
                {"id": "user2", "type": "user", "role": "reader"},
            ]
        }

        assert client.is_file_public("abc123xyz") is False

    def test_is_file_public_false_on_403(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.list.return_value.execute.side_effect = _make_http_error(403, "Forbidden")

        assert client.is_file_public("abc123xyz") is False

    def test_is_file_public_not_found_raises(self, client, mock_service):
        mock_perms = mock_service.permissions.return_value
        mock_perms.list.return_value.execute.side_effect = _make_http_error(404, "File not found")

        with pytest.raises(NotFound):
            client.is_file_public("abc123xyz")

    def test_get_or_create_root_folder_found(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.list.return_value.execute.return_value = {
            "files": [{"id": "root_folder_123", "name": "GDG Website"}]
        }

        folder_id = client.get_or_create_root_folder("GDG Website")
        assert folder_id == "root_folder_123"
        mock_files.create.assert_not_called()

    def test_get_or_create_root_folder_creates_when_missing(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.list.return_value.execute.return_value = {"files": []}
        mock_files.create.return_value.execute.return_value = {"id": "new_root_id"}

        folder_id = client.get_or_create_root_folder("GDG Website")
        assert folder_id == "new_root_id"
        mock_files.create.assert_called_once_with(
            body={"name": "GDG Website", "mimeType": "application/vnd.google-apps.folder"}, fields="id"
        )

    def test_get_or_create_event_folder_found(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.list.return_value.execute.return_value = {
            "files": [{"id": "event_folder_abc", "name": "Hackathon (event #42)"}]
        }

        folder_id = client.get_or_create_event_folder(42, "Hackathon", root_folder_id="root_123")
        assert folder_id == "event_folder_abc"
        mock_files.create.assert_not_called()

    def test_get_or_create_event_folder_creates_when_missing(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.list.return_value.execute.return_value = {"files": []}
        mock_files.create.return_value.execute.return_value = {"id": "new_event_folder_id"}

        folder_id = client.get_or_create_event_folder(42, "Hackathon", root_folder_id="root_123")
        assert folder_id == "new_event_folder_id"
        mock_files.create.assert_called_once_with(
            body={
                "name": "Hackathon (event #42)",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["root_123"],
            },
            fields="id",
        )

    def test_upload_file_stream(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.create.return_value.execute.return_value = {
            "id": "uploaded_123",
            "name": "slides.pdf",
            "webViewLink": "https://drive.google.com/file/d/uploaded_123/view",
        }

        import io

        file_stream = io.BytesIO(b"slide content")
        result = client.upload_file(file_stream, "slides.pdf", "application/pdf", "folder_xyz")
        assert result["id"] == "uploaded_123"
        mock_files.create.assert_called_once()
        call_kwargs = mock_files.create.call_args[1]
        assert call_kwargs["body"] == {"name": "slides.pdf", "parents": ["folder_xyz"]}
        assert call_kwargs["fields"] == "id, name, webViewLink"

    def test_delete_file_success(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.delete.return_value.execute.return_value = {}

        client.delete_file("file_to_del")
        mock_files.delete.assert_called_once_with(fileId="file_to_del")

    def test_delete_file_404_ignored(self, client, mock_service):
        mock_files = mock_service.files.return_value
        mock_files.delete.return_value.execute.side_effect = _make_http_error(404, "Not found")

        client.delete_file("already_gone")

    def test_get_storage_quota_success(self, client, mock_service):
        mock_about = mock_service.about.return_value
        mock_about.get.return_value.execute.return_value = {
            "storageQuota": {
                "limit": "15000000000",
                "usage": "7500000000",
                "usageInDrive": "5000000000",
                "usageInDriveTrash": "1000000",
            }
        }
        res = client.get_storage_quota()
        assert res["limit"] == 15000000000
        assert res["usage"] == 7500000000
        assert res["usage_in_drive"] == 5000000000
        assert res["usage_in_drive_trash"] == 1000000
        mock_about.get.assert_called_once_with(fields="storageQuota")

    def test_get_storage_quota_unlimited(self, client, mock_service):
        mock_about = mock_service.about.return_value
        mock_about.get.return_value.execute.return_value = {"storageQuota": {"usage": "104857600"}}
        res = client.get_storage_quota()
        assert res["limit"] is None
        assert res["usage"] == 104857600
        assert res["usage_in_drive"] is None
        assert res["usage_in_drive_trash"] is None

    def test_get_storage_quota_unconfigured(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
        reload_settings()
        unconfigured_client = GDriveClient()
        with pytest.raises(DriveIntegrationNotConfigured):
            unconfigured_client.get_storage_quota()

    def test_clients_re_exports(self):
        assert AppGDriveClient is GDriveClient
        assert app_get_gdrive_client is not None
        assert isinstance(get_gdrive_client(), GDriveClient)
