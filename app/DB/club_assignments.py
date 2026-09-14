"""Club structure reads. Only explicit assignments contribute to rosters/counts."""

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import ClubAssignmentRole, ClubAssignments, Departments


def get_department_roster(session: Session, department_id: int) -> Sequence[ClubAssignments]:
    """Include leaders and deputies once, including when the department is archived."""
    return session.scalars(
        select(ClubAssignments)
        .where(ClubAssignments.current_scope_id == department_id)
        .options(selectinload(ClubAssignments.member))
        .order_by(ClubAssignments.role, ClubAssignments.member_id)
    ).all()


def get_active_department_rosters(session: Session) -> Sequence[ClubAssignments]:
    """Load every current assignment for active departments in one query."""
    return session.scalars(
        select(ClubAssignments)
        .join(Departments, Departments.id == ClubAssignments.department_id)
        .where(ClubAssignments.ends_at.is_(None), Departments.active == 1)
        .options(selectinload(ClubAssignments.member))
        .order_by(ClubAssignments.department_id, ClubAssignments.role, ClubAssignments.member_id)
    ).all()


def get_presidents(session: Session) -> Sequence[ClubAssignments]:
    return session.scalars(
        select(ClubAssignments)
        .where(ClubAssignments.current_scope_id == 0)
        .options(selectinload(ClubAssignments.member))
        .order_by(ClubAssignments.president_slot)
    ).all()


def get_current_leadership(session: Session, *, include_archived: bool = False) -> Sequence[ClubAssignments]:
    """Load overview leadership in bulk, without querying once per card."""
    statement = (
        select(ClubAssignments)
        .join(Departments, Departments.id == ClubAssignments.department_id)
        .where(
            ClubAssignments.ends_at.is_(None),
            ClubAssignments.role.in_([ClubAssignmentRole.LEADER, ClubAssignmentRole.DEPUTY]),
        )
        .options(selectinload(ClubAssignments.member))
        .order_by(ClubAssignments.department_id, ClubAssignments.role)
    )
    if not include_archived:
        statement = statement.where(Departments.active == 1)
    return session.scalars(statement).all()


def get_tenure_history(
    session: Session,
    *,
    department_id: int | None = None,
    member_id: int | None = None,
    role: ClubAssignmentRole | None = None,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[ClubAssignments]:
    """Current and closed periods, newest first; no archive filtering.

    Use role=PRESIDENT for club-wide history. The ID breaks timestamp ties so
    pagination is deterministic even when several periods change together.
    """
    statement = select(ClubAssignments).options(
        selectinload(ClubAssignments.member), selectinload(ClubAssignments.department)
    )
    if department_id is not None:
        statement = statement.where(ClubAssignments.department_id == department_id)
    if member_id is not None:
        statement = statement.where(ClubAssignments.member_id == member_id)
    if role is not None:
        statement = statement.where(ClubAssignments.role == role)
    return session.scalars(
        statement.order_by(ClubAssignments.starts_at.desc(), ClubAssignments.id.desc()).limit(limit).offset(offset)
    ).all()


def get_department_member_counts(session: Session, *, include_archived: bool = False) -> dict[int, int]:
    """Keep empty departments in the result, with zero people."""
    statement = (
        select(Departments.id, func.count(ClubAssignments.member_id.distinct()))
        .outerjoin(ClubAssignments, ClubAssignments.current_scope_id == Departments.id)
        .group_by(Departments.id)
        .order_by(Departments.id)
    )
    if not include_archived:
        statement = statement.where(Departments.active == 1)
    return dict(session.execute(statement).tuples().all())


def count_current_members(session: Session, *, include_archived: bool = False) -> int:
    """Count a person once across Presidents and all included department roles."""
    statement = (
        select(func.count(ClubAssignments.member_id.distinct()))
        .outerjoin(Departments, Departments.id == ClubAssignments.department_id)
        .where(ClubAssignments.ends_at.is_(None))
    )
    if not include_archived:
        statement = statement.where(or_(ClubAssignments.department_id.is_(None), Departments.active == 1))
    return session.scalar(statement) or 0


def lock_department(session: Session, department_id: int) -> Departments | None:
    """Serialize roster writes and archive/restore on their shared parent row.

    Refresh objects already in the identity map: an earlier overview read may
    have happened before another transaction archived this department.
    """
    return session.scalar(
        select(Departments)
        .where(Departments.id == department_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_member_assignment(session: Session, department_id: int, member_id: int) -> ClubAssignments | None:
    # A locking read also bypasses an older MySQL REPEATABLE READ snapshot.
    return session.scalar(
        select(ClubAssignments)
        .where(ClubAssignments.current_scope_id == department_id, ClubAssignments.member_id == member_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_leadership_assignment(
    session: Session, department_id: int, role: ClubAssignmentRole
) -> ClubAssignments | None:
    return session.scalar(
        select(ClubAssignments)
        .where(ClubAssignments.current_scope_id == department_id, ClubAssignments.current_leadership_role == role.value)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_assignment(session: Session, assignment_id: int) -> ClubAssignments | None:
    return session.scalar(
        select(ClubAssignments)
        .where(ClubAssignments.id == assignment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
