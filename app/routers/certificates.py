from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.DB import events as events_queries, logs as log_queries
from app.DB.main import SessionLocal
from app.routers.models import CertificateRequest, SimplifiedMember, CertificateJobResponse, ManualCertificateRequest
from app.config import config
from app.routers.logging import (
    LogFile,
    write_log,
    write_log_json,
    write_log_exception,
    write_log_traceback,
    write_log_title,
)
from app.helpers import admin_guard, get_effective_date
import httpx
import json
from typing import Annotated

router = APIRouter()


@router.post("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=CertificateJobResponse)
async def send_certificates(event_id: int, credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]):
    with LogFile("send certificates"), SessionLocal() as session:
        try:
            write_log_title(f"Sending certificates for event [{event_id}]")

            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                excep = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
                write_log_exception(excep)
                raise excep
            write_log(f"Found event: [{event.name}]")

            attendance = log_queries.get_event_attendance(session, event_id, "exclusive_all")
            attendance_count = len(attendance)
            write_log(f"Found [{attendance_count}] attendees who attended all days for event [{event.name}]")

            if attendance_count == 0:
                write_log_exception(
                    HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="No attendees who completed all days found for this event",
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No attendees who completed all days found for this event",
                )

            start_effective = get_effective_date(event.start_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
            end_effective = get_effective_date(event.end_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
            days = (end_effective - start_effective).days
            if days == 0:
                date_str = start_effective.strftime("%Y-%m-%d")
            else:
                date_str = f"{start_effective.strftime('%Y-%m-%d')} - {end_effective.strftime('%Y-%m-%d')}"
            write_log(f"Event date formatted as: [{date_str}]")

            simplified_members = [
                SimplifiedMember(
                    name=attendee.Members.name, email=attendee.Members.email, gender=attendee.Members.gender
                )
                for attendee in attendance
            ]
            write_log(f"Transformed [{len(simplified_members)}] members to simplified format")

            cert_request = CertificateRequest(
                event_name=event.name,
                announced_name=event.name,
                date=date_str,
                official=bool(event.is_official),
                members=simplified_members,
            )
            write_log_json(
                json.dumps(
                    {
                        "event_name": event.name,
                        "date": date_str,
                        "official": bool(event.is_official),
                        "member_count": len(simplified_members),
                    },
                    indent=4,
                    ensure_ascii=False,
                )
            )

            certificate_api_url = config.CERTIFICATE_API_URL
            write_log(f"Sending request to certificate API: [{certificate_api_url}/certificates]")

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        f"{certificate_api_url}/certificates",
                        json=cert_request.model_dump(mode="json"),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log(f"Certificate API responded with job_id: [{response_data.get('job_id')}]")
                    write_log_json(response_data)
                    return CertificateJobResponse(**response_data)
                except httpx.TimeoutException:
                    write_log_exception(Exception("Certificate API request timed out after 30 seconds"))
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Certificate API request timed out"
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(
                        Exception(f"Certificate API returned error status {e.response.status_code}: {e.response.text}")
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Certificate API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(Exception(f"Failed to connect to certificate API: {str(e)}"))
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to certificate API"
                    )
        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while sending certificates"
            )


@router.post("/manual/{event_id:int}", status_code=status.HTTP_200_OK, response_model=CertificateJobResponse)
async def send_manual_certificates(
    event_id: int,
    request: ManualCertificateRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("send manual certificates"), SessionLocal() as session:
        try:
            write_log_title(f"Sending manual certificates for event [{event_id}]")

            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                excep = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
                write_log_exception(excep)
                raise excep
            write_log(f"Found event: [{event.name}]")

            members_count = len(request.members)
            write_log(f"Received [{members_count}] manual members for certificate generation")

            start_effective = get_effective_date(event.start_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
            end_effective = get_effective_date(event.end_datetime, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
            days = (end_effective - start_effective).days
            if days == 0:
                date_str = start_effective.strftime("%Y-%m-%d")
            else:
                date_str = f"{start_effective.strftime('%Y-%m-%d')} - {end_effective.strftime('%Y-%m-%d')}"
            write_log(f"Event date formatted as: [{date_str}]")

            cert_request = CertificateRequest(
                event_name=event.name,
                announced_name=event.name,
                date=date_str,
                official=bool(event.is_official),
                members=request.members,
            )
            write_log_json(
                json.dumps(
                    {
                        "event_name": event.name,
                        "date": date_str,
                        "official": bool(event.is_official),
                        "member_count": members_count,
                    },
                    indent=4,
                    ensure_ascii=False,
                )
            )

            certificate_api_url = config.CERTIFICATE_API_URL
            write_log(f"Sending request to certificate API: [{certificate_api_url}/certificates]")

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        f"{certificate_api_url}/certificates",
                        json=cert_request.model_dump(mode="json"),
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    response_data = response.json()
                    write_log(f"Certificate API responded with job_id: [{response_data.get('job_id')}]")
                    write_log_json(response_data)
                    return CertificateJobResponse(**response_data)
                except httpx.TimeoutException:
                    write_log_exception(Exception("Certificate API request timed out after 30 seconds"))
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Certificate API request timed out"
                    )
                except httpx.HTTPStatusError as e:
                    write_log_exception(
                        Exception(f"Certificate API returned error status {e.response.status_code}: {e.response.text}")
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Certificate API returned error: {e.response.status_code}",
                    )
                except httpx.RequestError as e:
                    write_log_exception(Exception(f"Failed to connect to certificate API: {str(e)}"))
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to connect to certificate API"
                    )
        except HTTPException:
            raise
        except Exception as e:
            write_log_exception(e)
            write_log_traceback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while sending certificates"
            )
