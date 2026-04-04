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
import httpx


router = APIRouter()


@router.post("/blasts/{event_id:int}", status_code=status.HTTP_200_OK, response_model=AcceptanceBlastResponse)
async def send_acceptance_blasts(
    event_id: int,
    request: Request,
    subject: str = Query(..., description="Email subject line"),
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    with LogFile("send acceptance blasts"), SessionLocal() as session:
        try:
            write_log_title(f"Sending acceptance blasts for event [{event_id}]")

            html_body = await request.body()
            html_content = html_body.decode("utf-8")

            if not html_content or not html_content.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must contain HTML content"
                )

            write_log(f"Received HTML body with {len(html_content)} characters")

            submissions = submissions_queries.get_accepted_not_invited_by_event(session, event_id)
            submissions_count = len(submissions)
            write_log(f"Found [{submissions_count}] accepted-not-invited submissions")

            if submissions_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No accepted submissions that haven't been invited found for this event",
                )

            emails = [sub.email for sub in submissions if sub.email]
            if not emails:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="No valid emails found for accepted submissions"
                )

            write_log(f"Extracted [{len(emails)}] emails from submissions")

            acceptance_api_url = config.CERTIFICATE_API_URL
            write_log(f"Sending request to acceptance API: [{acceptance_api_url}/blasts]")
            write_log_json({"subject": subject, "email_count": len(emails), "emails": emails})

            async with httpx.AsyncClient(timeout=60.0) as client:
                try:
                    emails_param = ",".join(emails)
                    response = await client.post(
                        f"{acceptance_api_url}/blasts",
                        params={"emails": emails_param, "subject": subject},
                        content=html_content,
                        headers={"Content-Type": "text/html; charset=utf-8"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log("Acceptance API responded successfully")
                    write_log_json(response_data)
                except httpx.TimeoutException:
                    write_log_exception(Exception("Acceptance API request timed out"))
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Acceptance API request timed out"
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(Exception(f"Acceptance API error: {e.response.status_code}"))
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Acceptance API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(Exception(f"Failed to connect to acceptance API: {str(e)}"))
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to acceptance API"
                    )

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
    subject: str = Query(..., description="Email subject line"),
    emails: list[str] = Query(..., description="Email addresses to send to"),
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    with LogFile("send acceptance test"):
        try:
            write_log_title("Sending acceptance test emails")

            html_body = await request.body()
            html_content = html_body.decode("utf-8")

            if not html_content or not html_content.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must contain HTML content"
                )

            write_log(f"Received HTML body with {len(html_content)} characters")

            if not emails:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid email addresses provided")

            write_log(f"Parsed [{len(emails)}] test emails")
            write_log_json({"emails": emails})

            acceptance_api_url = config.CERTIFICATE_API_URL
            write_log(f"Sending request to acceptance API: [{acceptance_api_url}/blasts]")

            async with httpx.AsyncClient(timeout=60.0) as client:
                try:
                    emails_param = ",".join(emails)
                    response = await client.post(
                        f"{acceptance_api_url}/blasts",
                        params={"emails": emails_param, "subject": subject},
                        content=html_content,
                        headers={"Content-Type": "text/html; charset=utf-8"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log("Acceptance API responded successfully")
                    write_log_json(response_data)
                except httpx.TimeoutException:
                    write_log_exception(Exception("Acceptance API request timed out"))
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Acceptance API request timed out"
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(Exception(f"Acceptance API error: {e.response.status_code}"))
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Acceptance API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(Exception(f"Failed to connect to acceptance API: {str(e)}"))
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to acceptance API"
                    )

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
