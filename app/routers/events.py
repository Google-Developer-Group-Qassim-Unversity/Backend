import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.DB import (
    events as events_queries,
    forms as form_queries,
    submissions as submission_queries,
    logs as log_queries,
)

from app.routers.models import (
    EventHistoryItem,
    EventPerson_model,
    EventHistoryResponse,
    PaginatedEvents_model,
    Events_model,
    EventsWithContentLinks_model,
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
    EventContentLink_model,
    InternalServerErrorResponse,
    UpdateEventModel,
    UpdateEventStatus_model,
    UpdateEventMeetingUrl_model,
    CreateEventContentLink_model,
    UpdateEventContentLink_model,
    DriveRecordingCandidatesResponse,
    DriveFileMakePublicResponse_model,
)
from app.clients import GDrive
from app.helpers import CurrentMember
from app.leaderboard_cache import reset_leaderboard_cache_best_effort
from app.services.events import create_full_event
from app.services.google_client import set_form_publish_state
from app.semesters import resolve_semester
from time import perf_counter
from typing import Annotated, Literal
from sqlalchemy.exc import IntegrityError
from app.exceptions import DataIntegrityError, DriveIntegrationNotConfigured
from app.dependencies import DB
from sqlalchemy.orm import Session
from app.DB import event_pipeline as pipeline_queries
from app.DB.schema import EventHistoryAction, EventsLocationType, EventsStatus, FormType, Members, PipelineHistoryAction
from app.services import event_history, pipeline_calendar, pipeline_history
from app.services.push import cases as push_cases
from app.services.push import delivery as push_delivery

from app.routers.responses import DetailResponse
from app.services.permissions.catalogue import Perm
from app.services.permissions.dependencies import CurrentAccess
from app.services.permissions.guards import Require
from app.services.permissions.departments import event_departments


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/events", tags=["events"])


def _semester_id(session: Session, semester: int | str) -> str:
    """The row id of the semester a ``?semester=<hijri code>`` filter names."""
    try:
        hijri_code = int(semester)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Semester '{semester}' not found")
    return resolve_semester(session, hijri_code).id


@router.get("/", status_code=status.HTTP_200_OK, response_model=list[EventsWithContentLinks_model])
def get_all_events(session: DB, semester: Annotated[int | str, Query()] = "all"):
    logger.info("Fetching all events")
    start = perf_counter()
    logger.info("Querying events from database")
    if semester == "all":
        events = events_queries.get_events(session)
    else:
        events = events_queries.get_events_by_semester(session, _semester_id(session, semester))
    end = perf_counter()
    logger.info(
        f"fetched [{len(events)}] events DB took [{(end - start) * 1000:.2f}]ms to execute (semester={semester})"
    )
    return events


@router.get(
    "/paginated",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedEvents_model,
    dependencies=[Depends(Require(Perm.EVENTS_VIEW))],
)
def list_events_paginated(
    session: DB,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    semester: Annotated[int | str, Query()] = "all",
    status_filter: Annotated[Literal["draft", "open", "active", "closed"] | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query()] = None,
    exclude_custom: Annotated[bool, Query()] = False,
):
    """One page of events for the admin table - filtered and counted in the
    database. The full-array GET / is left intact for the leaderboard app and
    the dashboard aggregates."""
    semester_id = _semester_id(session, semester) if semester != "all" else None

    offset = (page - 1) * page_size
    total = events_queries.count_events(session, semester_id, status_filter, search)
    items = events_queries.get_events_paginated(session, page_size, offset, semester_id, status_filter, search)
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


@router.get("/{event_id:int}", status_code=status.HTTP_200_OK, response_model=EventsWithContentLinks_model)
def get_event_by_id(event_id: int, session: DB):
    """The event row the leaderboard app and the admin pages read. Published
    content links ride along; drafts are filtered server-side."""
    event = events_queries.get_event_by_id(session, event_id)
    events_queries.attach_published_content_links_of_event(session, event)
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
    "/{event_id:int}/history",
    status_code=status.HTTP_200_OK,
    response_model=EventHistoryResponse,
    dependencies=[Depends(Require(Perm.EVENTS_VIEW))],
)
def get_event_history(event_id: int, session: DB):
    """Who did what to the event from /events, and when, oldest first. It outlives a deleted event."""
    request = pipeline_queries.get_request_for_event(session, event_id)
    return EventHistoryResponse(
        items=[
            EventHistoryItem(
                action=row.action,
                at=row.at,
                actor=EventPerson_model(member_id=row.actor.id, name=row.actor.name) if row.actor else None,
                details=row.details,
            )
            for row in event_history.for_event(session, event_id)
        ],
        pipeline_request_id=request.id if request else None,
    )


@router.get(
    "/{event_id:int}/details",
    status_code=status.HTTP_200_OK,
    response_model=EventDetailsModel,
    dependencies=[Depends(Require(Perm.EVENTS_VIEW))],
)
def get_event_details(event_id: int, session: DB):
    """return an event + its associated actions, this is needed by the frontend to populate the update event form with the current event data and associated actions"""
    event = events_queries.get_event_by_id(session, event_id)
    actions = events_queries.get_actions_by_event_id(session, event_id)
    if not event or not actions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    # Admin view: every content link (published or not), as its own list -
    # never attached onto the event object, so only a code path that asks for
    # the whole set can ever return it (a serializer reading the event row
    # sees published rows only, or none).
    return {
        "event": event,
        "actions": actions,
        "responsible": _person(event.responsible_member),
        "created_by": _person(event.creator),
        "content_links": events_queries.get_event_content_links(session, event_id, published_only=False),
    }


def _person(member: Members | None) -> dict | None:
    return {"member_id": member.id, "name": member.name} if member else None


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=Events_model,
    responses={409: {"model": ConflictResponse, "description": "Event already exists"}},
    dependencies=[Depends(Require(Perm.EVENTS_CREATE))],
)
def create_event(event_data: createEvent_model, session: DB, member: CurrentMember, access: CurrentAccess):
    access.require(Perm.EVENTS_CREATE, event_data.department_id)
    try:
        logger.info("Member [%s] creating New Event and Associated Form", member.id)
        new_event, _ = create_full_event(session, event_data, responsible_member_id=member.id, created_by=member.id)
        event_history.record(
            session,
            new_event,
            EventHistoryAction.CREATED,
            member,
            {
                "department_id": event_data.department_id,
                "department_action_id": event_data.department_action_id,
                "member_action_id": event_data.member_action_id,
            },
        )
        session.commit()
        session.refresh(new_event)

        # Best-effort: reset the leaderboard app's data cache so the new event is visible immediately.
        reset_leaderboard_cache_best_effort()
        logger.info("Leaderboard cache reset triggered after event creation")

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
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def update_event(
    event_id: int, event_data: UpdateEventModel, session: DB, member: CurrentMember, background_tasks: BackgroundTasks
):
    try:
        logger.info(f"Updating Event [{event_id}]")
        existing = events_queries.get_event_by_id(session, event_id)
        before = event_history.snapshot(existing) if existing else {}
        push_before = push_cases.event_state(existing) if existing else {}
        points_before: dict = {}

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

            points_before = {
                "department_action_id": department_log.action_id,
                "member_action_id": member_log.action_id,
                "department_id": log_queries.get_department_id_from_log(session, department_log.id),
            }

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

        changed = event_history.changes(before, event_history.snapshot(updated_event))
        if points_before:
            points_after = {
                "department_action_id": event_data.actions[0].action_id,
                "member_action_id": event_data.actions[1].action_id,
                "department_id": event_data.actions[0].department_id or points_before["department_id"],
            }
            changed |= event_history.changes(points_before, points_after)
        recorded = (
            event_history.record(session, updated_event, EventHistoryAction.EDITED, member, changed)
            if changed
            else None
        )
        pushed = push_cases.event_changed(session, updated_event, push_before, recorded.id if recorded else None)
        calendar_request = pipeline_calendar.mark_event(session, event_id)
        session.commit()
        pipeline_calendar.sync_after_commit(background_tasks, calendar_request)
        if pushed:
            background_tasks.add_task(push_delivery.kick)
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
    dependencies=[Depends(Require(Perm.EVENTS_DELETE, event_departments))],
    response_model=DetailResponse,
)
def delete_event(event_id: int, session: DB, member: CurrentMember, background_tasks: BackgroundTasks):
    logger.info(f"Deleting Event [{event_id}]")

    event = events_queries.get_event_by_id(session, event_id)
    if not event:
        logger.error(f"HTTP 404: Event [{event_id}] not found")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.status != "draft":
        logger.error(f"HTTP 400: Cannot delete event [{event_id}] with status [{event.status}]")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only draft events can be deleted")

    # A pipeline request that published it goes too, through the foreign key:
    # its tasks, penalty and points, and its days are free again. Its history
    # stays, with this as the last entry, and its calendar entry comes off.
    calendar_request = pipeline_calendar.mark_event(session, event_id)
    request = pipeline_queries.get_request_for_event(session, event_id)
    if request is not None:
        pipeline_history.record(
            session,
            PipelineHistoryAction.EVENT_DELETED,
            member,
            request,
            {"event_id": event_id, "event_name": event.name},
        )
    event_history.record(session, event, EventHistoryAction.DELETED, member)
    event_name = event.name
    events_queries.delete_event(session, event_id)
    session.commit()
    pipeline_calendar.sync_after_commit(background_tasks, calendar_request)
    logger.info(f"Event [{event_id}]: {event_name} deleted successfully")
    return {"detail": "Event deleted successfully"}


@router.put(
    "/{event_id:int}/status",
    status_code=status.HTTP_200_OK,
    response_model=Events_model,
    responses={404: {"model": NotFoundResponse, "description": "Event not found"}},
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def update_event_status(
    event_id: int,
    status_data: UpdateEventStatus_model,
    session: DB,
    member: CurrentMember,
    background_tasks: BackgroundTasks,
):
    event = events_queries.get_event_by_id(session, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    old_status = event.status

    # Publishing/unpublishing the event also publishes/unpublishes its Google
    # Form, if it has one - a copied form does not inherit the template's
    # accepting-responses state, so without this members hit an "unpublished
    # form" wall the admin has no way to see coming. Best-effort: the event's
    # own status is the source of truth, so a Google API failure here must not
    # block it - same reasoning as the leaderboard cache reset below.
    entering_open = status_data.status == "open" and old_status != EventsStatus.OPEN
    leaving_open = old_status == EventsStatus.OPEN and status_data.status != "open"
    if entering_open or leaving_open:
        form = form_queries.get_form_by_event_id(session, event_id)
        if form.form_type == FormType.GOOGLE and form.google_form_id:
            try:
                set_form_publish_state(form.google_form_id, is_published=entering_open)
            except Exception:
                logger.exception(
                    "Failed to %s Google Form %s for event %s",
                    "publish" if entering_open else "unpublish",
                    form.google_form_id,
                    event_id,
                )

    # Locked only now, after Google answered, so nothing waits on that call. A double
    # submit waits here, then finds the status already set: no history, no push.
    event = events_queries.lock_event(session, event_id)
    old_status = event.status
    push_before = push_cases.event_state(event)
    event.status = EventsStatus(status_data.status)
    recorded = None
    if event.status != old_status:
        recorded = event_history.record(
            session,
            event,
            EventHistoryAction.STATUS_CHANGED,
            member,
            {"status": [old_status.value, event.status.value]},
        )
    pushed = push_cases.event_changed(session, event, push_before, recorded.id if recorded else None)
    session.commit()
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    session.refresh(event)

    # Best-effort: reset the leaderboard cache when entering or leaving the "open" status,
    # since the leaderboard's open-events view is gated on status = 'open'.
    if status_data.status == "open" or old_status == "open":
        reset_leaderboard_cache_best_effort()

    return event


@router.put(
    "/{event_id:int}/meeting-url",
    status_code=status.HTTP_200_OK,
    response_model=Events_model,
    responses={404: {"model": NotFoundResponse, "description": "Event not found"}},
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def update_event_meeting_url(
    event_id: int,
    meeting_url_data: UpdateEventMeetingUrl_model,
    session: DB,
    member: CurrentMember,
    background_tasks: BackgroundTasks,
):
    """Set or clear the join link shown to members on a remote event."""
    existing_event = events_queries.lock_event(session, event_id)
    if meeting_url_data.meeting_url and existing_event.location_type != EventsLocationType.ONLINE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="meeting_url can only be set on an online event"
        )
    old_url = existing_event.meeting_url
    push_before = push_cases.event_state(existing_event)
    events_queries.update_event_meeting_url(session, event_id, meeting_url_data.meeting_url)
    event = existing_event
    recorded = None
    if old_url != event.meeting_url:
        recorded = event_history.record(
            session,
            event,
            EventHistoryAction.MEETING_URL_CHANGED,
            member,
            {"meeting_url": [old_url, event.meeting_url]},
        )
    pushed = push_cases.event_changed(session, event, push_before, recorded.id if recorded else None)
    calendar_request = pipeline_calendar.mark_event(session, event_id)
    session.commit()
    pipeline_calendar.sync_after_commit(background_tasks, calendar_request)
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    session.refresh(event)
    logger.info(f"Event [{event_id}] meeting url {'set' if meeting_url_data.meeting_url else 'cleared'}")

    # Best-effort: the leaderboard app caches event data, so the link would otherwise
    # not appear until the next revalidation.
    reset_leaderboard_cache_best_effort()

    return event


_content_link_snapshot = events_queries.content_link_snapshot
_duplicate_label_error = events_queries.is_duplicate_label_error


def _label_taken_response(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "/{event_id:int}/content-links",
    status_code=status.HTTP_201_CREATED,
    response_model=EventContentLink_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Event not found"},
        409: {"model": ConflictResponse, "description": "A link with this label already exists"},
    },
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def create_event_content_link(
    event_id: int,
    link_data: CreateEventContentLink_model,
    session: DB,
    member: CurrentMember,
    background_tasks: BackgroundTasks,
):
    """Manually paste a link to event content (Drive/YouTube) with the display
    label shown to members; published immediately."""
    if events_queries.is_content_link_label_taken(session, event_id, link_data.label):
        raise _label_taken_response("A content link with this label already exists for this event")
    # The unique index backstops the check above: two racing creates cannot
    # both pass the pre-check, and the loser is a 409 too. Any other
    # IntegrityError is not a label clash and stays an error.
    try:
        link = events_queries.create_event_content_link(session, event_id, link_data, created_by=member.id)
        event_history.record(
            session,
            event_id,
            EventHistoryAction.CONTENT_LINK_CHANGED,
            member,
            {"content_link": [None, _content_link_snapshot(link)]},
        )
        pushed = push_cases.content_added(session, link)
        session.commit()
        session.refresh(link)
        # The response carries the derived download_url too, same as the read
        # payloads - derive it once the row is settled.
        events_queries.set_download_url(link)
    except IntegrityError as exc:
        session.rollback()
        if not _duplicate_label_error(exc):
            raise
        raise _label_taken_response("A content link with this label already exists for this event")
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    logger.info(f"Event [{event_id}] content link [{link.id}] created, kind [{link.kind}]")
    reset_leaderboard_cache_best_effort()
    return link


@router.put(
    "/{event_id:int}/content-links/{link_id}",
    status_code=status.HTTP_200_OK,
    response_model=EventContentLink_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Link not found"},
        409: {"model": ConflictResponse, "description": "A link with this label already exists"},
    },
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def update_event_content_link(
    event_id: int,
    link_id: UUID,
    link_data: UpdateEventContentLink_model,
    session: DB,
    member: CurrentMember,
    background_tasks: BackgroundTasks,
):
    """Edit a content link's label/url/kind/published state. Links stay
    editable and deletable forever - nothing here clears or locks them."""
    existing = events_queries.get_content_link(session, event_id, str(link_id))
    if not existing:
        logger.error(f"HTTP 404: Content link [{link_id}] not found for event [{event_id}]")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content link not found")
    before = _content_link_snapshot(existing)
    if link_data.label is not None and events_queries.is_content_link_label_taken(
        session, event_id, link_data.label, exclude_id=str(link_id)
    ):
        raise _label_taken_response("A content link with this label already exists for this event")
    try:
        link = events_queries.update_event_content_link(session, existing, link_data)
        # The response carries the derived download_url too, same as the read
        # payloads - derive it once the row is edited.
        events_queries.set_download_url(link)
        event_history.record(
            session,
            event_id,
            EventHistoryAction.CONTENT_LINK_CHANGED,
            member,
            {"content_link": [before, _content_link_snapshot(link)]},
        )
        # Published only now: to members it is new.
        pushed = push_cases.content_added(session, link) if not before["published"] else 0
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if not _duplicate_label_error(exc):
            raise
        raise _label_taken_response("A content link with this label already exists for this event")
    if pushed:
        background_tasks.add_task(push_delivery.kick)
    logger.info(f"Event [{event_id}] content link [{link_id}] updated")
    reset_leaderboard_cache_best_effort()
    return link


@router.delete(
    "/{event_id:int}/content-links/{link_id}",
    status_code=status.HTTP_200_OK,
    response_model=DetailResponse,
    responses={404: {"model": NotFoundResponse, "description": "Link not found"}},
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def delete_event_content_link(event_id: int, link_id: UUID, session: DB, member: CurrentMember):
    """Hard delete; a deleted link is gone from the event detail response."""
    link = events_queries.delete_event_content_link(session, event_id, str(link_id))
    if not link:
        logger.error(f"HTTP 404: Content link [{link_id}] not found for event [{event_id}]")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content link not found")
    event_history.record(
        session,
        event_id,
        EventHistoryAction.CONTENT_LINK_CHANGED,
        member,
        {"content_link": [_content_link_snapshot(link), None]},
    )
    session.commit()
    logger.info(f"Event [{event_id}] content link [{link_id}] deleted")
    reset_leaderboard_cache_best_effort()
    return {"detail": "Content link deleted successfully"}


@router.get(
    "/{event_id:int}/drive-recording-candidates",
    status_code=status.HTTP_200_OK,
    response_model=DriveRecordingCandidatesResponse,
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def get_drive_recording_candidates(event_id: int, session: DB, member: CurrentMember, gdrive: GDrive):
    """Retrieve Google Meet recording candidates for an event using Meet API v2
    with fallback to legacy Drive folder search."""
    if not gdrive.is_configured():
        raise DriveIntegrationNotConfigured()

    event = events_queries.get_event_by_id(session, event_id)
    linked_ids = events_queries.get_all_linked_drive_file_ids(session)

    state, candidates = gdrive.find_recording_candidates(
        meeting_url=event.meeting_url,
        start_dt=event.start_datetime,
        end_dt=event.end_datetime,
        exclude_file_ids=linked_ids,
    )
    return {"state": state, "candidates": candidates}


@router.post(
    "/{event_id:int}/drive-recording-candidates/{file_id}/make-public",
    status_code=status.HTTP_200_OK,
    response_model=DriveFileMakePublicResponse_model,
    responses={
        404: {"model": NotFoundResponse, "description": "Event or recording candidate not found for this event"}
    },
    dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))],
)
def make_drive_recording_candidate_public(
    event_id: int, file_id: str, session: DB, member: CurrentMember, gdrive: GDrive
):
    """Grant public read permission ('anyone with the link can view') to a recording candidate."""
    if not gdrive.is_configured():
        raise DriveIntegrationNotConfigured()

    event = events_queries.get_event_by_id(session, event_id)
    linked_ids = events_queries.get_all_linked_drive_file_ids(session)
    state, candidates = gdrive.find_recording_candidates(
        meeting_url=event.meeting_url,
        start_dt=event.start_datetime,
        end_dt=event.end_datetime,
        exclude_file_ids=linked_ids,
    )
    is_candidate = file_id in {c["file_id"] for c in candidates}
    existing_links = events_queries.get_event_content_links(session, event.id, published_only=False)
    is_event_link = any(
        link.drive_file_id == file_id or (link.url and events_queries._drive_file_id_from_url(link.url) == file_id)
        for link in existing_links
    )
    if not (is_candidate or is_event_link):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recording candidate not found for this event"
        )

    gdrive.make_public(file_id, raise_on_blocked=True)
    is_public = gdrive.is_file_public(file_id)
    return {"file_id": file_id, "is_public": is_public}


# TODO: move to submissions router.
@router.get(
    "/submissions/{event_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=list[Get_Submission_model],
    dependencies=[Depends(Require(Perm.SUBMISSIONS_REVIEW, event_departments))],
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
