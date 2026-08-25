"""External service clients.

Two shapes, for two different callers:

- **`R2Client`** is a FastAPI dependency, so tests override it through
  `app.dependency_overrides` like any other dependency.
- **`get_http_client()`** is a plain accessor, because the code that needs it
  runs in `BackgroundTasks` after the response is sent, where dependency
  injection no longer reaches.
"""

import logging
from typing import Annotated, Any

import boto3
import httpx
from botocore.config import Config as BotoConfig
from fastapi import Depends

from app.config import config

logger = logging.getLogger(__name__)

# Individual calls override this; it is the ceiling, not the expectation.
DEFAULT_HTTP_TIMEOUT = 60.0

_http_client: httpx.AsyncClient | None = None


def get_r2_client() -> Any:
    """Cloudflare R2, over the S3 API."""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4"),
    )


R2Client = Annotated[Any, Depends(get_r2_client)]


async def open_http_client() -> None:
    """Create the shared client. Called from the app lifespan."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT)
        logger.debug("shared httpx client opened")


async def close_http_client() -> None:
    """Close it on shutdown, so connections are not dropped on the floor."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.debug("shared httpx client closed")


def get_http_client() -> httpx.AsyncClient:
    """The process-wide async HTTP client.

    One connection pool for the whole app instead of one per outbound call.
    Falls back to creating a client if the lifespan never ran - which is the
    case under `TestClient(app)` without a context manager.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT)
    return _http_client
