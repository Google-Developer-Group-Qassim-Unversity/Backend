"""Google Drive and Workspace API client wrapper.

Provides authenticated operations against Google Drive using the organizer account's
refresh token (without passing scope= during refresh).
"""

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from app.config import get_settings
from app.exceptions import DriveIntegrationNotConfigured, DrivePolicyBlocked, NotFound

logger = logging.getLogger(__name__)

_MEET_CODE_RE = re.compile(r"^[a-zA-Z]{2,5}-[a-zA-Z]{2,5}-[a-zA-Z]{2,5}$")


def extract_meet_code(url_or_code: str | None) -> str | None:
    """Extract a bare Google Meet code from a full URL, bare domain, or code string.

    Returns the lowercased code, e.g. 'abc-defg-hij', or None if invalid or not a Google Meet link.
    """
    if not url_or_code:
        return None
    raw = url_or_code.strip()
    if not raw:
        return None

    if "://" in raw or raw.startswith("meet.google.com"):
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        netloc = parsed.netloc.lower()
        if netloc != "meet.google.com" and not netloc.endswith(".meet.google.com"):
            return None
        path = parsed.path.strip("/")
        segments = [s for s in path.split("/") if s]
        if not segments:
            return None
        candidate = segments[-1].lower()
    else:
        candidate = raw.lower()

    if _MEET_CODE_RE.match(candidate):
        return candidate
    return None


def _format_rfc3339(dt: datetime) -> str:
    """Format datetime as RFC 3339 timestamp for Google Drive search queries."""
    if dt.tzinfo is None:
        settings = get_settings()
        dt = dt.replace(tzinfo=ZoneInfo(settings.APP_TIMEZONE))
    return dt.isoformat()


def _escape_drive_query(value: str) -> str:
    """Escape query parameters for Drive search query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def is_drive_configured() -> bool:
    """Return True if the required Google credentials are set in configuration."""
    settings = get_settings()
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REFRESH_TOKEN)


_credentials_cache: dict[tuple[str, str, str], Credentials] = {}
_credentials_lock = threading.Lock()


def _clear_credentials_cache() -> None:
    """Clear cached credentials. Intended for test isolation."""
    with _credentials_lock:
        _credentials_cache.clear()


def get_drive_credentials() -> Credentials:
    """Build Credentials using the refresh token flow without scope= parameter.

    Caches valid credentials in memory under a lock to prevent duplicate refreshes.
    Raises DriveIntegrationNotConfigured if credentials are not configured or invalid.
    """
    settings = get_settings()
    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and settings.GOOGLE_REFRESH_TOKEN):
        raise DriveIntegrationNotConfigured()

    key = (settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET, settings.GOOGLE_REFRESH_TOKEN)
    with _credentials_lock:
        creds = _credentials_cache.get(key)
        if creds is not None and creds.valid:
            return creds

        try:
            credentials = Credentials(
                None,
                refresh_token=settings.GOOGLE_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_CLIENT_ID,
                client_secret=settings.GOOGLE_CLIENT_SECRET,
            )
            if not credentials.valid:
                # HARD RULE: NEVER pass scope= or scopes= when refreshing
                credentials.refresh(GoogleRequest())
            _credentials_cache[key] = credentials
            return credentials
        except RefreshError as exc:
            logger.error("Failed to refresh Google Drive credentials: %s", exc)
            raise DriveIntegrationNotConfigured() from exc


class GDriveClient:
    """High-level client for Google Drive and Google Meet operations."""

    def __init__(self, service: Any = None, meet_service: Any = None) -> None:
        self._service: Any = service
        self._meet_service: Any = meet_service

    def is_configured(self) -> bool:
        """Check if Drive integration credentials are configured or services are injected."""
        if self._service is not None or self._meet_service is not None:
            return True
        return is_drive_configured()

    def get_service(self) -> Any:
        """Get or lazily create the Google Drive API service resource."""
        if self._service is not None:
            return self._service
        credentials = get_drive_credentials()
        return build("drive", "v3", credentials=credentials)

    def get_meet_service(self) -> Any:
        """Get or lazily create the Google Meet API v2 service resource."""
        if self._meet_service is not None:
            return self._meet_service
        credentials = get_drive_credentials()
        return build("meet", "v2", credentials=credentials)

    def get_file_link(self, file_id: str) -> str:
        """Retrieve the webViewLink for a Drive file.

        Falls back to standard view URL format if webViewLink is absent.
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        try:
            res = service.files().get(fileId=file_id, fields="webViewLink").execute()
            link = res.get("webViewLink")
            if link:
                return str(link)
            return f"https://drive.google.com/file/d/{file_id}/view"
        except HttpError as exc:
            if exc.resp.status == 404:
                raise NotFound("Google Drive file", file_id) from exc
            raise

    def make_public(self, file_id: str, *, raise_on_blocked: bool = False) -> bool:
        """Grant public read permission ('anyone with the link can view') to a file.

        Handles Google Workspace organization policy 403 blocks gracefully.
        Returns True on success, False if blocked by org policy.
        If raise_on_blocked is True, raises DrivePolicyBlocked on 403.
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        try:
            service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
            return True
        except HttpError as exc:
            if exc.resp.status == 403:
                logger.warning("Org policy blocked public sharing for Drive file %s: %s", file_id, exc)
                if raise_on_blocked:
                    raise DrivePolicyBlocked() from exc
                return False
            if exc.resp.status == 404:
                raise NotFound("Google Drive file", file_id) from exc
            raise

    def is_file_public(self, file_id: str) -> bool:
        """Check whether a file is already shared with 'anyone'."""
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        try:
            res = service.permissions().list(fileId=file_id, fields="permissions(type,role)").execute()
            permissions = res.get("permissions", [])
            for perm in permissions:
                if perm.get("type") == "anyone":
                    return True
            return False
        except HttpError as exc:
            if exc.resp.status == 404:
                raise NotFound("Google Drive file", file_id) from exc
            if exc.resp.status == 403:
                logger.warning("Forbidden when checking permissions for Drive file %s: %s", file_id, exc)
                return False
            raise

    def get_or_create_root_folder(self, folder_name: str = "GDG Website") -> str:
        """Find or create a folder at the My-Drive root by name.

        Uses official query: name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false.
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        escaped_name = _escape_drive_query(folder_name)
        q = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res = service.files().list(q=q, fields="files(id, name)", spaces="drive", orderBy="createdTime").execute()
        files = res.get("files", [])
        if files:
            return str(files[0]["id"])

        created = (
            service.files()
            .create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id")
            .execute()
        )
        return str(created["id"])

    def get_or_create_event_folder(self, event_id: int, event_name: str, root_folder_id: str | None = None) -> str:
        """Find or create an event subfolder: '{event name} (event #{id})' under root_folder_id."""
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        if root_folder_id is None:
            root_folder_id = self.get_or_create_root_folder()

        service = self.get_service()
        folder_name = f"{event_name} (event #{event_id})"
        escaped_name = _escape_drive_query(folder_name)
        q = (
            f"'{root_folder_id}' in parents and name = '{escaped_name}' and "
            f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        res = service.files().list(q=q, fields="files(id, name)", spaces="drive", orderBy="createdTime").execute()

        files = res.get("files", [])
        if files:
            return str(files[0]["id"])

        created = (
            service.files()
            .create(
                body={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [root_folder_id],
                },
                fields="id",
            )
            .execute()
        )
        return str(created["id"])

    def upload_file(
        self, file_obj: Any, filename: str, content_type: str | None, parent_folder_id: str
    ) -> dict[str, Any]:
        """Upload a file stream into the given parent folder using resumable upload.

        No disk temporary files or base64 encoding.
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        media = MediaIoBaseUpload(file_obj, mimetype=content_type or "application/octet-stream", resumable=True)
        body = {"name": filename, "parents": [parent_folder_id]}
        return service.files().create(body=body, media_body=media, fields="id, name, webViewLink").execute()

    def delete_file(self, file_id: str) -> None:
        """Permanently delete a Drive file."""
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        try:
            service.files().delete(fileId=file_id).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return
            raise

    def get_storage_quota(self) -> dict[str, Any]:
        """Fetch storage quota for the Drive account using about.get(fields="storageQuota").

        Returns a dictionary with:
        - usage: bytes used across all Google services (int)
        - limit: total storage limit in bytes, or None if unlimited (int | None)
        - usage_in_drive: bytes used specifically in Google Drive (int | None)
        - usage_in_drive_trash: bytes used in Google Drive trash (int | None)
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        service = self.get_service()
        try:
            res = service.about().get(fields="storageQuota").execute()
            quota = res.get("storageQuota", {})

            def _to_int(val: Any) -> int | None:
                if val is None:
                    return None
                try:
                    return int(val)
                except (ValueError, TypeError):
                    return None

            return {
                "usage": _to_int(quota.get("usage")) or 0,
                "limit": _to_int(quota.get("limit")),
                "usage_in_drive": _to_int(quota.get("usageInDrive")),
                "usage_in_drive_trash": _to_int(quota.get("usageInDriveTrash")),
            }
        except Exception as exc:
            logger.warning("Failed to fetch storage quota from Google Drive: %s", exc)
            raise

    def search_legacy_recordings(
        self, start_dt: datetime, end_dt: datetime, exclude_file_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Fallback search in Drive for Google Meet recordings within the event time window."""
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        exclude_ids = exclude_file_ids or set()
        drive_service = self.get_service()

        window_start = start_dt - timedelta(hours=2)
        window_end = end_dt + timedelta(hours=24)
        start_iso = _format_rfc3339(window_start)
        end_iso = _format_rfc3339(window_end)

        found_files: list[dict[str, Any]] = []

        # 1. Search for "Google Meet" and "Legacy Meet Recordings" folders
        folder_q = (
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false and "
            "(name = 'Google Meet' or name = 'Legacy Meet Recordings')"
        )
        try:
            folder_res = drive_service.files().list(q=folder_q, fields="files(id, name)", spaces="drive").execute()
            folders = folder_res.get("files", []) if isinstance(folder_res, dict) else []
            if not isinstance(folders, list):
                folders = []

            if folders:
                folder_ids = [f["id"] for f in folders if isinstance(f, dict) and "id" in f]
                if folder_ids:
                    parent_cond = " or ".join([f"'{fid}' in parents" for fid in folder_ids])
                    subfolder_q = (
                        f"mimeType = 'application/vnd.google-apps.folder' and trashed = false and ({parent_cond})"
                    )
                    subfolder_res = (
                        drive_service.files().list(q=subfolder_q, fields="files(id, name)", spaces="drive").execute()
                    )
                    subfolders = subfolder_res.get("files", []) if isinstance(subfolder_res, dict) else []
                    if not isinstance(subfolders, list):
                        subfolders = []
                    all_parent_ids = folder_ids + [sf["id"] for sf in subfolders if isinstance(sf, dict) and "id" in sf]

                    parent_video_cond = " or ".join([f"'{pid}' in parents" for pid in all_parent_ids])
                    video_q = (
                        f"mimeType = 'video/mp4' and trashed = false and ({parent_video_cond}) and "
                        f"createdTime >= '{start_iso}' and createdTime <= '{end_iso}'"
                    )
                    video_res = (
                        drive_service.files()
                        .list(
                            q=video_q,
                            fields="files(id, name, webViewLink, webContentLink, createdTime)",
                            spaces="drive",
                        )
                        .execute()
                    )
                    found_files = video_res.get("files", []) if isinstance(video_res, dict) else []
                    if not isinstance(found_files, list):
                        found_files = []
        except Exception as exc:
            logger.warning("Error searching Google Meet folders in Drive: %s", exc)

        # 2. If folder search didn't find anything, do folder-agnostic search by createdTime
        if not found_files:
            try:
                agnostic_q = (
                    f"mimeType = 'video/mp4' and trashed = false and "
                    f"createdTime >= '{start_iso}' and createdTime <= '{end_iso}'"
                )
                agnostic_res = (
                    drive_service.files()
                    .list(
                        q=agnostic_q, fields="files(id, name, webViewLink, webContentLink, createdTime)", spaces="drive"
                    )
                    .execute()
                )
                found_files = agnostic_res.get("files", []) if isinstance(agnostic_res, dict) else []
                if not isinstance(found_files, list):
                    found_files = []
            except Exception as exc:
                logger.warning("Error in folder-agnostic recording search: %s", exc)

        candidates: list[dict[str, Any]] = []
        for f in found_files:
            fid = f["id"]
            if fid in exclude_ids:
                continue
            is_pub = self.is_file_public(fid)
            candidates.append(
                {
                    "file_id": fid,
                    "web_view_link": f.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view",
                    "name": f.get("name") or "Meet Recording",
                    "created_time": f.get("createdTime"),
                    "is_public": is_pub,
                }
            )

        candidates.sort(key=lambda c: str(c.get("created_time") or ""), reverse=True)
        return candidates

    def find_recording_candidates(
        self, meeting_url: str | None, start_dt: datetime, end_dt: datetime, exclude_file_ids: set[str] | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Discover recording candidates for an event.

        Returns (state, candidates), where state is 'processing' | 'ready' | 'absent'.
        """
        if not self.is_configured():
            raise DriveIntegrationNotConfigured()

        exclude_ids = exclude_file_ids or set()
        meet_code = extract_meet_code(meeting_url)

        candidates: list[dict[str, Any]] = []
        has_started = False

        if meet_code:
            meet_service = self.get_meet_service()
            try:
                space = meet_service.spaces().get(name=f"spaces/{meet_code}").execute()
                space_name = space.get("name")
                conf_resp = meet_service.conferenceRecords().list(filter=f'space.name="{space_name}"').execute()
                records = conf_resp.get("conferenceRecords", [])

                if records:
                    file_ids_with_time: list[tuple[str, str | None]] = []

                    for rec in records:
                        rec_name = rec.get("name")
                        rec_list_resp = meet_service.conferenceRecords().recordings().list(parent=rec_name).execute()
                        recordings = rec_list_resp.get("recordings", [])
                        for r in recordings:
                            st = r.get("state")
                            if st == "STARTED":
                                has_started = True
                            elif st == "FILE_GENERATED":
                                dest = r.get("driveDestination", {})
                                fid = dest.get("file")
                                if fid and fid not in exclude_ids:
                                    file_ids_with_time.append((fid, r.get("startTime") or rec.get("startTime")))

                    if file_ids_with_time:
                        drive_service = self.get_service()
                        for fid, meet_start_time in file_ids_with_time:
                            try:
                                f = (
                                    drive_service.files()
                                    .get(fileId=fid, fields="id, name, webViewLink, webContentLink, createdTime")
                                    .execute()
                                )
                                is_pub = self.is_file_public(fid)
                                candidates.append(
                                    {
                                        "file_id": fid,
                                        "web_view_link": f.get("webViewLink")
                                        or f"https://drive.google.com/file/d/{fid}/view",
                                        "name": f.get("name") or "Meeting Recording",
                                        "created_time": f.get("createdTime") or meet_start_time,
                                        "is_public": is_pub,
                                    }
                                )
                            except HttpError as exc:
                                if exc.resp.status == 404:
                                    continue
                                raise
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                # 404 on spaces.get -> fallback to legacy search
                pass

        if candidates:
            candidates.sort(key=lambda c: str(c.get("created_time") or ""), reverse=True)
            return ("ready", candidates)

        if has_started:
            return ("processing", [])

        return ("absent", [])


def get_gdrive_client() -> GDriveClient:
    """FastAPI dependency yielding a GDriveClient instance."""
    return GDriveClient()
