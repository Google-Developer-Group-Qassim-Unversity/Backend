"""Which department is Design, Logistics and Media in the events pipeline, and the departments to pick from."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import Departments, PipelineTeam, PipelineTeams


def get_departments(session: Session, ids: set[int] | None = None) -> Sequence[Departments]:
    """Active departments, or exactly ``ids`` whatever their state."""
    statement = select(Departments).order_by(Departments.id)
    if ids is None:
        statement = statement.where(Departments.active == 1)
    else:
        statement = statement.where(Departments.id.in_(ids))
    return session.scalars(statement).all()


def get_teams(session: Session) -> Sequence[PipelineTeams]:
    return session.scalars(select(PipelineTeams).options(selectinload(PipelineTeams.department))).all()


def get_team_department_id(session: Session, team: PipelineTeam) -> int | None:
    return session.scalar(select(PipelineTeams.department_id).where(PipelineTeams.team == team))


def set_teams(session: Session, mapping: dict[PipelineTeam, int | None]) -> None:
    """Replace the whole map. Deleting first keeps the unique department index happy during a swap."""
    for row in session.scalars(select(PipelineTeams)).all():
        session.delete(row)
    session.flush()
    for team, department_id in mapping.items():
        if department_id is not None:
            session.add(PipelineTeams(team=team, department_id=department_id))
    session.flush()
