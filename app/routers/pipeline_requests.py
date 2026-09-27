"""Event requests: book dates, fill in the event, and move it along the pipeline."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.DB import event_pipeline as queries
from app.DB.schema import EventRequests, EventRequestStage
from app.dependencies import DB
from app.routers.pipeline_models import (
    BookRequest,
    EventDetails,
    EventRequestDetail,
    EventRequestSummary,
    PaginatedEventRequests,
    PersonRef,
    PipelineDepartment,
    RedateRequest,
    UpdateDetailsRequest,
)
from app.routers.responses import DetailResponse
from app.services import event_pipeline as service
from app.services import event_pipeline_clock as clock
from app.services.department_permissions import Actor, PipelineActor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline/requests", tags=["events pipeline"])


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


def detail(session, actor: PipelineActor, request: EventRequests) -> EventRequestDetail:
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
        can_edit=service.can_edit(actor, request),
        now=clock.now(),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EventRequestDetail)
def book_event_request(body: BookRequest, session: DB, actor: Actor):
    """Book a date range: a new draft that holds those days for 24 hours."""
    with service.booking_lock(session):
        request = service.book(session, actor, body.department_id, body.start_date, body.end_date)
        session.commit()
    return detail(session, actor, request)


@router.get("", status_code=status.HTTP_200_OK, response_model=PaginatedEventRequests)
def list_event_requests(
    session: DB,
    actor: Actor,
    department_id: Annotated[int | None, Query()] = None,
    stage: Annotated[EventRequestStage | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """The requests of the departments the caller acts for (every department for a super admin)."""
    visible = service.visible_department_ids(actor)
    if department_id is not None:
        actor.require_act_for(department_id)
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
def get_event_request(request_id: int, session: DB, actor: Actor):
    return detail(session, actor, service.get_request_for(session, actor, request_id))


@router.put("/{request_id:int}/dates", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def redate_event_request(request_id: int, body: RedateRequest, session: DB, actor: Actor):
    """New dates for a request that lost its own; a draft gets a fresh 24-hour hold."""
    with service.booking_lock(session):
        request = service.get_request_for(session, actor, request_id, lock=True)
        service.redate(session, actor, request, body.start_date, body.end_date)
        session.commit()
    return detail(session, actor, request)


@router.put("/{request_id:int}/details", status_code=status.HTTP_200_OK, response_model=EventRequestDetail)
def update_event_request_details(request_id: int, body: UpdateDetailsRequest, session: DB, actor: Actor):
    """Save any of the event details; only the fields sent change."""
    request = service.get_request_for(session, actor, request_id, lock=True)
    service.update_details(session, actor, request, body.model_dump(exclude_unset=True))
    session.commit()
    return detail(session, actor, request)


@router.delete("/{request_id:int}", status_code=status.HTTP_200_OK, response_model=DetailResponse)
def cancel_event_request(request_id: int, session: DB, actor: Actor):
    """Drop a draft. Its dates, if it still held any, are free again."""
    request = service.get_request_for(session, actor, request_id, lock=True)
    service.cancel(session, actor, request)
    session.commit()
    logger.info("Request %s cancelled", request_id)
    return DetailResponse(detail="Request cancelled")
