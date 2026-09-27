"""Real independent MySQL transactions competing for the same roster, including stale snapshots."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.DB import club_structure as queries
from app.DB.schema import (
    ClubMembershipChanges,
    ClubMemberships,
    Departments,
    DepartmentsType,
    Members,
    SemesterDepartments,
    Semesters,
)
from app.exceptions import ClubStructureConflict
from app.services import club_structure as service
from tests.factories import make_member


@pytest.fixture
def committed_club(engine):
    """Only this fixture's rows are committed/removed; never share db_bind across threads."""
    suffix = uuid4().hex[:12]
    with Session(engine) as session:
        semester_id = session.scalar(select(Semesters.id).where(Semesters.hijri_code == 475))
        department = Departments(name=f"Concurrent {suffix}", ar_name="قسم", type=DepartmentsType.PRACTICAL)
        members = [
            Members(**make_member(name=f"Concurrent {n}", uni_id=f"{suffix}-{n}", email=f"{suffix}-{n}@example.com"))
            for n in range(3)
        ]
        session.add_all([department, *members])
        session.flush()
        session.add(SemesterDepartments(semester_id=semester_id, department_id=department.id))
        refs = SimpleNamespace(
            semester_id=semester_id, department_id=department.id, member_ids=[member.id for member in members]
        )
        session.commit()
    try:
        yield refs
    finally:
        with Session(engine) as session:
            for table in (ClubMembershipChanges, ClubMemberships, SemesterDepartments):
                session.execute(delete(table).where(table.department_id == refs.department_id))
            session.execute(delete(Departments).where(Departments.id == refs.department_id))
            session.execute(delete(Members).where(Members.id.in_(refs.member_ids)))
            session.commit()


def roster(session, refs):
    return sorted(
        (row.member_id, row.role.key) for row in queries.get_memberships(session, refs.semester_id, refs.department_id)
    )


def compete(engine, refs, first, second, *, expected_conflicts=1):
    barrier = Barrier(2)

    def run(operation):
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            # Establish a consistent-read snapshot, as a prior overview request would.
            roster(session, refs)
            barrier.wait(timeout=10)
            try:
                result = operation(session)
                session.commit()
                return result
            except ClubStructureConflict as exc:
                session.rollback()
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, operation) for operation in (first, second)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(isinstance(result, ClubStructureConflict) for result in results) == expected_conflicts
    return results


def appoint(session, refs, member_id, replaces=None):
    return service.grant_role(
        session,
        refs.semester_id,
        refs.department_id,
        member_id,
        "leader",
        actor=f"clerk_{member_id}",
        replaces_member_id=replaces,
    )


def test_only_one_concurrent_claim_on_a_vacant_seat_succeeds(engine, committed_club):
    refs = committed_club
    first, second = refs.member_ids[1], refs.member_ids[2]
    compete(engine, refs, lambda s: appoint(s, refs, first), lambda s: appoint(s, refs, second))
    with Session(engine) as session:
        leaders = [member for member, role in roster(session, refs) if role == "leader"]
        assert len(leaders) == 1
        # The winner's member row came with the seat; the loser left nothing behind.
        assert roster(session, refs) == [(leaders[0], "leader"), (leaders[0], "member")]


def test_only_one_concurrent_replacement_of_the_same_holder_succeeds(engine, committed_club):
    refs = committed_club
    holder = refs.member_ids[0]
    with Session(engine) as session:
        appoint(session, refs, holder)
        session.commit()

    compete(
        engine,
        refs,
        lambda s: appoint(s, refs, refs.member_ids[1], replaces=holder),
        lambda s: appoint(s, refs, refs.member_ids[2], replaces=holder),
    )
    with Session(engine) as session:
        rows = roster(session, refs)
        assert len([member for member, role in rows if role == "leader"]) == 1
        # The replaced holder stays a member.
        assert (holder, "member") in rows and (holder, "leader") not in rows


def test_competing_member_additions_do_not_duplicate_the_roster(engine, committed_club):
    refs = committed_club

    def add(session):
        return service.add_member(
            session, refs.semester_id, refs.department_id, refs.member_ids[0], actor="clerk_admin"
        )

    compete(engine, refs, add, add)
    with Session(engine) as session:
        assert roster(session, refs) == [(refs.member_ids[0], "member")]


def test_a_grant_holds_the_department_lock_until_the_caller_commits(engine, committed_club):
    refs = committed_club
    started = Event()

    def claim():
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            started.set()
            with pytest.raises(ClubStructureConflict, match="taken"):
                appoint(session, refs, refs.member_ids[1])
            session.rollback()

    with Session(engine) as first, ThreadPoolExecutor(max_workers=1) as executor:
        appoint(first, refs, refs.member_ids[0])
        future = executor.submit(claim)
        try:
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            # Release the lock even if an assertion fails, so neither the
            # worker nor the test suite can be left waiting.
            first.commit()
        future.result(timeout=10)
    with Session(engine) as session:
        assert roster(session, refs) == [(refs.member_ids[0], "leader"), (refs.member_ids[0], "member")]


def test_caller_rollback_undoes_a_successful_service_change(engine, committed_club):
    refs = committed_club
    with Session(engine) as session:
        appoint(session, refs, refs.member_ids[0])
        session.rollback()
    with Session(engine) as session:
        assert roster(session, refs) == []
        assert queries.get_changes(session, department_id=refs.department_id) == []
