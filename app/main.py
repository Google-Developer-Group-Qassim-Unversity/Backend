import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.config import config
from app.DB.main import get_engine
from app.error_handlers import register_exception_handlers
from app.routers import (
    attendance,
    emails,
    upload,
    members,
    events,
    departments,
    action,
    custom,
    forms,
    submissions,
    submissions_manual,
    points,
    semesters,
    health,
    cache,
    wallet,
)

sentry_sdk.init(
    dsn=config.SENTRY_DSN, environment="development" if config.is_dev else "production", traces_sample_rate=0.2
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Touch the settings the app cannot serve a single request without, so a
    # missing one is a failed boot rather than a 500 on whichever endpoint
    # happens to need it first. The rest stay lazy: an instance with no R2 or
    # Google Wallet credentials should still start and serve everything else.
    config.DATABASE_URL
    config.CLERK_GUARD
    logger.info("Startup complete")

    yield

    # get_engine is lru_cached; calling it here when nothing ever built an
    # engine would create one purely to throw it away.
    if get_engine.cache_info().currsize:
        get_engine().dispose()
        logger.info("Database engine disposed")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


register_exception_handlers(app)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


app.include_router(health.router)
app.include_router(members.router)
app.include_router(events.router)
app.include_router(points.router)
app.include_router(semesters.router)
app.include_router(attendance.router)
app.include_router(emails.router)
app.include_router(departments.router)
app.include_router(action.router)
app.include_router(custom.router)
app.include_router(forms.router)
app.include_router(submissions.router)
app.include_router(submissions_manual.router)
app.include_router(upload.router)
app.include_router(cache.router)
app.include_router(wallet.router)
