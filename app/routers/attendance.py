from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from typing import Literal
from app.DB import (
    events as events_queries,
    forms as form_queries,
    submissions as submission_queries,
    logs as log_queries,
    members as member_queries,
)
from app.DB.main import SessionLocal
from app.routers.models import (
    NotFoundResponse,
    BadRequestResponse,
    InternalServerErrorResponse,
    EventAttendanceResponse,
    ManualAttendanceRequest,
)
from app.config import config
from app.routers.logging import (
    create_log_file,
    write_log_exception,
    write_log,
    write_log_title,
    write_log_traceback,
)
from app.helpers import (
    validate_attendance_token,
    credentials_to_member_model,
    is_admin,
    get_effective_date,
)
from datetime import datetime, timedelta


router = APIRouter()


@router.post(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": NotFoundResponse, "description": "Event not found"},
        400: {"model": BadRequestResponse, "description": "..."},
        500: {
            "model": InternalServerErrorResponse,
            "description": "Internal server error",
        },
    },
)
def mark_attendance(
    event_id: int,
    token: str = Query(None, description="Optional attendance token for QR code links"),
    credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD),
):
    log_file = create_log_file("mark attendance")

    # Validate attendance token
    if token:
        write_log(log_file, f"validating attendance token for event [{event_id}]")
        try:
            validate_attendance_token(token, event_id)
            write_log(log_file, f"Token validated successfully for event [{event_id}]")
        except HTTPException as e:
            write_log_exception(log_file, Exception(f"Token validation failed reason: '{e.detail}'"))
            raise
    else:
        write_log(log_file, "HTTP 400:No attendance token provided")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No attendance token provided!",
        )

    with SessionLocal() as session:
        try:
            member = member_queries.get_member_by_uni_id(session, credentials_to_member_model(credentials).uni_id)
            if not member:
                excep = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
                write_log_exception(log_file, excep)
                raise excep
            write_log_title(
                log_file,
                f"Marking attendance for member [{member.name}] with uni_id [{member.uni_id}]",
            )

            # 1. check if event exists
            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                excep = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
                write_log_exception(log_file, excep)
                raise excep
            write_log(
                log_file,
                f"Attendace for event [{event.name}] for member [{member.name}]",
            )

            event_log = log_queries.get_attendable_logs(session, event_id)
            if not event_log:
                write_log_exception(
                    log_file,
                    Exception(f"HTTP 500: Event [{event_id}] has no attendable logs!!"),
                )
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            write_log(log_file, f"Attendable log found for event [{event_log.id}]")

            # 2. check if already marked attendance for today
            member_logs = log_queries.get_member_logs(session, member.id, event_log.id)
            if member_logs is None:
                write_log(
                    log_file,
                    f"No member logs found for member [{member.id}] and event [{event.name}] (has not marked attendance yet)",
                )
            else:
                write_log(
                    log_file,
                    f"Found [{len(member_logs)}] member logs for member [{member.id}] and event [{event.name}]",
                )
                for member_log in member_logs:
                    write_log(
                        log_file,
                        f"Checking member log id: [{member_log.id}], log_id: [{member_log.log_id}], Date: [{member_log.date}]",
                    )
                    now_effective = get_effective_date(datetime.now(), config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
                    log_effective = get_effective_date(member_log.date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
                    if log_effective == now_effective:
                        write_log(
                            log_file,
                            f"Member [{member.id}] has already marked attendance for today (effective date: {now_effective})",
                        )
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="!انت سجلت حضورك لهذا الحدث اليوم",
                        )
                    else:
                        write_log(
                            log_file,
                            f"Member [{member.id}] attended on effective date [{log_effective}]",
                        )

            # 3. get form registration type
            form = form_queries.get_form_by_event_id(session, event_id)
            if not form:
                write_log_exception(
                    log_file,
                    Exception(f"HTTP 500: Form for event [{event_id}] not found!!"),
                )
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            write_log(log_file, f"Form found: [{form.id}] of type [{form.form_type}]")
            form_type = form.form_type

            if form_type == "none":
                write_log(
                    log_file,
                    f"Form type is none (Doesn't require registeration), creating member log for member [{member.id}] and log [{event_log.id}]",
                )
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(log_file, f"Member marked attendance for event [{event.name}]")

            if form_type == "google" or form_type == "registration":
                write_log(
                    log_file,
                    f"Form type is google or registration, checking submissions for member [{member.id}] and form [{form.id}]",
                )
                submissions = submission_queries.get_submission_by_form_and_member(session, form.id, member.id)
                if not submissions:
                    write_log_exception(
                        log_file,
                        f"HTTP 400: Member [{member.id}] has not submitted the form [{form.id}]",
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="ما عبيت فورم الحدث",
                    )
                if submissions.is_accepted == 0:
                    write_log_exception(
                        log_file,
                        f"HTTP 400: Member [{member.id}] has not been accepted to the event [{event.name}]",
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="ما انقبلت في الحدث",
                    )

                write_log(
                    log_file,
                    f"Member [{member.id}] has submitted the form and been accepted to the event [{event.name}], marking attendance...",
                )
                log_queries.create_member_log(session, member.id, event_log.id)
                write_log(
                    log_file,
                    f"Member marked attendance for today the [{datetime.now().date().day}]th for event [{event.name}]",
                )

            session.commit()
            return

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=EventAttendanceResponse,
)
def get_event_attendance(
    event_id: int,
    type: Literal["count", "detailed", "me"] = Query(
        "count",
        description="Type of attendance data: 'count' (public), 'detailed' (admin), 'me' (authenticated user)",
    ),
    day: int | Literal["all", "exclusive_all"] = Query(
        "all",
        description="Filter by event day: 'all' (all days), 'exclusive_all' (only those atteded all days), int (specific day number 1-based index)",
    ),
    credentials: HTTPAuthorizationCredentials | None = Depends(config.CLERK_GUARD_optional),
):
    with SessionLocal() as session:
        event = events_queries.get_event_by_id(session, event_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

        event_days = (event.end_datetime - event.start_datetime).days + 1
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
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required for detailed attendance",
                )
            attendance = log_queries.get_event_attendance(session, event_id, day)
            return EventAttendanceResponse(attendance_count=len(attendance), attendance=attendance)

        if type == "me":
            if not credentials:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required",
                )
            uni_id = credentials_to_member_model(credentials).uni_id
            member = member_queries.get_member_by_uni_id(session, uni_id)
            if not member:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
            attendance = log_queries.get_event_attendance(session, event_id, day)
            member_attendance = [a for a in attendance if a.Members.uni_id == uni_id]
            return EventAttendanceResponse(attendance_count=len(member_attendance), attendance=member_attendance)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid type '{type}'. Must be 'count', 'detailed', or 'me'.",
        )


@router.post(
    "/{event_id}/manual",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": NotFoundResponse, "description": "Event or member not found"},
        400: {"model": BadRequestResponse, "description": "Already marked or invalid request"},
        403: {"model": BadRequestResponse, "description": "Admin privileges required"},
        500: {"model": InternalServerErrorResponse, "description": "Internal server error"},
    },
)
def mark_attendance_manual(
    event_id: int,
    request: ManualAttendanceRequest,
    credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD),
):
    if not is_admin(credentials):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )

    log_file = create_log_file("mark attendance manual")

    with SessionLocal() as session:
        try:
            write_log_title(
                log_file,
                f"Manual attendance for event [{event_id}], members {request.member_ids}",
            )

            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

            if event.status == "closed":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot mark attendance for a closed event",
                )

            event_log = log_queries.get_attendable_logs(session, event_id)
            if not event_log:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Event has no attendable logs",
                )

            event_days = (event.end_datetime - event.start_datetime).days + 1

            days_to_mark = request.days if request.days else ([request.day] if request.day else [None])

            success_count = 0
            failed_count = 0

            for member_id in request.member_ids:
                member = member_queries.get_member_by_id(session, member_id)
                if not member:
                    failed_count += 1
                    continue

                member_success = False
                for day in days_to_mark:
                    if day is not None:
                        if day < 1 or day > event_days:
                            continue
                        target_date = event.start_datetime + timedelta(days=day - 1)
                    else:
                        target_date = datetime.now()

                    member_logs = log_queries.get_member_logs(session, member.id, event_log.id)
                    if member_logs:
                        target_effective = get_effective_date(target_date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
                        already_marked = False
                        for member_log in member_logs:
                            log_effective = get_effective_date(member_log.date, config.ATTENDANCE_EARLY_HOURS_THRESHOLD)
                            if log_effective == target_effective:
                                already_marked = True
                                break
                        if already_marked:
                            continue

                    log_queries.create_member_log(session, member.id, event_log.id, target_date)
                    member_success = True
                    write_log(log_file, f"Manual attendance marked for member [{member.name}] on day [{day or 'today'}]")

                if member_success:
                    success_count += 1
                else:
                    failed_count += 1

            session.commit()
            return {"success": success_count, "failed": failed_count}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete(
    "/{event_id}/manual",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": NotFoundResponse, "description": "Event, member, or attendance not found"},
        403: {"model": BadRequestResponse, "description": "Admin privileges required"},
        500: {"model": InternalServerErrorResponse, "description": "Internal server error"},
    },
)
def remove_attendance_manual(
    event_id: int,
    request: ManualAttendanceRequest,
    credentials: HTTPAuthorizationCredentials = Depends(config.CLERK_GUARD),
):
    if not is_admin(credentials):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )

    log_file = create_log_file("remove attendance manual")

    with SessionLocal() as session:
        try:
            write_log_title(
                log_file,
                f"Remove attendance for event [{event_id}], members {request.member_ids}",
            )

            event = events_queries.get_event_by_id(session, event_id)
            if not event:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

            event_log = log_queries.get_attendable_logs(session, event_id)
            if not event_log:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Event has no attendable logs",
                )

            event_days = (event.end_datetime - event.start_datetime).days + 1

            if request.day is not None:
                if request.day < 1 or request.day > event_days:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Day {request.day} is out of range. Event has {event_days} day(s).",
                    )
                target_date = event.start_datetime + timedelta(days=request.day - 1)
            else:
                target_date = datetime.now()

            success_count = 0
            failed_count = 0

            for member_id in request.member_ids:
                member = member_queries.get_member_by_id(session, member_id)
                if not member:
                    failed_count += 1
                    continue

                deleted = log_queries.delete_member_log(session, member.id, event_log.id, target_date)
                if deleted:
                    success_count += 1
                    write_log(log_file, f"Attendance removed for member [{member.name}] on day [{request.day or 'today'}]")
                else:
                    failed_count += 1

            session.commit()
            return {"success": success_count, "failed": failed_count}

        except HTTPException:
            session.rollback()
            raise
        except Exception as e:
            session.rollback()
            write_log_exception(log_file, e)
            write_log_traceback(log_file)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
