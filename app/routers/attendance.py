from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from typing import Literal, Annotated
from sqlalchemy.orm import Session
from app.DB import (
    events as events_queries,
    forms as form_queries,
    submissions as submission_queries,
    logs as log_queries,
    members as member_queries,
)
from app.DB.main import SessionLocal
from app.DB.schema import Events, Logs
from app.routers.models import (
    NotFoundResponse,
    BadRequestResponse,
    InternalServerErrorResponse,
    EventAttendanceResponse,
    ManualAttendanceRequest,
    BackfillAttendanceRequest,
    BackfillAttendanceResponse,
)
from app.config import config
from app.exceptions import MemberNotFound
from app.routers.logging import LogFile, write_log_exception, write_log, write_log_title, write_log_traceback
from app.helpers import (
    validate_attendance_token,
    credentials_to_member_model,
    is_admin,
    get_effective_date,
    admin_guard,
    authenticated_guard,
    optional_clerk_guard,
)
from datetime import datetime, timedelta

router = APIRouter()


def get_event_with_attendable_log(session: Session, event_id: int) -> tuple[Events, Logs]:
    event = events_queries.get_event_by_id(session, event_id)
    event_log = log_queries.get_attendable_logs(session, event_id)
    if not event_log:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Event has no attendable logs")
    return event, event_log


def is_member_marked_for_day(session: Session, member_id: int, event_log_id: int, target_date: datetime) -> bool:
    member_logs = log_queries.get_member_logs(session, member_id, event_log_id)
    if not member_logs:
        return False
    target_effective = get_effective_date(target_date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
    for member_log in member_logs:
        log_effective = get_effective_date(member_log.date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
        if log_effective == target_effective:
            return True
    return False


def get_event_days(event: Events) -> int:
    return (event.end_datetime - event.start_datetime).days + 1


def get_target_date_for_day(event: Events, day: int | None) -> datetime:
    if day is None:
        return datetime.now()
    return event.start_datetime + timedelta(days=day - 1)


@router.post("/{event_id:int}", status_code=status.HTTP_200_OK)
def mark_attendance(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)],
    token: Annotated[str | None, Query(description="Optional attendance token for QR code links")] = None,
):
    with LogFile("mark attendance"), SessionLocal() as session:
        try:
            # Validate attendance token
            if token:
                write_log(f"validating attendance token for event [{event_id}]")
                try:
                    validate_attendance_token(token, event_id)
                    write_log(f"Token validated successfully for event [{event_id}]")
                except HTTPException as e:
                    write_log_exception(Exception(f"Token validation failed reason: '{e.detail}'"))
                    raise
            else:
                write_log("HTTP 400:No attendance token provided")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No attendance token provided!")

            member = member_queries.get_member_by_uni_id(session, credentials_to_member_model(credentials).uni_id)
            write_log_title(f"Marking attendance for member [{member.name}] with uni_id [{member.uni_id}]")

            event, event_log = get_event_with_attendable_log(session, event_id)
            write_log(f"Attendace for event [{event.name}] for member [{member.name}]")
            write_log(f"Attendable log found for event [{event_log.id}]")

            effective_now = get_effective_date(datetime.now(), config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
            event_start = event.start_datetime.date()
            event_end = event.end_datetime.date()
            if effective_now < event_start or effective_now > event_end:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="لا يمكنك تسجيل الحضور خارج فترة الحدث"
                )

            if is_member_marked_for_day(session, member.id, event_log.id, datetime.now()):
                write_log(f"Member [{member.id}] has already marked attendance for today")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="!انت سجلت حضورك لهذا الحدث اليوم")

            # 3. get form registration type
            form = form_queries.get_form_by_event_id(session, event_id)
            if not form:
                write_log_exception(Exception(f"HTTP 500: Form for event [{event_id}] not found!!"))
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            write_log(f"Form found: [{form.id}] of type [{form.form_type}]")
            form_type = form.form_type

            if form_type == "none":
                write_log(
                    f"Form type is none (Doesn't require registeration), creating member log for member [{member.id}] and log [{event_log.id}]"
                )
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(f"Member marked attendance for event [{event.name}]")

            if form_type == "google" or form_type == "registration":
                write_log(
                    f"Form type is google or registration, checking submissions for member [{member.id}] and form [{form.id}]"
                )
                submissions = submission_queries.get_submission_by_form_and_member(session, form.id, member.id)
                if not submissions:
                    write_log_exception(f"HTTP 400: Member [{member.id}] has not submitted the form [{form.id}]")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ما عبيت فورم الحدث")
                if submissions.is_accepted == 0:
                    write_log_exception(
                        f"HTTP 400: Member [{member.id}] has not been accepted to the event [{event.name}]"
                    )
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ما انقبلت في الحدث")

                write_log(
                    f"Member [{member.id}] has submitted the form and been accepted to the event [{event.name}], marking attendance..."
                )
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(
                    f"Member marked attendance for today the [{datetime.now().date().day}]th for event [{event.name}]"
                )

            session.commit()
            return

        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{event_id}/backfill", status_code=status.HTTP_200_OK, response_model=BackfillAttendanceResponse)
def backfill_attendance(
    event_id: int,
    request: BackfillAttendanceRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):

    with LogFile("backfill attendance"), SessionLocal() as session:
        try:
            write_log_title(
                f"Backfill attendance for event [{event_id}], {len(request.members)} members, day [{request.day}]"
            )

            event, event_log = get_event_with_attendable_log(session, event_id)

            event_days = get_event_days(event)
            if request.day < 1 or request.day > event_days:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Day {request.day} is out of range. Event has {event_days} day(s).",
                )

            target_date = get_target_date_for_day(event, request.day)

            created_count = 0
            existing_count = 0
            already_attended_count = 0

            for member_data in request.members:
                try:
                    member = member_queries.get_member_by_uni_id(session, member_data.uni_id)
                    existing_count += 1
                    write_log(f"Found existing member [{member.name}] with uni_id [{member.uni_id}]")
                except MemberNotFound:
                    new_member = member_queries.create_member(session, member_data)
                    if new_member is None:
                        write_log_exception(f"Failed to create member with uni_id [{member_data.uni_id}]")
                        continue
                    created_count += 1
                    member = new_member
                    write_log(f"Created new member [{member.name}] with uni_id [{member.uni_id}]")

                if is_member_marked_for_day(session, member.id, event_log.id, target_date):
                    already_attended_count += 1
                    write_log(f"Member [{member.name}] already has attendance for day [{request.day}], skipping")
                    continue

                log_queries.create_member_log(session, member.id, event_log.id, target_date)
                write_log(f"Backfilled attendance for member [{member.name}] on day [{request.day}]")

            session.commit()
            marked_count = (created_count + existing_count) - already_attended_count
            return BackfillAttendanceResponse(
                created_count=created_count,
                existing_count=existing_count,
                already_attended_count=already_attended_count,
                marked_count=marked_count,
                attendance_date=target_date,
            )

        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=EventAttendanceResponse)
def get_event_attendance(
    event_id: int,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_clerk_guard)] = None,
    type: Annotated[
        Literal["count", "detailed", "me"],
        Query(description="Type of attendance data: 'count' (public), 'detailed' (admin), 'me' (authenticated user)"),
    ] = "count",
    day: Annotated[
        int | Literal["all", "exclusive_all"],
        Query(
            description="Filter by event day: 'all' (all days), 'exclusive_all' (only those atteded all days), int (specific day number 1-based index)"
        ),
    ] = "all",
):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)

        event_days = get_event_days(event)
        if isinstance(day, int) and (day < 1 or day > event_days):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Day {day} is out of range. Event has {event_days} day(s).",
            )

        if type == "count":
            attendance = log_queries.get_event_attendance(session, event_id, day)
            return EventAttendanceResponse(attendance_count=len(attendance), attendance=None)

        if type == "detailed":
            if not credentials or not is_admin(credentials):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required for detailed attendance"
                )
            attendance = log_queries.get_event_attendance(session, event_id, day)
            return EventAttendanceResponse(attendance_count=len(attendance), attendance=attendance)

        if type == "me":
            if not credentials:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
            uni_id = credentials_to_member_model(credentials).uni_id
            member = member_queries.get_member_by_uni_id(session, uni_id)
            attendance = log_queries.get_event_attendance(session, event_id, day)
            member_attendance = [a for a in attendance if a.Members.uni_id == uni_id]
            return EventAttendanceResponse(attendance_count=len(member_attendance), attendance=member_attendance)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid type '{type}'. Must be 'count', 'detailed', or 'me'.",
        )


@router.post("/{event_id}/manual", status_code=status.HTTP_200_OK)
def mark_attendance_manual(
    event_id: int,
    request: ManualAttendanceRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("mark attendance manual"), SessionLocal() as session:
        try:
            write_log_title(f"Manual attendance for event [{event_id}], members {request.member_ids}")

            event, event_log = get_event_with_attendable_log(session, event_id)

            if event.status == "closed":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot mark attendance for a closed event"
                )

            event_days = get_event_days(event)
            days_to_mark = request.days if request.days else ([request.day] if request.day else [])

            success_count = 0
            failed_count = 0

            for member_id in request.member_ids:
                try:
                    member = member_queries.get_member_by_id(session, member_id)
                except MemberNotFound:
                    failed_count += 1
                    continue

                member_success = False
                for day in days_to_mark:
                    if day is not None and (day < 1 or day > event_days):
                        continue

                    target_date = get_target_date_for_day(event, day)

                    if is_member_marked_for_day(session, member.id, event_log.id, target_date):
                        continue

                    log_queries.create_member_log(session, member.id, event_log.id, target_date)
                    member_success = True
                    write_log(f"Manual attendance marked for member [{member.name}] on day [{day or 'today'}]")

                if member_success:
                    success_count += 1
                else:
                    failed_count += 1

            session.commit()
            return {"success": success_count, "failed": failed_count}

        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete("/{event_id}/manual", status_code=status.HTTP_200_OK)
def remove_attendance_manual(
    event_id: int,
    request: ManualAttendanceRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)],
):
    with LogFile("remove attendance manual"), SessionLocal() as session:
        try:
            write_log_title(f"Remove attendance for event [{event_id}], members {request.member_ids}")

            event, event_log = get_event_with_attendable_log(session, event_id)

            event_days = get_event_days(event)

            if request.day is not None and (request.day < 1 or request.day > event_days):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Day {request.day} is out of range. Event has {event_days} day(s).",
                )

            target_date = get_target_date_for_day(event, request.day)

            success_count = 0
            failed_count = 0

            for member_id in request.member_ids:
                try:
                    member = member_queries.get_member_by_id(session, member_id)
                except MemberNotFound:
                    failed_count += 1
                    continue

                deleted = log_queries.delete_member_log(session, member.id, event_log.id, target_date)
                if deleted:
                    success_count += 1
                    write_log(f"Attendance removed for member [{member.name}] on day [{request.day or 'today'}]")
                else:
                    failed_count += 1

            session.commit()
            return {"success": success_count, "failed": failed_count}

        except Exception as e:
            session.rollback()
            write_log_exception(e)
            write_log_traceback()
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
