from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, logs as log_queries
from ..DB.main import SessionLocal
from app.routers.models import (
    CertificateRequest,
    SimplifiedMember,
    CertificateJobResponse,
    ManualCertificateRequest,
)
from app.config import config
from app.routers.logging import (
    create_log_file,
    write_log,
    write_log_json,
    write_log_exception,
    write_log_traceback,
    write_log_title,
)
from app.helpers import admin_guard
import httpx
import json

router = APIRouter()


@router.post(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=CertificateJobResponse,
)
async def send_certificates(
    event_id: int, credentials: HTTPAuthorizationCredentials = Depends(admin_guard)
):
    log_file = create_log_file("send certificates")
    with SessionLocal() as session:
        try:
            write_log_title(log_file, f"Sending certificates for event [{event_id}]")

            # Fetch event details
            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                excep = HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
                )
                write_log_exception(log_file, excep)
                raise excep
            write_log(log_file, f"Found event: [{event.name}]")

            # Fetch attendance list - only members who attended all days
            attendance = log_queries.get_event_attendance(
                session, event_id, "exclusive_all"
            )
            attendance_count = len(attendance)
            write_log(
                log_file,
                f"Found [{attendance_count}] attendees who attended all days for event [{event.name}]",
            )

            if attendance_count == 0:
                write_log_exception(
                    log_file,
                    HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No attendees who completed all days found for this event",
                    ),
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No attendees who completed all days found for this event",
                )

            # Format date based on event duration
            days = (event.end_datetime.date() - event.start_datetime.date()).days
            if days == 0:
                date_str = event.start_datetime.strftime("%Y-%m-%d")
            else:
                date_str = f"{event.start_datetime.strftime('%Y-%m-%d')} - {event.end_datetime.strftime('%Y-%m-%d')}"
            write_log(log_file, f"Event date formatted as: [{date_str}]")

            # Transform members to simplified format
            # Extract Members object from the new attendance structure
            simplified_members = [
                SimplifiedMember(
                    name=attendee["Members"].name,
                    email=attendee["Members"].email,
                    gender=attendee["Members"].gender,
                )
                for attendee in attendance
            ]
            write_log(
                log_file,
                f"Transformed [{len(simplified_members)}] members to simplified format",
            )

            # Build certificate request payload
            cert_request = CertificateRequest(
                event_name=event.name,
                announced_name=event.name,
                date=date_str,
                official=bool(event.is_official),
                members=simplified_members,
            )
            write_log_json(
                log_file,
                json.dumps(
                    {
                        "event_name": event.name,
                        "date": date_str,
                        "official": bool(event.is_official),
                        "member_count": len(simplified_members),
                    },
                    indent=4,
                    ensure_ascii=False,
                ),
            )

            # Make async POST request to certificate API
            certificate_api_url = config.CERTIFICATE_API_URL
            write_log(
                log_file,
                f"Sending request to certificate API: [{certificate_api_url}/certificates]",
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        f"{certificate_api_url}/certificates",
                        json=cert_request.model_dump(mode="json"),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log(
                        log_file,
                        f"Certificate API responded with job_id: [{response_data.get('job_id')}]",
                    )
                    write_log_json(log_file, response_data)
                    return CertificateJobResponse(**response_data)
                except httpx.TimeoutException:
                    write_log_exception(
                        log_file,
                        Exception("Certificate API request timed out after 30 seconds"),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                        detail="Certificate API request timed out",
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(
                        log_file,
                        Exception(
                            f"Certificate API returned error status {e.response.status_code}: {e.response.text}"
                        ),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Certificate API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(
                        log_file,
                        Exception(f"Failed to connect to certificate API: {str(e)}"),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Failed to connect to certificate API",
                    )
        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending certificates",
            )


@router.post(
    "/manual/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=CertificateJobResponse,
)
async def send_manual_certificates(
    event_id: int,
    request: ManualCertificateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(admin_guard),
):
    log_file = create_log_file("send manual certificates")
    with SessionLocal() as session:
        try:
            write_log_title(
                log_file, f"Sending manual certificates for event [{event_id}]"
            )

            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                excep = HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
                )
                write_log_exception(log_file, excep)
                raise excep
            write_log(log_file, f"Found event: [{event.name}]")

            members_count = len(request.members)
            write_log(
                log_file,
                f"Received [{members_count}] manual members for certificate generation",
            )

            days = (event.end_datetime.date() - event.start_datetime.date()).days
            if days == 0:
                date_str = event.start_datetime.strftime("%Y-%m-%d")
            else:
                date_str = f"{event.start_datetime.strftime('%Y-%m-%d')} - {event.end_datetime.strftime('%Y-%m-%d')}"
            write_log(log_file, f"Event date formatted as: [{date_str}]")

            cert_request = CertificateRequest(
                event_name=event.name,
                announced_name=event.name,
                date=date_str,
                official=bool(event.is_official),
                members=request.members,
            )
            write_log_json(
                log_file,
                json.dumps(
                    {
                        "event_name": event.name,
                        "date": date_str,
                        "official": bool(event.is_official),
                        "member_count": members_count,
                    },
                    indent=4,
                    ensure_ascii=False,
                ),
            )

            certificate_api_url = config.CERTIFICATE_API_URL
            write_log(
                log_file,
                f"Sending request to certificate API: [{certificate_api_url}/certificates]",
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        f"{certificate_api_url}/certificates",
                        json=cert_request.model_dump(mode="json"),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log(
                        log_file,
                        f"Certificate API responded with job_id: [{response_data.get('job_id')}]",
                    )
                    write_log_json(log_file, response_data)
                    return CertificateJobResponse(**response_data)
                except httpx.TimeoutException:
                    write_log_exception(
                        log_file,
                        Exception("Certificate API request timed out after 30 seconds"),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                        detail="Certificate API request timed out",
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(
                        log_file,
                        Exception(
                            f"Certificate API returned error status {e.response.status_code}: {e.response.text}"
                        ),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Certificate API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(
                        log_file,
                        Exception(f"Failed to connect to certificate API: {str(e)}"),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Failed to connect to certificate API",
                    )
        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An error occurred while sending certificates",
            )
