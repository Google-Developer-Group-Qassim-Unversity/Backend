"""Transactional club structure operations; callers commit before responding.

The roster is per semester: ``club_memberships`` holds one row per role a
person holds in a department that semester. The rules kept here:

- A leader or VP is also a member of that department, in its own row. Giving
  someone a role adds their member row if it is missing; removing someone from
  a department removes every role they hold there; taking a role away leaves
  them a member.
- A role's seat limit is ``department_role_limits.max_holders`` for that
  department if set, else ``club_roles.max_holders`` (NULL = unlimited).
  Seats are counted under a lock on the ``semester_departments`` row, so two
  concurrent grants cannot both take the last seat.
- Every added or removed role is appended to ``club_membership_changes``.

Every mutation runs in a savepoint so a failed operation leaves nothing
behind even if the caller catches the error. Clerk subjects come from the
authenticated caller, never from a request body. None of this grants
application permissions.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.DB import club_structure as queries
from app.DB import semesters as semesters_queries
from app.DB.schema import (
    ClubMembershipAction,
    ClubMembershipChanges,
    ClubMemberships,
    ClubRoles,
    Departments,
    DepartmentsType,
    Members,
    SemesterDepartments,
    Semesters,
)
from app.exceptions import ClubStructureConflict, InvalidClubStructure, MemberNotFound, NotFound, SemesterNotFound

MEMBER_ROLE = "member"


class DepartmentSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    ar_name: str = Field(min_length=1, max_length=100)
    type: DepartmentsType
    color: str = Field(default="#4285f4", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str = Field(default="users", min_length=1, max_length=32)
    show_in_leaderboard: bool = True


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
            "uq_club_memberships_member_role": "This person already holds this role. Refresh before trying again.",
            "fk_club_memberships_member": "This member is no longer available. Refresh and choose another member.",
            "fk_club_memberships_semester_department": "This department is not part of this semester any more.",
            "PRIMARY": "This department is already part of this semester.",
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


def _actor(actor: str) -> None:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 255:
        raise InvalidClubStructure("A nonempty Clerk subject of at most 255 characters is required.")


def _semester(session: Session, semester_id: str) -> Semesters:
    semester = semesters_queries.get_semester_by_id(session, semester_id)
    if semester is None:
        raise SemesterNotFound(semester_id)
    return semester


def _department(session: Session, department_id: int) -> Departments:
    department = session.get(Departments, department_id)
    if department is None:
        raise NotFound("Department", department_id)
    return department


def _member(session: Session, member_id: int) -> None:
    if session.scalar(select(Members.id).where(Members.id == member_id)) is None:
        raise MemberNotFound(member_id)


def _role(session: Session, key: str) -> ClubRoles:
    role = queries.get_role_by_key(session, key)
    if role is None:
        raise NotFound("Club role", key)
    return role


def _locked_scope(session: Session, semester_id: str, department_id: int) -> SemesterDepartments:
    scope = queries.lock_semester_department(session, semester_id, department_id)
    if scope is None:
        _semester(session, semester_id)
        _department(session, department_id)
        raise ClubStructureConflict("This department is not part of this semester. Add it to the semester first.")
    return scope


def seat_limit(session: Session, department_id: int, role: ClubRoles) -> int | None:
    limits = queries.get_role_limits(session)
    return limits.get((department_id, role.id), role.max_holders)


def _log(session: Session, row: ClubMemberships, action: ClubMembershipAction, actor: str) -> None:
    session.add(
        ClubMembershipChanges(
            semester_id=row.semester_id,
            department_id=row.department_id,
            member_id=row.member_id,
            role_id=row.role_id,
            action=action,
            actor=actor,
        )
    )


def _add(
    session: Session, semester_id: str, department_id: int, member_id: int, role: ClubRoles, actor: str
) -> ClubMemberships:
    row = ClubMemberships(
        semester_id=semester_id, department_id=department_id, member_id=member_id, role_id=role.id, created_by=actor
    )
    session.add(row)
    _log(session, row, ClubMembershipAction.ADDED, actor)
    return row


def _remove(session: Session, row: ClubMemberships, actor: str) -> None:
    _log(session, row, ClubMembershipAction.REMOVED, actor)
    session.delete(row)


# ---------- departments ----------


def create_department(session: Session, settings: DepartmentSettings, semester_id: str) -> Departments:
    """A new department is part of the semester it was created in."""
    _semester(session, semester_id)
    with _change(session):
        department = Departments(**settings.model_dump())
        session.add(department)
        session.flush()
        session.add(SemesterDepartments(semester_id=semester_id, department_id=department.id))
    return department


def update_department_settings(session: Session, department_id: int, settings: DepartmentSettings) -> Departments:
    with _change(session):
        department = _department(session, department_id)
        for field, value in settings.model_dump().items():
            setattr(department, field, value)
    return department


def set_department_active(session: Session, department_id: int, *, active: bool) -> Departments:
    """Archive/restore. Archived departments are left out when a semester is copied; nothing else changes."""
    with _change(session):
        department = _department(session, department_id)
        department.active = int(active)
    return department


def add_department_to_semester(session: Session, semester_id: str, department_id: int) -> SemesterDepartments:
    _semester(session, semester_id)
    department = _department(session, department_id)
    if not department.active:
        raise ClubStructureConflict("This department is archived. Restore it before adding it to a semester.")
    if queries.get_semester_department(session, semester_id, department_id) is not None:
        raise ClubStructureConflict("This department is already part of this semester.")
    with _change(session):
        scope = SemesterDepartments(semester_id=semester_id, department_id=department_id)
        session.add(scope)
    return scope


def remove_department_from_semester(session: Session, semester_id: str, department_id: int) -> None:
    """Only an empty department with no points that semester can be taken out of it."""
    with _change(session):
        scope = _locked_scope(session, semester_id, department_id)
        if queries.department_has_memberships(session, semester_id, department_id):
            raise ClubStructureConflict("Remove everyone from this department's roster first.")
        if queries.department_has_points(session, semester_id, department_id):
            raise ClubStructureConflict("This department earned points in this semester, so it stays part of it.")
        session.delete(scope)


# ---------- roster ----------


def add_member(
    session: Session, semester_id: str, department_id: int, member_id: int, *, actor: str
) -> ClubMemberships:
    _actor(actor)
    with _change(session):
        _locked_scope(session, semester_id, department_id)
        _member(session, member_id)
        if any(
            row.role.key == MEMBER_ROLE
            for row in queries.get_member_roles(session, semester_id, department_id, member_id)
        ):
            raise ClubStructureConflict("This person is already on the department roster.")
        row = _add(session, semester_id, department_id, member_id, _role(session, MEMBER_ROLE), actor)
    return row


def remove_member(session: Session, semester_id: str, department_id: int, member_id: int, *, actor: str) -> int:
    """Take someone out of a department, with every role they hold there. Returns how many rows went."""
    _actor(actor)
    with _change(session):
        _locked_scope(session, semester_id, department_id)
        rows = queries.get_member_roles(session, semester_id, department_id, member_id)
        if not rows:
            raise ClubStructureConflict("This person is no longer on the department roster.")
        for row in rows:
            _remove(session, row, actor)
    return len(rows)


def grant_role(
    session: Session,
    semester_id: str,
    department_id: int,
    member_id: int,
    role_key: str,
    *,
    actor: str,
    replaces_member_id: int | None = None,
) -> ClubMemberships:
    """Give someone a leader/VP role; they also become a member if they were not one.

    When every seat is taken, ``replaces_member_id`` names the holder to take
    the role from in the same transaction (they stay a member). Without it
    the grant is refused, so a stale screen cannot silently replace someone.
    """
    _actor(actor)
    if role_key == MEMBER_ROLE:
        raise InvalidClubStructure("Use the roster to add members.")
    with _change(session):
        _locked_scope(session, semester_id, department_id)
        _member(session, member_id)
        role = _role(session, role_key)
        held = {row.role.key: row for row in queries.get_member_roles(session, semester_id, department_id, member_id)}
        if role_key in held:
            return held[role_key]

        holders = queries.get_role_holders(session, semester_id, department_id, role.id)
        if replaces_member_id is not None:
            replaced = next((row for row in holders if row.member_id == replaces_member_id), None)
            if replaced is None:
                raise ClubStructureConflict("The person being replaced no longer holds this role. Refresh first.")
            _remove(session, replaced, actor)
            holders = [row for row in holders if row is not replaced]
        limit = seat_limit(session, department_id, role)
        if limit is not None and len(holders) >= limit:
            raise ClubStructureConflict(
                f"Every {role.name} seat in this department is taken. Replace or remove a holder first."
            )

        if MEMBER_ROLE not in held:
            _add(session, semester_id, department_id, member_id, _role(session, MEMBER_ROLE), actor)
        row = _add(session, semester_id, department_id, member_id, role, actor)
    return row


def revoke_role(
    session: Session, semester_id: str, department_id: int, member_id: int, role_key: str, *, actor: str
) -> None:
    """Take a leader/VP role away. The person stays a member of the department."""
    _actor(actor)
    if role_key == MEMBER_ROLE:
        raise InvalidClubStructure("Remove the person from the roster instead.")
    with _change(session):
        _locked_scope(session, semester_id, department_id)
        row = next(
            (
                r
                for r in queries.get_member_roles(session, semester_id, department_id, member_id)
                if r.role.key == role_key
            ),
            None,
        )
        if row is None:
            raise ClubStructureConflict("This person no longer holds this role. Refresh before trying again.")
        _remove(session, row, actor)


def copy_semester(session: Session, target_id: str, source_id: str, *, actor: str) -> int:
    """Start a semester from another one's structure. Returns how many roster rows were copied.

    Only into a semester with an empty roster, so nothing already there is
    overwritten. Archived departments are skipped. Names come across as the
    departments' current names, not the old term's.
    """
    _actor(actor)
    if target_id == source_id:
        raise InvalidClubStructure("Choose a different semester to copy from.")
    _semester(session, target_id)
    _semester(session, source_id)
    if queries.semester_has_memberships(session, target_id):
        raise ClubStructureConflict("This semester already has a roster. Copying is only for an empty semester.")

    copied = 0
    with _change(session):
        existing = {scope.department_id for scope in queries.get_semester_departments(session, target_id)}
        copied_departments = set()
        for source in queries.get_semester_departments(session, source_id):
            if not source.department.active:
                continue
            if source.department_id not in existing:
                session.add(SemesterDepartments(semester_id=target_id, department_id=source.department_id))
            copied_departments.add(source.department_id)
        session.flush()
        for row in queries.get_memberships(session, source_id):
            if row.department_id in copied_departments:
                _add(session, target_id, row.department_id, row.member_id, row.role, actor)
                copied += 1
    return copied
