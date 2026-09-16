"""Exercise the assignment constraints against the migrated MySQL database."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.DB.schema import ClubAssignments, Departments, DepartmentsType, Members, Role
from tests.factories import make_member


def assign(session, member_id, department_id=None, role="member", **values):
    assignment = ClubAssignments(
        member_id=member_id, department_id=department_id, role=role, changed_by="clerk_super_admin", **values
    )
    session.add(assignment)
    session.flush()
    return assignment


def test_department_defaults_and_creation_date(db_session):
    department = Departments(name="New Department", ar_name="قسم جديد", type=DepartmentsType.ADMINISTRATIVE)
    db_session.add(department)
    db_session.flush()
    db_session.refresh(department)

    assert department.leadership_enabled == 1
    assert department.active == 1
    assert department.color == "#4285f4"
    assert department.icon == "users"
    assert department.created_at is not None
    assert department.updated_at is not None


def test_two_equal_presidents_can_also_be_board_members(db_session, seed_refs):
    board = Departments(
        name="Board of Directors", ar_name="مجلس الإدارة", type=DepartmentsType.ADMINISTRATIVE, leadership_enabled=0
    )
    db_session.add(board)
    db_session.flush()

    for slot, member in enumerate((seed_refs.ahmed, seed_refs.sara), start=1):
        assign(db_session, member.id, role="president", president_slot=slot)
        assign(db_session, member.id, department_id=board.id)

    assert len(db_session.scalars(select(ClubAssignments)).all()) == 4
    assert db_session.scalars(select(Role)).all() == []


@pytest.mark.parametrize("slot", [1, 2])
def test_president_slot_cannot_be_occupied_twice(db_session, seed_refs, slot):
    assign(db_session, seed_refs.ahmed.id, role="president", president_slot=slot)
    with pytest.raises(IntegrityError, match="uq_club_assignments_current_president"):
        assign(db_session, seed_refs.sara.id, role="president", president_slot=slot)


def test_same_person_cannot_occupy_both_president_slots(db_session, seed_refs):
    assign(db_session, seed_refs.ahmed.id, role="president", president_slot=1)
    with pytest.raises(IntegrityError, match="uq_club_assignments_current_member"):
        assign(db_session, seed_refs.ahmed.id, role="president", president_slot=2)


@pytest.mark.parametrize("role", ["leader", "deputy"])
def test_department_has_only_one_current_holder_of_each_leadership_role(db_session, seed_refs, role):
    assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id, role)
    with pytest.raises(IntegrityError, match="uq_club_assignments_current_leader"):
        assign(db_session, seed_refs.sara.id, seed_refs.dept_design.id, role)


@pytest.mark.parametrize("role", ["member", "leader", "deputy"])
def test_member_cannot_have_two_current_department_roles(db_session, seed_refs, role):
    assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id)
    with pytest.raises(IntegrityError, match="uq_club_assignments_current_member"):
        assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id, role)


def test_multiple_members_and_membership_in_multiple_departments(db_session, seed_refs):
    assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id)
    assign(db_session, seed_refs.sara.id, seed_refs.dept_design.id)
    assign(db_session, seed_refs.ahmed.id, seed_refs.dept_business.id)
    assert len(db_session.scalars(select(ClubAssignments)).all()) == 3


@pytest.mark.parametrize(
    "role,with_department,slot",
    [
        ("president", False, None),
        ("president", False, 0),
        ("president", False, 3),
        ("president", True, 1),
        ("member", False, None),
        ("leader", False, None),
        ("deputy", False, None),
        ("member", True, 1),
    ],
)
def test_invalid_role_scope_is_rejected_by_database(db_session, seed_refs, role, with_department, slot):
    # MySQL CHECK failures are reported as OperationalError by PyMySQL.
    with pytest.raises(OperationalError, match="ck_club_assignments_scope"):
        assign(
            db_session,
            seed_refs.ahmed.id,
            seed_refs.dept_design.id if with_department else None,
            role,
            president_slot=slot,
        )


def test_replacement_retains_tenure_and_frees_current_keys(db_session, seed_refs):
    start = datetime(2026, 9, 1)
    replacement = start + timedelta(days=10)
    former_leader = assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id, "leader", starts_at=start)
    former_leader.ends_at = replacement
    former_leader.ended_by = "clerk_replacing_admin"
    db_session.flush()

    assign(db_session, seed_refs.ahmed.id, seed_refs.dept_design.id, starts_at=replacement)
    assign(db_session, seed_refs.sara.id, seed_refs.dept_design.id, "leader", starts_at=replacement)
    db_session.refresh(former_leader)

    assert former_leader.starts_at == start
    assert former_leader.ends_at == replacement
    assert former_leader.changed_by == "clerk_super_admin"
    assert former_leader.ended_by == "clerk_replacing_admin"
    assert former_leader.current_scope_id is None
    assert former_leader.current_leadership_role is None
    assert len(db_session.scalars(select(ClubAssignments)).all()) == 3


def test_president_can_return_to_a_previously_held_slot(db_session, seed_refs):
    for day in (1, 5):
        start = datetime(2026, 9, day)
        assign(
            db_session,
            seed_refs.ahmed.id,
            role="president",
            president_slot=1,
            starts_at=start,
            ends_at=start + timedelta(days=1),
            ended_by="clerk_super_admin",
        )
    assign(db_session, seed_refs.ahmed.id, role="president", president_slot=1)
    assert len(db_session.scalars(select(ClubAssignments)).all()) == 3


def test_end_cannot_precede_start(db_session, seed_refs):
    with pytest.raises(OperationalError, match="ck_club_assignments_period"):
        assign(
            db_session,
            seed_refs.ahmed.id,
            seed_refs.dept_design.id,
            starts_at=datetime(2026, 9, 2),
            ends_at=datetime(2026, 9, 1),
        )


@pytest.mark.parametrize("target", ["member", "department"])
def test_referenced_records_cannot_be_deleted_even_after_tenure_ends(db_session, seed_refs, target):
    member = Members(**make_member())
    db_session.add(member)
    db_session.flush()
    assign(
        db_session, member.id, seed_refs.dept_design.id, starts_at=datetime(2026, 9, 1), ends_at=datetime(2026, 9, 2)
    )
    model, row_id = (Members, member.id) if target == "member" else (Departments, seed_refs.dept_design.id)
    with pytest.raises(IntegrityError, match="fk_club_assignments_"):
        db_session.execute(delete(model).where(model.id == row_id))
