"""The events pipeline: from booking a date to a published event."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.DB import department_permissions as permission_queries
from app.DB.schema import EventRequests, PipelineTeam
from app.dependencies import DB
from app.exceptions import DepartmentForbidden, NotFound, PipelineConflict
from app.helpers import admin_guard, super_admin_guard
from app.routers.pipeline_models import (
    ActingDepartment,
    BanDaysRequest,
    BanResult,
    CalendarDayRequest,
    CalendarDayResponse,
    CalendarResponse,
    EventRequestSummary,
    InboxItem,
    NotificationItem,
    NotificationRequest,
    PaginatedNotifications,
    UnbanDaysRequest,
    PipelineDepartment,
    PipelineMeResponse,
    PipelineTeamEntry,
    SetPipelineTeamsRequest,
    SweepResponse,
)
from app.services import event_pipeline as pipeline_service
from app.services import event_pipeline_clock as clock
from app.services import pipeline_notifications as notifications
from app.services import pipeline_sweep
from app.services.department_permissions import Actor, PipelineActor

logger = logging.getLogger(__name__)

# Admins only until the new permissions system lands: the pipeline's own department
# checks still run, but a signed-in person with no admin role gets nothing here.
router = APIRouter(prefix="/pipeline", tags=["events pipeline"], dependencies=[Depends(admin_guard)])


def _require_access(actor: PipelineActor) -> None:
    if not actor.has_pipeline_access:
        raise DepartmentForbidden(0, "use the pipeline for any")


def _team_entries(session) -> list[PipelineTeamEntry]:
    return [
        PipelineTeamEntry(team=row.team, department=PipelineDepartment.model_validate(row.department))
        for row in sorted(permission_queries.get_teams(session), key=lambda r: list(PipelineTeam).index(r.team))
    ]


@router.get("/me", status_code=status.HTTP_200_OK, response_model=PipelineMeResponse)
def get_pipeline_me(session: DB, actor: Actor):
    """Which departments the caller can act for, and which department is which team."""
    teams = _team_entries(session)
    teams_by_department: dict[int, list[PipelineTeam]] = {}
    for entry in teams:
        teams_by_department.setdefault(entry.department.id, []).append(entry.team)

    ids = None if actor.is_super_admin else actor.acting_department_ids
    departments = permission_queries.get_departments(session, ids) if ids is None or ids else []
    return PipelineMeResponse(
        member_id=actor.member.id,
        name=actor.member.name,
        is_super_admin=actor.is_super_admin,
        has_access=actor.has_pipeline_access,
        departments=[
            ActingDepartment(
                **PipelineDepartment.model_validate(department).model_dump(),
                is_officer=department.id in actor.officer_of,
                can_grant=actor.can_grant(department.id),
                teams=teams_by_department.get(department.id, []),
            )
            for department in departments
        ],
        teams=teams,
    )


@router.put(
    "/teams",
    status_code=status.HTTP_200_OK,
    response_model=list[PipelineTeamEntry],
    dependencies=[Depends(super_admin_guard)],
)
def set_pipeline_teams(body: SetPipelineTeamsRequest, session: DB):
    mapping = {PipelineTeam.DESIGN: body.design, PipelineTeam.LOGISTICS: body.logistics, PipelineTeam.MEDIA: body.media}
    chosen = [d for d in mapping.values() if d is not None]
    if len(chosen) != len(set(chosen)):
        raise PipelineConflict("team_department_reused", "One department cannot play two teams", 422)
    for department_id in chosen:
        if not permission_queries.get_departments(session, {department_id}):
            raise NotFound("Department", department_id)
    permission_queries.set_teams(session, mapping)
    session.commit()
    logger.info("Pipeline teams set: %s", {t.value: d for t, d in mapping.items()})
    return _team_entries(session)


@router.get("/calendar", status_code=status.HTTP_200_OK, response_model=CalendarResponse)
def get_pipeline_calendar(
    session: DB, actor: Actor, start: Annotated[date, Query(alias="from")], end: Annotated[date, Query(alias="to")]
):
    """One entry per day from ``from`` to ``to`` (at most 120 days), with its status."""
    _require_access(actor)
    days = pipeline_service.calendar(session, start, end)
    return CalendarResponse(
        today=clock.today(),
        first_bookable_date=pipeline_service.first_bookable_day(),
        days=[
            CalendarDayResponse(
                date=d.date,
                status=d.status.value,
                reason=d.reason,
                requests=[
                    CalendarDayRequest(
                        id=r.id,
                        department=PipelineDepartment.model_validate(r.department),
                        title=r.title,
                        stage=r.stage,
                    )
                    for r in d.requests
                ],
            )
            for d in days
        ],
    )


@router.put("/calendar/bans", status_code=status.HTTP_200_OK, response_model=BanResult)
def ban_pipeline_days(body: BanDaysRequest, session: DB, actor: Actor):
    """Logistics closes days to bookings. A day already banned takes the new reason."""
    with pipeline_service.booking_lock(session):
        days, _undated = pipeline_service.ban(session, actor, body.dates, body.reason)
        session.commit()
    return BanResult(count=len(days))


@router.delete("/calendar/bans", status_code=status.HTTP_200_OK, response_model=BanResult)
def unban_pipeline_days(body: UnbanDaysRequest, session: DB, actor: Actor):
    """Logistics reopens days. Requests that lost a day to the ban do not get it back."""
    removed = pipeline_service.unban(session, actor, body.dates)
    session.commit()
    return BanResult(count=removed)


def _visible_departments(actor: PipelineActor) -> set[int] | None:
    return None if actor.is_super_admin else actor.acting_department_ids


@router.get("/notifications", status_code=status.HTTP_200_OK, response_model=PaginatedNotifications)
def list_pipeline_notifications(
    session: DB,
    actor: Actor,
    unread: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """The caller's departments' notifications, newest first. Read state is per person."""
    _require_access(actor)
    total, unread_count, rows, read = notifications.list_for(
        session, _visible_departments(actor), actor.member.id, unread, page_size, (page - 1) * page_size
    )
    return PaginatedNotifications(
        items=[
            NotificationItem(
                id=n.id,
                kind=n.kind,
                department=PipelineDepartment.model_validate(n.department),
                request=NotificationRequest(id=n.request.id, title=n.request.title, stage=n.request.stage),
                payload=n.payload,
                created_at=n.created_at,
                read=n.id in read,
            )
            for n in rows
        ],
        total=total,
        unread=unread_count,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.post("/notifications/{notification_id:int}/read", status_code=status.HTTP_200_OK, response_model=BanResult)
def read_pipeline_notification(notification_id: int, session: DB, actor: Actor):
    _require_access(actor)
    _, _, rows, _ = notifications.list_for(session, _visible_departments(actor), actor.member.id, True, 1000, 0)
    count = notifications.mark_read(session, actor.member.id, [n.id for n in rows if n.id == notification_id])
    session.commit()
    return BanResult(count=count)


@router.post("/notifications/read-all", status_code=status.HTTP_200_OK, response_model=BanResult)
def read_all_pipeline_notifications(session: DB, actor: Actor):
    _require_access(actor)
    _, _, rows, _ = notifications.list_for(session, _visible_departments(actor), actor.member.id, True, 1000, 0)
    count = notifications.mark_read(session, actor.member.id, [n.id for n in rows])
    session.commit()
    return BanResult(count=count)


@router.post(
    "/sweep", status_code=status.HTTP_200_OK, response_model=SweepResponse, dependencies=[Depends(super_admin_guard)]
)
def run_pipeline_sweep(background_tasks: BackgroundTasks):
    """Run the timed chores now instead of waiting for the next tick. Safe to repeat."""
    result = pipeline_sweep.sweep_once()
    if result is None:
        return SweepResponse(ran=False)
    background_tasks.add_task(pipeline_sweep.send_emails, result)
    return SweepResponse(ran=True, **result.counts())


@router.get("/inbox", status_code=status.HTTP_200_OK, response_model=list[InboxItem])
def get_pipeline_inbox(session: DB, actor: Actor, team: Annotated[PipelineTeam | None, Query()] = None):
    """Requests waiting on the caller's team(s), oldest first."""
    _require_access(actor)
    return [
        InboxItem(request=_summary(request), team=task.team, status=task.status, opened_at=task.opened_at)
        for request, task in pipeline_service.inbox(session, actor, team)
    ]


def _summary(request: EventRequests) -> EventRequestSummary:
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
