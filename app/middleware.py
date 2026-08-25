"""Request-scoped logging context."""

import logging
import time
import uuid

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import request_id_var

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Give every request an id, log one summary line, and tell Sentry about it.

    The id is echoed back in `X-Request-ID` so a user reporting a failure can
    quote something that maps to exact log lines, and it is set as a Sentry tag
    so an error event links to them too.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        sentry_sdk.set_tag("request_id", request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("%s %s -> %d in %.1fms", request.method, request.url.path, response.status_code, elapsed_ms)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            # the exception handlers own the response; this only records timing
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception("%s %s failed after %.1fms", request.method, request.url.path, elapsed_ms)
            raise
        finally:
            # reset last: the lines above must still carry this request's id
            request_id_var.reset(token)
