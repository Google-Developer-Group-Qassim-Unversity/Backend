"""Which department is Design, Logistics and Media in the events pipeline, and the departments to pick from.

A team is the department on the current semester's roster whose English name
contains the team's name, ignoring case: "Design", "UI/UX Design" and "design"
are all Design. Exactly one department may match; none or several means the
team is not set, until a department is renamed in Club structure.
"""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.DB.schema import Departments, PipelineTeam, SemesterDepartments
from app.semesters import current_semester


def get_departments(session: Session, ids: set[int] | None = None) -> Sequence[Departments]:
    """Active departments, or exactly ``ids`` whatever their state."""
    statement = select(Departments).order_by(Departments.id)
    if ids is None:
        statement = statement.where(Departments.active == 1)
    else:
        statement = statement.where(Departments.id.in_(ids))
    return session.scalars(statement).all()


def get_team_candidates(session: Session) -> dict[PipelineTeam, list[Departments]]:
    """Every department on this semester's roster whose name contains each team's name.

    The name is the one the department has this semester (``semester_departments.name``,
    falling back to its current name).
    """
    semester = current_semester(session)
    if semester is None:
        return {team: [] for team in PipelineTeam}
    rows = session.execute(
        select(Departments, func.coalesce(SemesterDepartments.name, Departments.name))
        .join(SemesterDepartments, SemesterDepartments.department_id == Departments.id)
        .where(SemesterDepartments.semester_id == semester.id)
        .order_by(Departments.id)
    ).all()
    return {team: [d for d, name in rows if team.value in name.casefold()] for team in PipelineTeam}


def get_teams(session: Session) -> dict[PipelineTeam, Departments]:
    """The teams that have exactly one department this semester."""
    return {team: found[0] for team, found in get_team_candidates(session).items() if len(found) == 1}
