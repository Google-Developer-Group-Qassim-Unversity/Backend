"""The events pipeline: from booking a date to a published event."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.DB import department_permissions as permission_queries
from app.DB.schema import PipelineTeam
from app.dependencies import DB
from app.exceptions import DepartmentForbidden, NotFound, PipelineConflict
from app.helpers import super_admin_guard
from app.routers.pipeline_models import (
    ActingDepartment,
    BanDaysRequest,
    BanResult,
    CalendarDayResponse,
    CalendarResponse,
    UnbanDaysRequest,
    PipelineDepartment,
    PipelineMeResponse,
    PipelineTeamEntry,
    SetPipelineTeamsRequest,
)
from app.services import event_pipeline as pipeline_service
from app.services import event_pipeline_clock as clock
from app.services.department_permissions import Actor, PipelineActor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["events pipeline"])


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
        days=[CalendarDayResponse(date=d.date, status=d.status.value, reason=d.reason) for d in days],
    )


@router.put("/calendar/bans", status_code=status.HTTP_200_OK, response_model=BanResult)
def ban_pipeline_days(body: BanDaysRequest, session: DB, actor: Actor):
    """Logistics closes days to bookings. A day already banned takes the new reason."""
    days = pipeline_service.ban(session, actor, body.dates, body.reason)
    session.commit()
    return BanResult(count=len(days))


@router.delete("/calendar/bans", status_code=status.HTTP_200_OK, response_model=BanResult)
def unban_pipeline_days(body: UnbanDaysRequest, session: DB, actor: Actor):
    """Logistics reopens days. Requests that lost a day to the ban do not get it back."""
    removed = pipeline_service.unban(session, actor, body.dates)
    session.commit()
    return BanResult(count=removed)
