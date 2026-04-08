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
from app.routers.logging import LogFile, write_log, write_log_exception, write_log_traceback

router = APIRouter()


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

        try:
            client = get_r2_client()
            content = await file.read()
            client.put_object(Bucket=config.R2_BUCKET_NAME, Key=key, Body=content, ContentType=file.content_type)
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

        url = f"{config.R2_PUBLIC_URL.rstrip('/')}/{key}"
        write_log(f"Upload successful: {url}")
        return {"url": url}
