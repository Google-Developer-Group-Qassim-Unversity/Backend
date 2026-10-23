import logging
import mimetypes
import os
import uuid
from typing import Annotated, Any


from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from sqlalchemy.exc import IntegrityError

from app.clients import GDrive, R2Client
from app.config import config
from app.DB import events as event_queries
from app.DB.schema import EventContentLinks, EventHistoryAction, EventsContentLinkKind
from app.dependencies import DB
from app.exceptions import DriveIntegrationNotConfigured
from app.helpers import CurrentMember
from app.leaderboard_cache import reset_leaderboard_cache_best_effort
from app.routers.models import (
    ConflictResponse,
    DriveStorageQuotaResponse_model,
    EventContentLink_model,
    NotFoundResponse,
)
from app.routers.responses import AttachmentUploadResponse, UploadResponse
from app.services import event_history
from app.services.push import cases as push_cases
from app.services.push import delivery as push_delivery
from app.services.permissions.catalogue import Perm
from app.services.permissions.departments import event_departments
from app.services.permissions.guards import Require, Staff


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/upload", tags=["upload"])

ALLOWED_ATTACHMENT_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024


def get_extension(filename: str | None, content_type: str | None) -> str:
    if filename:
        ext = os.path.splitext(filename)[1]
        if ext:
            return ext
    if content_type:
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ext
    return ""


@router.post("/", status_code=201, response_model=UploadResponse, dependencies=[Depends(Require(Perm.UPLOADS))])
async def upload_file(file: Annotated[UploadFile, File()], client: R2Client):
    file_id = str(uuid.uuid4())
    extension = get_extension(file.filename, file.content_type)
    key = f"event-images/{file_id}{extension}"

    logger.info(f"Uploading file: {file.filename} -> {key}")

    content = await file.read()
    # boto3 is synchronous; without the threadpool this blocks the event loop
    # for the whole upload, stalling every other request on this worker
    await run_in_threadpool(
        client.put_object, Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type
    )

    url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
    logger.info(f"Upload successful: {url}")
    return {"url": url}


@router.post(
    "/email-attachment",
    status_code=201,
    response_model=AttachmentUploadResponse,
    dependencies=[Depends(Require(Perm.UPLOADS))],
)
async def upload_email_attachment(file: Annotated[UploadFile, File()], client: R2Client):
    if file.content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Unsupported attachment type: {file.content_type}. Allowed types: images and PDF."
        )

    content = await file.read()
    if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise HTTPException(
            status_code=400, detail=f"Attachment exceeds the {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)}MB size limit."
        )

    file_id = str(uuid.uuid4())
    extension = get_extension(file.filename, file.content_type)
    key = f"email-attachments/{file_id}{extension}"

    logger.info(f"Uploading email attachment: {file.filename} -> {key}")

    # boto3 is synchronous; without the threadpool this blocks the event loop
    # for the whole upload, stalling every other request on this worker
    await run_in_threadpool(
        client.put_object, Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type
    )

    url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
    logger.info(f"Upload successful: {url}")
    return {"url": url, "filename": file.filename, "content_type": file.content_type, "size": len(content)}


def clean_filename_for_label(filename: str | None) -> str:
    """Derive a friendly initial label from the uploaded file's original name."""
    if not filename:
        return "Slides"
    base = os.path.splitext(os.path.basename(filename))[0]
    cleaned = base.replace("_", " ").strip()
    return cleaned or "Slides"


_content_link_snapshot = event_queries.content_link_snapshot

ALLOWED_SLIDE_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".key", ".odp"}
MAX_SLIDES_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB


class _CountingReader:
    """Wrapper around file stream enforcing a byte limit for MediaIoBaseUpload."""

    def __init__(self, fh: Any, limit: int):
        self._fh = fh
        self._limit = limit
        self._seen = 0

    def read(self, size: int = -1) -> bytes:
        buf = self._fh.read(size)
        if not buf:
            return buf
        self._seen += len(buf)
        if self._seen > self._limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds the 100MB limit")
        return buf

    def seek(self, offset: int, whence: int = 0) -> int:
        res = self._fh.seek(offset, whence)
        if hasattr(self._fh, "tell") and self._fh.tell() > self._limit:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds the 100MB limit")
        return res

    def tell(self) -> int:
        return self._fh.tell()


@router.post(
    "/drive-slides/",
    status_code=status.HTTP_201_CREATED,
    response_model=EventContentLink_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Event not found"},
        409: {"model": ConflictResponse, "description": "A link with this label already exists"},
        413: {"description": "File exceeds the 100MB limit"},
        422: {"description": "Only presentation files are supported or invalid parameters"},
    },
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def upload_drive_slides(
    request: Request,
    file: Annotated[UploadFile, File()],
    event_id: Annotated[int, Query(...)],
    session: DB,
    member: CurrentMember,
    gdrive: GDrive,
    background_tasks: BackgroundTasks,
):
    """Stream multipart upload into My-Drive 'GDG Website/{event name} (event #{id})/'."""
    if not gdrive.is_configured():
        raise DriveIntegrationNotConfigured()

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SLIDES_SIZE_BYTES + 2 * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds the 100MB limit"
                )
        except ValueError:
            pass

    filename = file.filename or "slides"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_SLIDE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only presentation files (PDF/PPTX/PPT/KEY/ODP) are supported",
        )

    event = event_queries.get_event_by_id(session, event_id)

    root_folder_id = gdrive.get_or_create_root_folder("GDG Website")
    event_folder_id = gdrive.get_or_create_event_folder(event.id, event.name, root_folder_id)

    counting_stream = _CountingReader(file.file, MAX_SLIDES_SIZE_BYTES)
    uploaded = gdrive.upload_file(counting_stream, filename, file.content_type, event_folder_id)
    drive_file_id = uploaded["id"]

    try:
        gdrive.make_public(drive_file_id, raise_on_blocked=True)

        file_url = uploaded.get("webViewLink") or gdrive.get_file_link(drive_file_id)

        cleaned_label = clean_filename_for_label(filename)
        label = cleaned_label
        counter = 2
        while event_queries.is_content_link_label_taken(session, event.id, label):
            label = f"{cleaned_label} ({counter})"
            counter += 1

        link = EventContentLinks(
            event_id=event.id,
            label=label,
            url=file_url,
            kind=EventsContentLinkKind.SLIDES,
            drive_file_id=drive_file_id,
            published=1,
            created_by=member.id,
        )
        session.add(link)
        event_history.record(
            session,
            event.id,
            EventHistoryAction.CONTENT_LINK_CHANGED,
            member,
            {"content_link": [None, _content_link_snapshot(link)]},
        )
        pushed = push_cases.content_added(session, link)
        session.commit()
        session.refresh(link)
    except IntegrityError as exc:
        session.rollback()
        try:
            gdrive.delete_file(drive_file_id)
        except Exception:
            pass
        if not event_queries.is_duplicate_label_error(exc):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A content link with this label already exists for this event"
        )
    except Exception:
        session.rollback()
        try:
            gdrive.delete_file(drive_file_id)
        except Exception:
            pass
        raise

    if pushed:
        background_tasks.add_task(push_delivery.kick)
    event_queries.set_download_url(link)
    logger.info(f"Event [{event.id}] drive slide uploaded: link [{link.id}], file [{drive_file_id}]")
    reset_leaderboard_cache_best_effort()
    return link


@router.get(
    "/drive-storage-quota",
    status_code=status.HTTP_200_OK,
    response_model=DriveStorageQuotaResponse_model,
    dependencies=[Depends(Staff)],
)
def get_drive_storage_quota(gdrive: GDrive):
    """Retrieve Google Drive storage quota telemetry (fail-soft)."""
    if not gdrive.is_configured():
        raise DriveIntegrationNotConfigured()

    try:
        quota = gdrive.get_storage_quota()
        limit = quota.get("limit")
        usage = quota.get("usage", 0)
        usage_pct: float | None = None
        if limit and limit > 0:
            usage_pct = round((usage / limit) * 100, 1)

        return {
            "configured": True,
            "usage": usage,
            "limit": limit,
            "usage_in_drive": quota.get("usage_in_drive"),
            "usage_in_drive_trash": quota.get("usage_in_drive_trash"),
            "usage_percentage": usage_pct,
        }
    except DriveIntegrationNotConfigured:
        raise
    except Exception as exc:
        logger.warning("Error fetching Drive storage quota: %s", exc)
        return {
            "configured": True,
            "usage": None,
            "limit": None,
            "usage_in_drive": None,
            "usage_in_drive_trash": None,
            "usage_percentage": None,
        }
