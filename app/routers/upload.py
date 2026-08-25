import logging
import mimetypes
import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile


from app.clients import R2Client
from app.config import config
from app.helpers import admin_guard

from app.routers.responses import AttachmentUploadResponse, UploadResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/upload", tags=["upload"], dependencies=[Depends(admin_guard)])

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


@router.post("/", status_code=201, response_model=UploadResponse)
async def upload_file(file: Annotated[UploadFile, File()], client: R2Client):
    file_id = str(uuid.uuid4())
    extension = get_extension(file.filename, file.content_type)
    key = f"event-images/{file_id}{extension}"

    logger.info(f"Uploading file: {file.filename} -> {key}")

    content = await file.read()
    client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type)

    url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
    logger.info(f"Upload successful: {url}")
    return {"url": url}


@router.post("/email-attachment", status_code=201, response_model=AttachmentUploadResponse)
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

    client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type)

    url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
    logger.info(f"Upload successful: {url}")
    return {"url": url, "filename": file.filename, "content_type": file.content_type, "size": len(content)}
