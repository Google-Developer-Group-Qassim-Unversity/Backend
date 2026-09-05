import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.DB import (
    events as events_queries,
    forms as form_queries,
    submissions as submission_queries,
    logs as log_queries,
)

from app.routers.models import (
    Events_model,
    ConflictResponse,
    NotFoundResponse,
    Form_model,
    Open_Events_model,
    Get_Submission_model,
    createEvent_model,
    Member_model,
    MemberEvents_model,
    EventWithAttendance_model,
    EventDetailsModel,
    InternalServerErrorResponse,
    UpdateEventModel,
    UpdateEventStatus_model,
    UpdateEventMeetingUrl_model,
)
from app.helpers import CurrentMember, admin_guard
from app.leaderboard_cache import reset_leaderboard_cache
from app.services.google_client import set_form_publish_state
from app.semesters import resolve_semester, semester_date_bounds
from time import perf_counter
from typing import Annotated
from app.exceptions import DataIntegrityError
from app.dependencies import DB
from app.DB.schema import EventsLocationType, EventsStatus, FormType

from app.routers.responses import DetailResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/events", tags=["events"])


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[Events_model])
def get_all_events(session: DB, semester: Annotated[int | str, Query()] = "all"):
    logger.info("Fetching all events")
    start = perf_counter()
    logger.info("Querying events from database")
    if semester == "all":
        events = events_queries.get_events(session)
    else:
        try:
            semester_id = int(semester)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Semester '{semester}' not found")
        resolved = resolve_semester(session, semester_id)
        start_date, end_date = semester_date_bounds(resolved)
        events = events_queries.get_events_by_semester(session, start_date, end_date)
    end = perf_counter()
    logger.info(
        f"fetched [{len(events)}] events DB took [{(end - start) * 1000:.2f}]ms to execute (semester={semester})"
    )
    return events


@router.get("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=Events_model)
def get_event_by_id(event_id: int, session: DB):
    event = events_queries.get_event_by_id(session, event_id)
    session.flush()
    return event


@router.get("/{event_id:int}/form", status_code=status.HTTP_200_OK, response_model=Form_model)
def get_event_form(event_id: int, session: DB):
    form = form_queries.get_form_by_event_id(session, event_id)
    return form


@router.get("/open", status_code=status.HTTP_200_OK, response_model=list[Open_Events_model])
def get_registrable_events(session: DB):
    """returns events + their associated form"""
    start = perf_counter()
    logger.info("Querying open events from database")
    open_events = events_queries.get_open_events(session)
    end = perf_counter()
    logger.info(f"fetched [{len(open_events)}] open events DB took [{(end - start) * 1000:.2f}]ms to execute")
    return open_events


@router.get("/me", status_code=status.HTTP_200_OK, response_model=MemberEvents_model)
def get_my_events(member: CurrentMember, session: DB):
    attended_raw, participated_raw = events_queries.get_member_events(session, member.id)
    return MemberEvents_model(
        attended=[EventWithAttendance_model(**e) for e in attended_raw],
        participated=[Events_model(**e) for e in participated_raw],
    )


@router.get(
    "/{event_id:int}/details",
    status_code=status.HTTP_200_OK,
    response_model=EventDetailsModel,
    dependencies=[Depends(admin_guard)],
)
def get_event_details(event_id: int, session: DB):
    """return an event + its associated actions, this is needed by the frontend to populate the update event form with the current event data and associated actions"""
    event = events_queries.get_event_by_id(session, event_id)
    actions = events_queries.get_actions_by_event_id(session, event_id)
    if not event or not actions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return {"event": event, "actions": actions}


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=Events_model,
    responses={409: {"model": ConflictResponse, "description": "Event already exists"}},
    dependencies=[Depends(admin_guard)],
)
def create_event(event_data: createEvent_model, session: DB):
    try:
        logger.info("Creating New Event and Associated Form")
        # 1. create event
        new_event = events_queries.create_event(session, event_data.event)
        logger.info(f"Created Event [{new_event.id}]: {new_event.name}")

        # 2. create associated form
        new_form = form_queries.create_form(
            session, Form_model(event_id=new_event.id, form_type=FormType(event_data.form_type))
        )
        logger.info(f"Created Form [{new_form.id}] for Event [{new_event.id}]")

        # 3. create logs for event
        department_log = log_queries.create_log(session, new_event.id, event_data.department_action_id)
        # the member-type Logs row is looked up later by (event_id, action_id) via
        # get_attendable_logs, not through this reference, so it is create-only here.
        log_queries.create_log(session, new_event.id, event_data.member_action_id)

        # 4. give department points for each day
        days = (event_data.event.end_datetime - event_data.event.start_datetime).days + 1
        for day in range(days):
            logger.info(f"Giving department {event_data.department_id} points for day [{day + 1}]/[{days}]")
            log_queries.create_department_log(session, event_data.department_id, department_log.id)

        logger.info(
            f"Created logs for event department: [{event_data.department_action_id}] and member: [{event_data.member_action_id}]"
        )
        session.commit()
        session.refresh(new_event)

        # Best-effort: reset the leaderboard app's data cache so the new event is visible immediately.
        try:
            reset_leaderboard_cache()
            logger.info("Leaderboard cache reset triggered after event creation")
        except Exception as cache_err:
            logger.error(cache_err)

        return new_event
    finally:
        logger.debug("request body: %s", event_data.model_dump(mode="json"))


@router.put(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=Events_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Event not found"},
        409: {"model": ConflictResponse, "description": "Event already exists"},
        500: {"model": InternalServerErrorResponse, "description": "Internal server error"},
    },
    dependencies=[Depends(admin_guard)],
)
def update_event(event_id: int, event_data: UpdateEventModel, session: DB):
    try:
        logger.info(f"Updating Event [{event_id}]")

        # 1. Validate event exists and update event fields
        if event_data.event.end_datetime < event_data.event.start_datetime:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="End datetime must be after start datetime. For overnight events, the end date should be the next day.",
            )
        updated_event = events_queries.update_event(session, event_id, event_data.event)
        assert updated_event != -1, f"Event with id [{event_id}] hit an IntegrityError while updating."

        if updated_event is None:
            logger.error(f"HTTP 404: Event [{event_id}] not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        logger.info(f"Updated Event [{event_id}]: {updated_event.name}")

        # ignore this step if event is custom, we only need to do this for "Full Events"
        if updated_event.location_type != "none":
            # 2. Get all logs for this event
            logs = log_queries.get_logs_by_event_id(session, event_id)
            if not logs or len(logs) < 2:
                logger.error(f"HTTP 500: Event [{event_id}] does not have expected logs")
                raise DataIntegrityError("Event logs not found")
            logger.info(f"Found [{len(logs)}] logs for event [{event_id}]")

            # 3. Identify department log and member log
            # Department log has DepartmentsLogs entries, member log doesn't
            department_log = None
            member_log = None
            # TODO: This WRONG logs that don't havea department_id should not be assumed to be member logs
            for log_entry in logs:
                current_dept_id = log_queries.get_department_id_from_log(session, log_entry.id)
                if current_dept_id is not None:
                    department_log = log_entry
                else:
                    member_log = log_entry

            if not department_log or not member_log:
                logger.error(f"HTTP 500: Could not identify department and member logs for event [{event_id}]")
                raise DataIntegrityError("Could not identify logs")

            # 4. Actions list: first = department action, second = member action
            department_action = event_data.actions[0]
            member_action = event_data.actions[1]
            logger.info(
                f"Department action: [{department_action.action_id}], Member action: [{member_action.action_id}]"
            )

            # 5. Update department log action_id
            log_queries.update_log_action_id(session, department_log.id, department_action.action_id)
            logger.info(f"Updated department log [{department_log.id}] action_id to [{department_action.action_id}]")

            # 6. Update member log action_id
            log_queries.update_log_action_id(session, member_log.id, member_action.action_id)
            logger.info(f"Updated member log [{member_log.id}] action_id to [{member_action.action_id}]")

            # 7. Handle department_id and/or days change
            current_dept_id = log_queries.get_department_id_from_log(session, department_log.id)
            new_dept_id = department_action.department_id
            current_dept_logs_count = log_queries.get_department_logs_count(session, department_log.id)

            # there is a bug here, where if the start-end difference corsses the 12AM lines.
            # e.g start 2026-01-01 10:00 PM, end 2026-01-02 2:00 AM => this will be counted as 2 days instead of 1 day
            # UPDATE: there actually is NO bug, the timedelta here calculates the difference in days by looking at the date part of the datetime, so in the above example it will correctly calculate it as 1 day, not 2 days, because the date part of both datetimes is different, even though the time difference is only 4 hours. So this works correctly for overnight events as well.
            new_days = (updated_event.end_datetime - updated_event.start_datetime).days + 1

            logger.info(
                f"Current: dept_id=[{current_dept_id}], days=[{current_dept_logs_count}]. New: dept_id=[{new_dept_id}], days=[{new_days}]"
            )

            # if Department changed - delete all and recreate
            if new_dept_id is not None and current_dept_id != new_dept_id:
                logger.info(f"Department changed from [{current_dept_id}] to [{new_dept_id}]")

                deleted_count = log_queries.delete_department_logs_by_log_id(session, department_log.id)
                logger.info(f"Deleted [{deleted_count}] old department logs for log [{department_log.id}]")

                for day in range(new_days):
                    log_queries.create_department_log(session, new_dept_id, department_log.id)
                logger.info(f"Created [{new_days}] department logs for new department [{new_dept_id}]")

            # if Same department but days changed
            elif current_dept_logs_count != new_days:
                dept_id_to_use = new_dept_id if new_dept_id is not None else current_dept_id
                if dept_id_to_use is None:
                    raise DataIntegrityError(
                        f"Log [{department_log.id}] has no department id and none was provided in the update"
                    )

                # if days increased - add more department logs
                if new_days > current_dept_logs_count:
                    days_to_add = new_days - current_dept_logs_count
                    logger.info(
                        f"Days increased from [{current_dept_logs_count}] to [{new_days}], adding [{days_to_add}] department logs"
                    )
                    for _ in range(days_to_add):
                        log_queries.create_department_log(session, dept_id_to_use, department_log.id)
                # if Days decreased - remove some department logs
                else:
                    days_to_remove = current_dept_logs_count - new_days
                    logger.info(
                        f"Days decreased from [{current_dept_logs_count}] to [{new_days}], removing [{days_to_remove}] department logs"
                    )
                    log_queries.delete_n_department_logs(session, department_log.id, days_to_remove)

        session.commit()
        session.refresh(updated_event)
        logger.info(f"Event [{event_id}] updated successfully")
        return updated_event

    finally:
        logger.debug("request body: %s", event_data.model_dump(mode="json"))


@router.delete(
    "/{event_id:int}",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": NotFoundResponse, "description": "Event not found"},
        400: {"description": "Only draft events can be deleted"},
    },
    dependencies=[Depends(admin_guard)],
    response_model=DetailResponse,
)
def delete_event(event_id: int, session: DB):
    logger.info(f"Deleting Event [{event_id}]")

    event = events_queries.get_event_by_id(session, event_id)
    if not event:
        logger.error(f"HTTP 404: Event [{event_id}] not found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.status != "draft":
        logger.error(f"HTTP 400: Cannot delete event [{event_id}] with status [{event.status}]")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only draft events can be deleted")

    event_name = event.name
    events_queries.delete_event(session, event_id)
    session.commit()
    logger.info(f"Event [{event_id}]: {event_name} deleted successfully")
    return {"detail": "Event deleted successfully"}


@router.put(
    "/{event_id:int}/status",
    status_code=status.HTTP_200_OK,
    response_model=Events_model,
    responses={404: {"model": NotFoundResponse, "description": "Event not found"}},
    dependencies=[Depends(admin_guard)],
)
def update_event_status(event_id: int, status_data: UpdateEventStatus_model, session: DB):
    event = events_queries.get_event_by_id(session, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    old_status = event.status

    # Publishing/unpublishing the event also publishes/unpublishes its Google
    # Form, if it has one - a copied form does not inherit the template's
    # accepting-responses state, so without this members hit an "unpublished
    # form" wall the admin has no way to see coming. Done before the DB write
    # so a Google API failure raises instead of leaving the event "open" with
    # a form that still silently rejects submissions.
    entering_open = status_data.status == "open" and old_status != EventsStatus.OPEN
    leaving_open = old_status == EventsStatus.OPEN and status_data.status != "open"
    if entering_open or leaving_open:
        form = form_queries.get_form_by_event_id(session, event_id)
        if form.form_type == FormType.GOOGLE and form.google_form_id:
            set_form_publish_state(form.google_form_id, is_published=entering_open)

    event.status = EventsStatus(status_data.status)
    session.commit()
    session.refresh(event)

    # Best-effort: reset the leaderboard cache when entering or leaving the "open" status,
    # since the leaderboard's open-events view is gated on status = 'open'.
    if status_data.status == "open" or old_status == "open":
        try:
            reset_leaderboard_cache()
        except Exception:
            pass

    return event


@router.put(
    "/{event_id:int}/meeting-url",
    status_code=status.HTTP_200_OK,
    response_model=Events_model,
    responses={404: {"model": NotFoundResponse, "description": "Event not found"}},
    dependencies=[Depends(admin_guard)],
)
def update_event_meeting_url(event_id: int, meeting_url_data: UpdateEventMeetingUrl_model, session: DB):
    """Set or clear the join link shown to members on a remote event."""
    existing_event = events_queries.get_event_by_id(session, event_id)
    if meeting_url_data.meeting_url and existing_event.location_type != EventsLocationType.ONLINE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="meeting_url can only be set on an online event"
        )
    event = events_queries.update_event_meeting_url(session, event_id, meeting_url_data.meeting_url)
    session.commit()
    session.refresh(event)
    logger.info(f"Event [{event_id}] meeting url {'set' if meeting_url_data.meeting_url else 'cleared'}")

    # Best-effort: the leaderboard app caches event data, so the link would otherwise
    # not appear until the next revalidation.
    try:
        reset_leaderboard_cache()
    except Exception as cache_err:
        logger.error(cache_err)

    return event


# TODO: move to submissions router.
@router.get(
    "/submissions/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=list[Get_Submission_model],
    dependencies=[Depends(admin_guard)],
)
def get_submissions_by_event(event_id: int, session: DB):
    try:
        submissions_data = submission_queries.get_submissions_by_event_id(session, event_id)

        # Transform to Submission_model objects
        submissions = []
        for row in submissions_data:
            member = Member_model(
                id=row.id,
                name=row.name,
                email=row.email,
                phone_number=row.phone_number,
                uni_id=row.uni_id,
                gender=row.gender,
                uni_level=row.uni_level,
                uni_college=row.uni_college,
            )

            submission = Get_Submission_model(
                member=member,
                submission_id=row.submission_id,
                submitted_at=row.submitted_at,
                form_type=row.form_type,
                submission_type=row.submission_type,
                is_accepted=bool(row.is_accepted),
                is_invited=bool(row.is_invited),
                google_submission_value=row.google_submission_value,
                event_id=row.event_id,
                form_id=row.form_id,
                google_form_id=row.google_form_id,
            )
            submissions.append(submission)

        return submissions
    except Exception:
        raise
