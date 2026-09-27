"""Club structure reads: which departments a semester has, and who holds which role in them."""

from collections.abc import Sequence

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.DB.schema import (
    ClubMembershipChanges,
    ClubMemberships,
    ClubRoles,
    DepartmentRoleLimits,
    Departments,
    DepartmentsLogs,
    Events,
    EventsStatus,
    Logs,
    SemesterDepartments,
)


def get_roles(session: Session) -> Sequence[ClubRoles]:
    return session.scalars(select(ClubRoles).order_by(ClubRoles.sort_order)).all()


def get_role_by_key(session: Session, key: str) -> ClubRoles | None:
    return session.scalar(select(ClubRoles).where(ClubRoles.key == key))


def get_role_limits(session: Session) -> dict[tuple[int, str], int | None]:
    """Per-department overrides of ``club_roles.max_holders``, keyed by (department, role id)."""
    rows = session.execute(
        select(DepartmentRoleLimits.department_id, DepartmentRoleLimits.role_id, DepartmentRoleLimits.max_holders)
    ).all()
    return {(department_id, role_id): max_holders for department_id, role_id, max_holders in rows}


def get_club_leadership_department(session: Session) -> Departments | None:
    return session.scalar(select(Departments).where(Departments.is_club_leadership == 1))


def get_semester_departments(session: Session, semester_id: str) -> Sequence[SemesterDepartments]:
    return session.scalars(
        select(SemesterDepartments)
        .where(SemesterDepartments.semester_id == semester_id)
        .options(selectinload(SemesterDepartments.department))
        .order_by(SemesterDepartments.department_id)
    ).all()


def get_semester_department(session: Session, semester_id: str, department_id: int) -> SemesterDepartments | None:
    return session.get(SemesterDepartments, (semester_id, department_id))


def lock_semester_department(session: Session, semester_id: str, department_id: int) -> SemesterDepartments | None:
    """Serialize roster writes for one department in one semester on their parent row.

    ``populate_existing`` refreshes an object an earlier read already loaded,
    and a locking read also bypasses an older REPEATABLE READ snapshot.
    """
    return session.scalar(
        select(SemesterDepartments)
        .where(SemesterDepartments.semester_id == semester_id, SemesterDepartments.department_id == department_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def get_memberships(session: Session, semester_id: str, department_id: int | None = None) -> Sequence[ClubMemberships]:
    statement = (
        select(ClubMemberships)
        .join(ClubRoles, ClubRoles.id == ClubMemberships.role_id)
        .where(ClubMemberships.semester_id == semester_id)
        .options(selectinload(ClubMemberships.member), selectinload(ClubMemberships.role))
        .order_by(ClubMemberships.department_id, ClubRoles.sort_order, ClubMemberships.created_at)
    )
    if department_id is not None:
        statement = statement.where(ClubMemberships.department_id == department_id)
    return session.scalars(statement).all()


def get_member_roles(
    session: Session, semester_id: str, department_id: int, member_id: int
) -> Sequence[ClubMemberships]:
    """Every role one person holds in one department.

    A locking read: under REPEATABLE READ a plain SELECT would answer from the
    transaction's first snapshot and miss a concurrent, already committed change.
    """
    return session.scalars(
        select(ClubMemberships)
        .where(
            ClubMemberships.semester_id == semester_id,
            ClubMemberships.department_id == department_id,
            ClubMemberships.member_id == member_id,
        )
        .options(selectinload(ClubMemberships.role))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def get_role_holders(session: Session, semester_id: str, department_id: int, role_id: str) -> Sequence[ClubMemberships]:
    """Who holds a role, as a locking read for the same reason as ``get_member_roles``."""
    return session.scalars(
        select(ClubMemberships)
        .where(
            ClubMemberships.semester_id == semester_id,
            ClubMemberships.department_id == department_id,
            ClubMemberships.role_id == role_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def count_semester_people(session: Session, semester_id: str) -> int:
    """Each person once, across every department of the semester."""
    return (
        session.scalar(
            select(func.count(ClubMemberships.member_id.distinct())).where(ClubMemberships.semester_id == semester_id)
        )
        or 0
    )


def semester_has_memberships(session: Session, semester_id: str) -> bool:
    return bool(session.scalar(select(exists().where(ClubMemberships.semester_id == semester_id))))


def department_has_memberships(session: Session, semester_id: str, department_id: int) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    ClubMemberships.semester_id == semester_id, ClubMemberships.department_id == department_id
                )
            )
        )
    )


def department_has_points(session: Session, semester_id: str, department_id: int) -> bool:
    """Whether the department earned points on a non-draft event of the semester."""
    return bool(
        session.scalar(
            select(
                exists()
                .where(DepartmentsLogs.department_id == department_id)
                .where(Logs.id == DepartmentsLogs.log_id)
                .where(Events.id == Logs.event_id)
                .where(Events.semester_id == semester_id, Events.status != EventsStatus.DRAFT)
            )
        )
    )


def get_changes(
    session: Session,
    *,
    semester_id: str | None = None,
    department_id: int | None = None,
    member_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[ClubMembershipChanges]:
    """Newest first. The id breaks timestamp ties so paging is deterministic."""
    statement = select(ClubMembershipChanges).options(
        selectinload(ClubMembershipChanges.member),
        selectinload(ClubMembershipChanges.role),
        selectinload(ClubMembershipChanges.department),
    )
    if semester_id is not None:
        statement = statement.where(ClubMembershipChanges.semester_id == semester_id)
    if department_id is not None:
        statement = statement.where(ClubMembershipChanges.department_id == department_id)
    if member_id is not None:
        statement = statement.where(ClubMembershipChanges.member_id == member_id)
    return session.scalars(
        statement.order_by(ClubMembershipChanges.created_at.desc(), ClubMembershipChanges.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
