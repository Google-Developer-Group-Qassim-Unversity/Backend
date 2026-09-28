"""The events pipeline: from booking a date to a published event."""

import logging

from fastapi import APIRouter, Depends, status

from app.DB import department_permissions as permission_queries
from app.DB.schema import PipelineTeam
from app.dependencies import DB
from app.exceptions import NotFound, PipelineConflict
from app.helpers import super_admin_guard
from app.routers.pipeline_models import (
    ActingDepartment,
    PipelineDepartment,
    PipelineMeResponse,
    PipelineTeamEntry,
    SetPipelineTeamsRequest,
)
from app.services.department_permissions import Actor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["events pipeline"])


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
