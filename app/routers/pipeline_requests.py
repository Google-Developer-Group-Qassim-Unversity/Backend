"""Event requests: book dates, fill in the event, and move it along the pipeline."""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.DB import event_pipeline as queries
from app.DB.schema import EventRequests, EventRequestStage, PipelineTeam
from app.dependencies import DB

from app.leaderboard_cache import reset_leaderboard_cache
from app.routers.pipeline_models import (
    BookRequest,
    EventDetails,
    EventRequestDetail,
    EventRequestSummary,
    PenaltyResponse,
    RequestActions,
    ReturnRequest,
    PaginatedEventRequests,
    PersonRef,
    PipelineDepartment,
    PublishRequest,
    RedateRequest,
    SaveBriefRequest,
    TaskResponse,
    UpdateDetailsRequest,
)
from app.routers.responses import DetailResponse
from app.services import event_briefs
from app.services import event_pipeline as service
from app.services import event_pipeline_clock as clock
from app.services import pipeline_notifications as notifications
from app.services.permissions.dependencies import Caller, CurrentCaller
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require, Staff

logger = logging.getLogger(__name__)


# Staff only. Each route then checks the pipeline permissions it needs in the service.
router = APIRouter(prefix="/pipeline/requests", tags=["events pipeline"], dependencies=[Depends(Staff)])


def summary(request: EventRequests) -> EventRequestSummary:
    return EventRequestSummary(
        id=request.id,
        department=PipelineDepartment.model_validate(request.department),
        stage=request.stage,
        title=request.title,
        start_date=request.start_date,
        end_date=request.end_date,
        hold_expires_at=request.hold_expires_at,
        undated_reason=request.undated_reason,
        created_at=request.created_at,
    )


def _penalty(session, request: EventRequests) -> PenaltyResponse | None:
    penalty = service.get_penalty(session, request)
    if penalty is None:
        return None
    return PenaltyResponse(
        late_days=penalty.late_days, points=penalty.points, applied=penalty.applied_log_id is not None
    )


def _actions(session, caller: Caller, request: EventRequests) -> RequestActions:
    requester = service.can_request_for(caller, request.department_id)
    return RequestActions(
        can_submit=requester and request.stage == EventRequestStage.DRAFT,
        can_return=service.can_return(session, caller, request),
        can_resubmit=requester and request.stage == EventRequestStage.RETURNED,
        complete=[t for t in service.ALL_TEAMS if service.can_complete(session, caller, request, t)],
        can_publish=service.can_publish(caller, request),
    )


def detail(session, caller: Caller, request: EventRequests) -> EventRequestDetail:
    session.refresh(request)
    return EventRequestDetail(
        **summary(request).model_dump(),
        created_by=PersonRef(member_id=request.creator.id, name=request.creator.name),
        details=EventDetails(
            title=request.title,
            description=request.description,
            event_type=request.event_type,
            presenter_name=request.presenter_name,
            presenter_email=request.presenter_email,
            day_modes=request.day_modes,
            daily_start_time=request.daily_start_time,
            daily_end_time=request.daily_end_time,
            is_official=None if request.is_official is None else bool(request.is_official),
            location_scope=request.location_scope,
            audience=request.audience,
            registration=request.registration,
            expected_accepted=request.expected_accepted,
            help_needed=request.help_needed,
        ),
        partners=[PipelineDepartment.model_validate(p.department) for p in request.partners],
        within_official_hours=service.within_official_hours(request),
        submitted_at=request.submitted_at,
        updated_at=request.updated_at,
        event_id=request.event_id,
        can_edit=service.can_edit(caller, request),
        tasks=[
            TaskResponse(
                team=task.team,
                status=task.status,
                brief=task.brief,
                brief_version=task.brief_version,
                opened_at=task.opened_at,
                completed_at=task.completed_at,
                completed_by=PersonRef(member_id=task.completer.id, name=task.completer.name)
                if task.completer
                else None,
            )
            for task in request.tasks
        ],
        missing=event_briefs.missing_fields(request)
        if request.stage in (EventRequestStage.DRAFT, EventRequestStage.RETURNED)
        else [],
        returned_at=request.returned_at,
        return_count=request.return_count,
        return_notes=request.return_notes,
        return_due_at=request.return_due_at,
        return_deadline=service.return_deadline(request),
        penalty=_penalty(session, request),
        actions=_actions(session, caller, request),
        now=clock.now(),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EventRequestDetail)
def book_event_request(body: BookRequest, session: DB, caller: CurrentCaller):
    """Book a date range: a new draft that holds those days for 24 hours."""
    with service.booking_lock(session):
        request = service.book(session, caller, body.department_id, body.start_date, body.end_date)
        session.commit()
    return detail(session, caller, request)


@router.get("", status_code=status.HTTP_200_OK, response_model=PaginatedEventRequests)
def list_event_requests(
    session: DB,
    caller: CurrentCaller,
    department_id: Annotated[int | None, Query()] = None,
    stage: Annotated[EventRequestStage | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """The requests of the departments the caller acts for (every department for a super admin)."""
    visible = service.visible_department_ids(session, caller)
    if department_id is not None:
        service.require_request_for(caller, department_id)
        visible = {department_id}
    total, rows = queries.list_requests(session, visible, stage, page_size, (page - 1) * page_size)
    return PaginatedEventRequests(
        items=[summary(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/{request_id:int}", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def get_event_request(request_id: int, session: DB, caller: CurrentCaller):
    return detail(session, caller, service.get_request_for(session, caller, request_id))


@router.put("/{request_id:int}/dates", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def redate_event_request(request_id: int, body: RedateRequest, session: DB, caller: CurrentCaller):
    """New dates for a request that lost its own; a draft gets a fresh 24-hour hold."""
    with service.booking_lock(session):
        request = service.get_request_for(session, caller, request_id, lock=True)
        service.redate(session, caller, request, body.start_date, body.end_date)
        session.commit()
    return detail(session, caller, request)


@router.put("/{request_id:int}/details", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def update_event_request_details(request_id: int, body: UpdateDetailsRequest, session: DB, caller: CurrentCaller):
    """Save any of the event details; only the fields sent change."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    service.update_details(session, caller, request, body.model_dump(exclude_unset=True))
    session.commit()
    return detail(session, caller, request)


@router.delete("/{request_id:int}", status_code=status.HTTP_200_OK, response_model=DetailResponse)
def cancel_event_request(request_id: int, session: DB, caller: CurrentCaller):
    """Drop a draft. Its dates, if it still held any, are free again."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    service.cancel(session, caller, request)
    session.commit()
    logger.info("Request %s cancelled", request_id)
    return DetailResponse(detail="Request cancelled")


@router.put("/{request_id:int}/briefs/{team}", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def save_event_request_brief(
    request_id: int, team: PipelineTeam, body: SaveBriefRequest, session: DB, caller: CurrentCaller
):
    """Save the Design or Logistics brief as a draft; submit checks it."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    service.save_brief(session, caller, request, team, body.brief)
    session.commit()
    return detail(session, caller, request)


@router.post("/{request_id:int}/submit", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def submit_event_request(request_id: int, session: DB, caller: CurrentCaller, background_tasks: BackgroundTasks):
    """Send a complete request to Design and Logistics. A 422 lists every missing field."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    emails = service.submit(session, caller, request)
    session.commit()
    notifications.send_after_commit(session, background_tasks, emails)
    return detail(session, caller, request)


@router.post(
    "/{request_id:int}/return",
    status_code=status.HTTP_200_OK,
    response_model=EventRequestDetail,
    dependencies=[Depends(Require(Perm.PIPELINE_DESIGN))],
)
def return_event_request(
    request_id: int, body: ReturnRequest, session: DB, caller: CurrentCaller, background_tasks: BackgroundTasks
):
    """Design sends the request back with notes: once, within two days. The team has 12 hours."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    email = service.return_request(session, caller, request, body.notes)
    session.commit()
    notifications.send_after_commit(session, background_tasks, [email])
    return detail(session, caller, request)


@router.post("/{request_id:int}/resubmit", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def resubmit_event_request(request_id: int, session: DB, caller: CurrentCaller, background_tasks: BackgroundTasks):
    """The team sends its fixed request back to Design. Late costs points, applied at publish."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    email = service.resubmit(session, caller, request)
    session.commit()
    notifications.send_after_commit(session, background_tasks, [email])
    return detail(session, caller, request)


@router.post(
    "/{request_id:int}/tasks/{team}/complete", status_code=status.HTTP_200_OK, response_model=EventRequestDetail
)
def complete_event_request_task(
    request_id: int, team: PipelineTeam, session: DB, caller: CurrentCaller, background_tasks: BackgroundTasks
):
    """A team marks its part done."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    emails = service.complete(session, caller, request, team)
    session.commit()
    notifications.send_after_commit(session, background_tasks, emails)
    return detail(session, caller, request)


@router.post("/{request_id:int}/publish", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def publish_event_request(request_id: int, body: PublishRequest, session: DB, caller: CurrentCaller):
    """Turn a ready request into a real event in /events, created as a draft for admins to review."""
    request = service.get_request_for(session, caller, request_id, lock=True)
    service.publish(session, caller, request, body.department_action_id, body.member_action_id, body.image_url)
    session.commit()
    # Best-effort, as after POST /events/: the leaderboard app caches events.
    try:
        reset_leaderboard_cache()
    except Exception as cache_error:
        logger.error(cache_error)
    return detail(session, caller, request)
