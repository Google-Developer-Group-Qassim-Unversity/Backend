import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.config import config
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

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


register_exception_handlers(app)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(points.router, prefix="/points", tags=["Points"])
app.include_router(semesters.router, prefix="/semesters", tags=["Semesters"])
app.include_router(attendance.router, prefix="/attendance", tags=["Attendance"])
app.include_router(emails.router, prefix="/emails", tags=["emails"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(action.router, prefix="/actions", tags=["actions"])
app.include_router(custom.router, prefix="/custom", tags=["custom"])
app.include_router(forms.router, prefix="/forms", tags=["Forms"])
app.include_router(submissions.router, prefix="/submissions", tags=["Submissions"])
app.include_router(submissions_manual.router, prefix="/submissions_manual", tags=["Submissions Manual"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(cache.router, prefix="/cache", tags=["cache"])
app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
