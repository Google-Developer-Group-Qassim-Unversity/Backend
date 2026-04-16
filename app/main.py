from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from starlette.requests import Request
from app.routers import (
    attendance,
    certificates,
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
    acceptance,
    health
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


@app.exception_handler(OperationalError)
def database_operational_error_handler(request: Request, exc: OperationalError):
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable. Please retry shortly."})


@app.exception_handler(SQLAlchemyTimeoutError)
def database_timeout_error_handler(request: Request, exc: SQLAlchemyTimeoutError):
    return JSONResponse(status_code=503, content={"detail": "Database is under heavy load. Please retry shortly."})


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(events.router, prefix="/events", tags=["events"])
app.include_router(points.router, prefix="/points", tags=["Points"])
app.include_router(attendance.router, prefix="/attendance", tags=["Attendance"])
app.include_router(certificates.router, prefix="/certificates", tags=["certificates"])
app.include_router(departments.router, prefix="/departments", tags=["departments"])
app.include_router(action.router, prefix="/actions", tags=["actions"])
app.include_router(custom.router, prefix="/custom", tags=["custom"])
app.include_router(forms.router, prefix="/forms", tags=["Forms"])
app.include_router(submissions.router, prefix="/submissions", tags=["Submissions"])
app.include_router(submissions_manual.router, prefix="/submissions_manual", tags=["Submissions Manual"])
app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(acceptance.router, prefix="/acceptance", tags=["acceptance"])
