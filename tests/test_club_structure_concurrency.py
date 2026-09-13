"""Real independent MySQL transactions, including stale REPEATABLE READ snapshots."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.DB import club_assignments as queries
from app.DB.schema import ClubAssignmentRole, ClubAssignments, Departments, DepartmentsType, Members
from app.exceptions import ClubStructureConflict
from app.services import club_structure as service
from tests.factories import make_member


@pytest.fixture
def committed_club(engine):
    """Only this fixture's rows are committed/removed; never share db_bind across threads."""
    suffix = uuid4().hex[:12]
    with Session(engine) as session:
        department = Departments(name=f"Concurrent {suffix}", ar_name="قسم", type=DepartmentsType.PRACTICAL)
        members = [
            Members(**make_member(name=f"Concurrent {n}", uni_id=f"{suffix}-{n}", email=f"{suffix}-{n}@example.com"))
            for n in range(3)
        ]
        session.add_all([department, *members])
        session.flush()
        refs = SimpleNamespace(department_id=department.id, member_ids=[member.id for member in members])
        session.commit()
    try:
        yield refs
    finally:
        with Session(engine) as session:
            session.execute(delete(ClubAssignments).where(ClubAssignments.member_id.in_(refs.member_ids)))
            session.execute(delete(Departments).where(Departments.id == refs.department_id))
            session.execute(delete(Members).where(Members.id.in_(refs.member_ids)))
            session.commit()


def compete(engine, refs, first, second, *, expected_conflicts=1):
    barrier = Barrier(2)

    def run(operation):
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            # Keep the object alive in the identity map as a prior overview
            # request would, and establish a consistent-read snapshot.
            cached_department = session.get(Departments, refs.department_id)
            queries.get_department_roster(session, refs.department_id)
            barrier.wait(timeout=10)
            try:
                result = operation(session)
                identifier = result.id if result is not None else None
                session.commit()
                assert cached_department is not None
                return identifier
            except ClubStructureConflict as exc:
                session.rollback()
                return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, operation) for operation in (first, second)]
        results = [future.result(timeout=15) for future in futures]
    assert sum(isinstance(result, ClubStructureConflict) for result in results) == expected_conflicts
    return results


def appoint(session, refs, member_id, expected=None):
    return service.replace_department_leadership(
        session,
        refs.department_id,
        ClubAssignmentRole.LEADER,
        member_id,
        expected_assignment_id=expected,
        changed_by=f"clerk_{member_id}",
    )


@pytest.mark.parametrize("occupied", [False, True])
def test_only_one_concurrent_leader_replacement_succeeds(engine, committed_club, occupied):
    refs = committed_club
    old_id = None
    if occupied:
        with Session(engine) as session:
            old_id = appoint(session, refs, refs.member_ids[0]).id
            for member_id in refs.member_ids[1:]:
                service.add_department_member(session, refs.department_id, member_id, changed_by="clerk_setup")
            session.commit()

    compete(
        engine,
        refs,
        lambda session: appoint(session, refs, refs.member_ids[1], old_id),
        lambda session: appoint(session, refs, refs.member_ids[2], old_id),
    )
    with Session(engine) as session:
        roster = queries.get_department_roster(session, refs.department_id)
        assert len([a for a in roster if a.role == ClubAssignmentRole.LEADER]) == 1
        assert len(roster) == (3 if occupied else 1)
        assert len(queries.get_tenure_history(session, department_id=refs.department_id)) == (5 if occupied else 1)
        if occupied:
            former_holder = next(a for a in roster if a.member_id == refs.member_ids[0])
            assert former_holder.role == ClubAssignmentRole.MEMBER
            assert session.get(ClubAssignments, old_id).ends_at == former_holder.starts_at


def test_competing_membership_additions_do_not_duplicate_roster(engine, committed_club):
    refs = committed_club

    def add(session):
        return service.add_department_member(session, refs.department_id, refs.member_ids[0], changed_by="clerk_admin")

    compete(engine, refs, add, add)
    with Session(engine) as session:
        assert len(queries.get_department_roster(session, refs.department_id)) == 1


@pytest.mark.parametrize("same_member", [False, True])
def test_competing_president_claims_respect_both_unique_rules(engine, committed_club, same_member):
    refs = committed_club
    compete(
        engine,
        refs,
        lambda session: service.replace_president(
            session, 1, refs.member_ids[0], expected_assignment_id=None, changed_by="clerk_first"
        ),
        lambda session: service.replace_president(
            session,
            2 if same_member else 1,
            refs.member_ids[0] if same_member else refs.member_ids[1],
            expected_assignment_id=None,
            changed_by="clerk_second",
        ),
    )
    with Session(engine) as session:
        presidents = [a for a in queries.get_presidents(session) if a.member_id in refs.member_ids]
        assert len(presidents) == 1


def test_competing_president_replacements_cannot_overwrite_the_winner(engine, committed_club):
    refs = committed_club
    with Session(engine) as session:
        old_id = service.replace_president(
            session, 1, refs.member_ids[0], expected_assignment_id=None, changed_by="clerk_setup"
        ).id
        session.commit()
    compete(
        engine,
        refs,
        lambda session: service.replace_president(
            session, 1, refs.member_ids[1], expected_assignment_id=old_id, changed_by="clerk_first"
        ),
        lambda session: service.replace_president(
            session, 1, refs.member_ids[2], expected_assignment_id=old_id, changed_by="clerk_second"
        ),
    )
    with Session(engine) as session:
        history = session.scalars(select(ClubAssignments).where(ClubAssignments.member_id.in_(refs.member_ids))).all()
        assert len(history) == 2
        assert len([a for a in history if a.ends_at is None]) == 1


def test_opposite_president_replacements_handle_deadlock_without_losing_tenure(engine, committed_club, monkeypatch):
    refs = committed_club
    with Session(engine) as session:
        old_ids = [
            service.replace_president(
                session, slot, member_id, expected_assignment_id=None, changed_by="clerk_setup"
            ).id
            for slot, member_id in enumerate(refs.member_ids[:2], start=1)
        ]
        session.commit()

    # Both requests first close their own seat, then try to assign the other
    # seat's holder. Neither is a valid independent replacement. Synchronize
    # the inserts so InnoDB must resolve their competing unique-key locks.
    insert_barrier = Barrier(2)
    original_open = service._open

    def synchronized_open(*args, **kwargs):
        insert_barrier.wait(timeout=10)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(service, "_open", synchronized_open)
    compete(
        engine,
        refs,
        lambda session: service.replace_president(
            session, 1, refs.member_ids[1], expected_assignment_id=old_ids[0], changed_by="clerk_first"
        ),
        lambda session: service.replace_president(
            session, 2, refs.member_ids[0], expected_assignment_id=old_ids[1], changed_by="clerk_second"
        ),
        expected_conflicts=2,
    )
    with Session(engine) as session:
        history = session.scalars(select(ClubAssignments).where(ClubAssignments.member_id.in_(refs.member_ids))).all()
        assert {a.id for a in history} == set(old_ids)
        assert all(a.ends_at is None and a.ended_by is None for a in history)


def test_write_refreshes_a_cached_department_after_another_transaction_archives_it(engine, committed_club):
    refs = committed_club
    with Session(engine) as stale_session:
        cached = stale_session.get(Departments, refs.department_id)
        assert cached.active == 1
        with Session(engine) as archiver:
            service.set_department_active(archiver, refs.department_id, active=False)
            archiver.commit()
        with pytest.raises(ClubStructureConflict, match="archived"):
            service.add_department_member(
                stale_session, refs.department_id, refs.member_ids[0], changed_by="clerk_admin"
            )


def test_archive_holds_department_lock_until_caller_commits(engine, committed_club):
    refs = committed_club
    started = Event()

    def add():
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            started.set()
            with pytest.raises(ClubStructureConflict, match="archived"):
                service.add_department_member(session, refs.department_id, refs.member_ids[0], changed_by="clerk_admin")
            session.rollback()

    with Session(engine) as archiver, ThreadPoolExecutor(max_workers=1) as executor:
        service.set_department_active(archiver, refs.department_id, active=False)
        future = executor.submit(add)
        try:
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        finally:
            # Release the outer transaction even if an assertion fails, so
            # neither the worker nor the test suite can be left waiting.
            archiver.commit()
        future.result(timeout=10)
    with Session(engine) as session:
        assert queries.get_department_roster(session, refs.department_id) == []


def test_caller_rollback_undoes_successful_service_change(engine, committed_club):
    refs = committed_club
    with Session(engine) as session:
        appoint(session, refs, refs.member_ids[0])
        session.rollback()
    with Session(engine) as session:
        assert queries.get_department_roster(session, refs.department_id) == []
