import mimetypes
import os
import uuid
from typing import Annotated

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi_clerk_auth import HTTPAuthorizationCredentials

from app.config import config
from app.helpers import admin_guard
from app.routers.logging import LogFile, write_log

router = APIRouter()

ALLOWED_ATTACHMENT_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


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


@router.post("/", status_code=201)
async def upload_file(
    file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with LogFile("upload"):
        file_id = str(uuid.uuid4())
        extension = get_extension(file.filename, file.content_type)
        key = f"event-images/{file_id}{extension}"

        write_log(f"Uploading file: {file.filename} -> {key}")

        client = get_r2_client()
        content = await file.read()
        client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type)

        url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
        write_log(f"Upload successful: {url}")
        return {"url": url}


@router.post("/email-attachment", status_code=201)
async def upload_email_attachment(
    file: Annotated[UploadFile, File()], credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]
):
    with LogFile("upload email attachment"):
        if file.content_type not in ALLOWED_ATTACHMENT_CONTENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported attachment type: {file.content_type}. Allowed types: images and PDF.",
            )

        content = await file.read()
        if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Attachment exceeds the {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)}MB size limit.",
            )

        file_id = str(uuid.uuid4())
        extension = get_extension(file.filename, file.content_type)
        key = f"email-attachments/{file_id}{extension}"

        write_log(f"Uploading email attachment: {file.filename} -> {key}")

        client = get_r2_client()
        client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type)

        url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
        write_log(f"Upload successful: {url}")
        return {"url": url, "filename": file.filename, "content_type": file.content_type, "size": len(content)}
