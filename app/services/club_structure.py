"""Transactional club structure operations; callers commit before responding.

Every mutation uses a savepoint so a failed replacement restores its former
periods even if the caller catches the error. Successful operations retain
their row locks until the caller commits or rolls back the outer transaction.
Clerk subjects come from the authenticated caller, never from a request body.
These services do not grant application permissions.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.DB import club_assignments as queries
from app.DB.schema import ClubAssignmentRole, ClubAssignments, Departments, DepartmentsType, Members
from app.exceptions import ClubStructureConflict, InvalidClubStructure, MemberNotFound, NotFound


class DepartmentSettings(BaseModel):
    """Editable settings. The migrated leadership capability is preserved."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    ar_name: str = Field(min_length=1, max_length=100)
    type: DepartmentsType
    color: str = Field(default="#4285f4", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="users", min_length=1, max_length=32)


def _is_lock_conflict(exc: OperationalError) -> bool:
    # InnoDB removes every savepoint when it aborts a deadlocked transaction.
    # SQLAlchemy's subsequent rollback can surface error 1305 instead of 1213;
    # only treat it as a conflict when the original lock error is in the chain.
    error: BaseException | None = exc
    while error is not None:
        if isinstance(error, OperationalError) and error.orig is not None:
            if error.orig.args and error.orig.args[0] in (1205, 1213):
                return True
        error = error.__context__
    return False


@contextmanager
def _change(session: Session) -> Iterator[None]:
    try:
        with session.begin_nested():
            yield
            session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        conflicts = {
            "uq_club_assignments_current_president": "This President slot is occupied. Refresh before replacing it.",
            "uq_club_assignments_current_leader": "This leadership seat is occupied. Refresh before replacing it.",
            "uq_club_assignments_current_member": "This person already holds a current role in this scope.",
            "fk_club_assignments_member": "This member is no longer available. Refresh and choose another member.",
            "fk_club_assignments_department": "This department is no longer available. Refresh before making changes.",
        }
        for constraint, detail in conflicts.items():
            if constraint in message:
                raise ClubStructureConflict(detail) from exc
        raise
    except OperationalError as exc:
        if _is_lock_conflict(exc):
            # InnoDB can abort the entire transaction on a deadlock, including
            # the savepoint. The caller must roll back and retry the request.
            session.rollback()
            raise ClubStructureConflict("Another club structure change is in progress. Refresh and retry.") from exc
        raise


def _actor(changed_by: str) -> None:
    if not isinstance(changed_by, str) or not changed_by.strip() or len(changed_by) > 255:
        raise InvalidClubStructure("A nonempty Clerk subject of at most 255 characters is required.")


def _department(session: Session, department_id: int, *, require_active: bool = True) -> Departments:
    department = queries.lock_department(session, department_id)
    if department is None:
        raise NotFound("Department", department_id)
    if require_active and not department.active:
        raise ClubStructureConflict("This department is archived. Restore it before changing its roster.")
    return department


def _member(session: Session, member_id: int) -> None:
    if session.scalar(select(Members.id).where(Members.id == member_id)) is None:
        raise MemberNotFound(member_id)


def _expect(current: ClubAssignments | None, expected_assignment_id: int | None) -> None:
    if (current.id if current else None) != expected_assignment_id:
        raise ClubStructureConflict("This assignment has changed. Refresh before trying again.")


def _now() -> datetime:
    # MySQL DATETIME(6) stores no timezone; all values here are UTC.
    return datetime.now(UTC).replace(tzinfo=None)


def _close(assignment: ClubAssignments, at: datetime, changed_by: str) -> None:
    if at < assignment.starts_at:
        raise ClubStructureConflict("The current tenure starts in the future and cannot be ended yet.")
    assignment.ends_at = at
    assignment.ended_by = changed_by


def _open(
    session: Session,
    member_id: int,
    department_id: int | None,
    role: ClubAssignmentRole,
    at: datetime,
    changed_by: str,
    president_slot: int | None = None,
) -> ClubAssignments:
    assignment = ClubAssignments(
        member_id=member_id,
        department_id=department_id,
        role=role,
        president_slot=president_slot,
        starts_at=at,
        changed_by=changed_by,
    )
    session.add(assignment)
    return assignment


def create_department(session: Session, settings: DepartmentSettings) -> Departments:
    with _change(session):
        department = Departments(**settings.model_dump())
        session.add(department)
    return department


def update_department_settings(session: Session, department_id: int, settings: DepartmentSettings) -> Departments:
    with _change(session):
        department = _department(session, department_id, require_active=False)
        for field, value in settings.model_dump().items():
            setattr(department, field, value)
    return department


def set_department_active(session: Session, department_id: int, *, active: bool) -> Departments:
    """Archive/restore without changing assignments, tenure, or points."""
    with _change(session):
        department = _department(session, department_id, require_active=False)
        department.active = int(active)
    return department


def add_department_member(session: Session, department_id: int, member_id: int, *, changed_by: str) -> ClubAssignments:
    return add_department_members(session, department_id, [member_id], changed_by=changed_by)[0]


def add_department_members(
    session: Session, department_id: int, member_ids: Sequence[int], *, changed_by: str
) -> list[ClubAssignments]:
    """Add a roster selection atomically, using one timestamp for the batch."""
    _actor(changed_by)
    if not member_ids:
        raise InvalidClubStructure("At least one member is required.")
    if len(member_ids) != len(set(member_ids)):
        raise InvalidClubStructure("A member can only appear once in a roster batch.")
    with _change(session):
        _department(session, department_id)
        at = _now()
        assignments = []
        for member_id in member_ids:
            _member(session, member_id)
            if queries.lock_member_assignment(session, department_id, member_id) is not None:
                detail = (
                    "This person is already on the department roster."
                    if len(member_ids) == 1
                    else "One or more selected people are already on the department roster."
                )
                raise ClubStructureConflict(detail)
            assignments.append(_open(session, member_id, department_id, ClubAssignmentRole.MEMBER, at, changed_by))
    return assignments


def remove_department_member(
    session: Session, department_id: int, member_id: int, *, expected_assignment_id: int, changed_by: str
) -> ClubAssignments:
    """Leave the department entirely, including any leadership seat held there."""
    _actor(changed_by)
    with _change(session):
        _department(session, department_id)
        current = queries.lock_member_assignment(session, department_id, member_id)
        _expect(current, expected_assignment_id)
        if current is None:
            raise ClubStructureConflict("This person is no longer on the department roster.")
        _close(current, _now(), changed_by)
    return current


def replace_department_leadership(
    session: Session,
    department_id: int,
    role: ClubAssignmentRole,
    member_id: int | None,
    *,
    expected_assignment_id: int | None,
    changed_by: str,
) -> ClubAssignments | None:
    """Fill, replace, or clear a seat; the former holder remains a regular member.

    None as the expected ID means the caller saw a vacant seat. None as the
    member means clear the seat. Moving a deputy to leader (or vice versa)
    requires clearing their other seat explicitly first.
    """
    _actor(changed_by)
    if role not in (ClubAssignmentRole.LEADER, ClubAssignmentRole.DEPUTY):
        raise InvalidClubStructure("Department leadership must be leader or deputy.")
    role = ClubAssignmentRole(role)
    with _change(session):
        department = _department(session, department_id)
        if not department.leadership_enabled:
            raise ClubStructureConflict("Leadership is disabled for this department.")
        current = queries.lock_leadership_assignment(session, department_id, role)
        _expect(current, expected_assignment_id)
        if current is not None and current.member_id == member_id:
            return current
        incoming = None
        if member_id is not None:
            _member(session, member_id)
            incoming = queries.lock_member_assignment(session, department_id, member_id)
            if incoming is not None and incoming.role != ClubAssignmentRole.MEMBER:
                raise ClubStructureConflict("This person holds the other leadership seat. Clear that seat first.")
        at = _now()
        if current is not None:
            _close(current, at, changed_by)
        if incoming is not None:
            _close(incoming, at, changed_by)
        # Free the generated unique keys before inserting either new period.
        session.flush()
        if current is not None:
            _open(session, current.member_id, department_id, ClubAssignmentRole.MEMBER, at, changed_by)
        assignment = _open(session, member_id, department_id, role, at, changed_by) if member_id is not None else None
    return assignment


def replace_president(
    session: Session, slot: int, member_id: int | None, *, expected_assignment_id: int | None, changed_by: str
) -> ClubAssignments | None:
    """Operate on one equal seat; Board membership is independent.

    There is no parent club row to lock. For an occupied seat, lock and verify
    the exact expected period. For a vacant seat, attempt the insert and let
    the unique index arbitrate competing claims; do not gap-lock empty slots.
    """
    _actor(changed_by)
    if type(slot) is not int or slot not in (1, 2):
        raise InvalidClubStructure("President slot must be 1 or 2.")
    if expected_assignment_id is None and member_id is None:
        raise InvalidClubStructure("An expected assignment is required to clear a President slot.")
    with _change(session):
        current = None
        if expected_assignment_id is not None:
            current = queries.lock_assignment(session, expected_assignment_id)
            if (
                current is None
                or current.ends_at is not None
                or current.role != ClubAssignmentRole.PRESIDENT
                or current.president_slot != slot
            ):
                raise ClubStructureConflict("This President slot has changed. Refresh before trying again.")
            if current.member_id == member_id:
                return current
        if member_id is not None:
            _member(session, member_id)
        at = _now()
        if current is not None:
            _close(current, at, changed_by)
            session.flush()
        assignment = (
            _open(session, member_id, None, ClubAssignmentRole.PRESIDENT, at, changed_by, slot)
            if member_id is not None
            else None
        )
    return assignment
