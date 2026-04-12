from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB.main import SessionLocal
from app.DB import submissions as submissions_queries
from app.routers.models import AcceptanceBlastResponse
from app.config import config
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_json,
    write_log_exception,
    write_log_traceback,
    write_log_title,
)
from app.helpers import admin_guard
from app.exceptions import EmptyBody, GatewayTimeout, BadGateway, ServiceUnavailable
import httpx
from typing import Annotated


router = APIRouter()


async def read_html_body(request: Request) -> str:
    html_content = (await request.body()).decode("utf-8")
    if not html_content or not html_content.strip():
        raise EmptyBody()
    return html_content


async def call_acceptance_api(emails: list[str], subject: str, html_content: str) -> dict:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{config.CERTIFICATE_API_URL}/blasts",
                params={"emails": ",".join(emails), "subject": subject},
                content=html_content,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            raise GatewayTimeout(detail="Acceptance API request timed out")
        except httpx.HTTPStatusError as e:
            raise BadGateway(detail=f"Acceptance API returned error: {e.response.status_code}")
        except httpx.RequestError:
            raise ServiceUnavailable(detail="Failed to connect to acceptance API")


@router.post("/blasts/{event_id:int}", status_code=status.HTTP_200_OK, response_model=AcceptanceBlastResponse)
async def send_acceptance_blasts(
    event_id: int,
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send acceptance blasts"), SessionLocal() as session:
        try:
            write_log_title(f"Sending acceptance blasts for event [{event_id}]")

            html_content = await read_html_body(request)
            write_log(f"Received HTML body with {len(html_content)} characters")

            submissions = submissions_queries.get_accepted_not_invited_by_event(session, event_id)
            emails = [sub.email for sub in submissions if sub.email]
            write_log(f"Found [{len(submissions)}] submissions, [{len(emails)}] emails")

            write_log(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")
            write_log_json({"subject": subject, "email_count": len(emails), "emails": emails})

            response_data = await call_acceptance_api(emails, subject, html_content)
            write_log("Acceptance API responded successfully")
            write_log_json(response_data)

            submission_ids = [sub.submission_id for sub in submissions]
            submissions_queries.mark_submissions_as_invited(session, submission_ids)
            session.commit()
            write_log(f"Marked [{len(submission_ids)}] submissions as invited")

            return AcceptanceBlastResponse(sent_count=len(emails), emails=emails)

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending acceptance emails",
            )


@router.post("/test", status_code=status.HTTP_200_OK, response_model=AcceptanceBlastResponse)
async def send_acceptance_test(
    request: Request,
    subject: Annotated[str, Query(description="Email subject line")],
    emails: Annotated[list[str], Query(description="Email addresses to send to")],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send acceptance test"):
        try:
            write_log_title("Sending acceptance test emails")

            html_content = await read_html_body(request)
            write_log(f"Received HTML body with {len(html_content)} characters")

            write_log(f"Parsed [{len(emails)}] test emails")
            write_log_json({"emails": emails})
            write_log(f"Sending request to acceptance API: [{config.CERTIFICATE_API_URL}/blasts]")

            response_data = await call_acceptance_api(emails, subject, html_content)
            write_log("Acceptance API responded successfully")
            write_log_json(response_data)

            return AcceptanceBlastResponse(sent_count=len(emails), emails=emails)

        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending test acceptance emails",
            )
