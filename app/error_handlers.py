"""Application-wide exception handlers.

Routes raise; they do not translate. Anything a handler here covers should not
be wrapped in a `try`/`except` inside a route.

Rollback is not this module's job either - the ``get_db`` dependency already
rolls back when a handler raises.
"""

import logging

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError, TimeoutError as SQLAlchemyTimeoutError

from app.exceptions import KnownHttpException

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(KnownHttpException)
    def known_http_exception_handler(request: Request, exc: KnownHttpException) -> JSONResponse:
        """Deliberate, expected failures - `NotFound`, `Conflict`, upstream 502/503/504.

        These are part of the API contract, so they are logged but never reported
        to Sentry, which keeps expected 5xx (a slow certificate API, say) out of
        the error feed.
        """
        logger.info("%s %s -> %d %s", request.method, request.url.path, exc.status_code, exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    @app.exception_handler(OperationalError)
    def database_operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("OperationalError on %s %s", request.method, request.url.path, exc_info=exc)
        sentry_sdk.capture_exception(exc)
        return JSONResponse(
            status_code=503, content={"detail": "Database temporarily unavailable. Please retry shortly."}
        )

    @app.exception_handler(SQLAlchemyTimeoutError)
    def database_timeout_error_handler(request: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        logger.error("Connection pool timeout on %s %s", request.method, request.url.path, exc_info=exc)
        sentry_sdk.capture_exception(exc)
        return JSONResponse(status_code=503, content={"detail": "Database is under heavy load. Please retry shortly."})

    @app.exception_handler(IntegrityError)
    def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        """A constraint the request violated - a duplicate key, a bad foreign key.

        The driver message can name columns and values, so it is logged rather
        than returned.
        """
        logger.warning("IntegrityError on %s %s", request.method, request.url.path, exc_info=exc)
        sentry_sdk.capture_exception(exc)
        return JSONResponse(status_code=409, content={"detail": "This conflicts with data that already exists."})

    @app.exception_handler(SQLAlchemyError)
    def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("Database error on %s %s", request.method, request.url.path, exc_info=exc)
        sentry_sdk.capture_exception(exc)
        return JSONResponse(status_code=500, content={"detail": "A database error occurred."})

    @app.exception_handler(Exception)
    def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last resort.

        Starlette re-raises after this returns, so the Sentry ASGI integration
        reports it; capturing here as well would duplicate the event.
        """
        logger.exception("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
