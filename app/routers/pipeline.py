"""The events pipeline: from booking a date to a published event."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.DB import pipeline_teams as team_queries
from app.DB.schema import EventRequests, PipelineTeam
from app.dependencies import DB
from app.exceptions import DepartmentForbidden

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
    SweepResponse,
)
from app.services import event_pipeline as pipeline_service
from app.services import event_pipeline_clock as clock
from app.services import pipeline_notifications as notifications
from app.services import pipeline_sweep
from app.services.permissions.catalogue import OFFICER_ROLES
from app.services.permissions.dependencies import Caller, CurrentCaller
from app.services.permissions.catalogue import Perm
from app.services.permissions.guards import Require, Staff, SuperAdmin

logger = logging.getLogger(__name__)

# Staff only. Each route then checks the pipeline permissions it needs, here or in the service.
router = APIRouter(prefix="/pipeline", tags=["events pipeline"], dependencies=[Depends(Staff)])


def _require_access(caller: Caller) -> None:
    if not pipeline_service.has_pipeline_access(caller):
        raise DepartmentForbidden(0, "use the pipeline for any")


def _team_entries(session) -> list[PipelineTeamEntry]:
    teams = team_queries.get_teams(session)
    return [
        PipelineTeamEntry(team=team, department=PipelineDepartment.model_validate(teams[team]))
        for team in PipelineTeam
        if team in teams
    ]


@router.get("/me", status_code=status.HTTP_200_OK, response_model=PipelineMeResponse)
def get_pipeline_me(session: DB, caller: CurrentCaller):
    """Which departments the caller can act for, and which department is which team."""
    teams = _team_entries(session)
    teams_by_department: dict[int, list[PipelineTeam]] = {}
    for entry in teams:
        teams_by_department.setdefault(entry.department.id, []).append(entry.team)

    ids = pipeline_service.visible_department_ids(session, caller)
    departments = team_queries.get_departments(session, ids) if ids is None or ids else []
    return PipelineMeResponse(
        member_id=caller.member.id,
        name=caller.member.name,
        is_super_admin=caller.access.is_super_admin,
        has_access=pipeline_service.has_pipeline_access(caller),
        departments=[
            ActingDepartment(
                **PipelineDepartment.model_validate(department).model_dump(),
                is_officer=bool(caller.access.roles.get(department.id, frozenset()) & OFFICER_ROLES),
                teams=teams_by_department.get(department.id, []),
            )
            for department in departments
        ],
        teams=teams,
    )


@router.get("/calendar", status_code=status.HTTP_200_OK, response_model=CalendarResponse)
def get_pipeline_calendar(
    session: DB,
    caller: CurrentCaller,
    start: Annotated[date, Query(alias="from")],
    end: Annotated[date, Query(alias="to")],
):
    """One entry per day from ``from`` to ``to`` (at most 120 days), with its status."""
    _require_access(caller)
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


@router.put(
    "/calendar/bans",
    status_code=status.HTTP_200_OK,
    response_model=BanResult,
    dependencies=[Depends(Require(Perm.PIPELINE_BANS))],
)
def ban_pipeline_days(body: BanDaysRequest, session: DB, caller: CurrentCaller):
    """Logistics closes days to bookings. A day already banned takes the new reason."""
    with pipeline_service.booking_lock(session):
        days, _undated = pipeline_service.ban(session, caller, body.dates, body.reason)
        session.commit()
    return BanResult(count=len(days))


@router.delete(
    "/calendar/bans",
    status_code=status.HTTP_200_OK,
    response_model=BanResult,
    dependencies=[Depends(Require(Perm.PIPELINE_BANS))],
)
def unban_pipeline_days(body: UnbanDaysRequest, session: DB, caller: CurrentCaller):
    """Logistics reopens days. Requests that lost a day to the ban do not get it back."""
    removed = pipeline_service.unban(session, caller, body.dates)
    session.commit()
    return BanResult(count=removed)


def _visible_departments(session, caller: Caller) -> set[int] | None:
    return pipeline_service.visible_department_ids(session, caller)


@router.get("/notifications", status_code=status.HTTP_200_OK, response_model=PaginatedNotifications)
def list_pipeline_notifications(
    session: DB,
    caller: CurrentCaller,
    unread: Annotated[bool, Query()] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """The caller's departments' notifications, newest first. Read state is per person."""
    _require_access(caller)
    total, unread_count, rows, read = notifications.list_for(
        session, _visible_departments(session, caller), caller.member.id, unread, page_size, (page - 1) * page_size
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
def read_pipeline_notification(notification_id: int, session: DB, caller: CurrentCaller):
    _require_access(caller)
    _, _, rows, _ = notifications.list_for(
        session, _visible_departments(session, caller), caller.member.id, True, 1000, 0
    )
    count = notifications.mark_read(session, caller.member.id, [n.id for n in rows if n.id == notification_id])
    session.commit()
    return BanResult(count=count)


@router.post("/notifications/read-all", status_code=status.HTTP_200_OK, response_model=BanResult)
def read_all_pipeline_notifications(session: DB, caller: CurrentCaller):
    _require_access(caller)
    _, _, rows, _ = notifications.list_for(
        session, _visible_departments(session, caller), caller.member.id, True, 1000, 0
    )
    count = notifications.mark_read(session, caller.member.id, [n.id for n in rows])
    session.commit()
    return BanResult(count=count)


@router.post("/sweep", status_code=status.HTTP_200_OK, response_model=SweepResponse, dependencies=[Depends(SuperAdmin)])
def run_pipeline_sweep(background_tasks: BackgroundTasks):
    """Run the timed chores now instead of waiting for the next tick. Safe to repeat."""
    result = pipeline_sweep.sweep_once()
    if result is None:
        return SweepResponse(ran=False)
    background_tasks.add_task(pipeline_sweep.send_emails, result)
    return SweepResponse(ran=True, **result.counts())


@router.get("/inbox", status_code=status.HTTP_200_OK, response_model=list[InboxItem])
def get_pipeline_inbox(session: DB, caller: CurrentCaller, team: Annotated[PipelineTeam | None, Query()] = None):
    """Requests waiting on the caller's team(s), oldest first."""
    _require_access(caller)
    return [
        InboxItem(request=_summary(request), team=task.team, status=task.status, opened_at=task.opened_at)
        for request, task in pipeline_service.inbox(session, caller, team)
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
