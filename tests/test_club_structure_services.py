"""Service invariants against the migrated MySQL schema (no API in Step 2)."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select

from app.DB import club_assignments as queries
from app.DB.schema import ClubAssignmentRole as RoleType, ClubAssignments, Departments, DepartmentsType, Role
from app.exceptions import ClubStructureConflict, InvalidClubStructure, MemberNotFound, NotFound
from app.services import club_structure as service

ACTOR = "clerk_assigning_admin"
REPLACER = "clerk_replacing_admin"


def add(session, department_id, member_id):
    return service.add_department_member(session, department_id, member_id, changed_by=ACTOR)


def leadership(session, department_id, member_id, *, role=RoleType.LEADER, expected=None):
    return service.replace_department_leadership(
        session, department_id, role, member_id, expected_assignment_id=expected, changed_by=REPLACER
    )


def president(session, member_id, *, slot=1, expected=None):
    return service.replace_president(session, slot, member_id, expected_assignment_id=expected, changed_by=REPLACER)


def test_rosters_start_empty_and_multiple_departments_count_people_once(db_session, seed_refs):
    design, business = seed_refs.dept_design.id, seed_refs.dept_business.id
    assert queries.count_current_members(db_session) == 0
    assert queries.get_department_member_counts(db_session) == {business: 0, design: 0}
    add(db_session, design, seed_refs.ahmed.id)
    add(db_session, business, seed_refs.ahmed.id)
    leadership(db_session, design, seed_refs.sara.id, role=RoleType.DEPUTY)
    president(db_session, seed_refs.ahmed.id)

    assert queries.get_department_member_counts(db_session) == {business: 1, design: 2}
    assert queries.count_current_members(db_session) == 2
    roster = queries.get_department_roster(db_session, design)
    assert {a.member.name for a in roster} == {seed_refs.ahmed.name, seed_refs.sara.name}
    assert {a.role for a in roster} == {RoleType.MEMBER, RoleType.DEPUTY}
    assert db_session.scalars(select(Role)).all() == []


@pytest.mark.parametrize("role", [RoleType.MEMBER, RoleType.LEADER, RoleType.DEPUTY])
def test_add_rejects_every_existing_department_role(db_session, seed_refs, role):
    department, member = seed_refs.dept_design.id, seed_refs.ahmed.id
    if role == RoleType.MEMBER:
        add(db_session, department, member)
    else:
        leadership(db_session, department, member, role=role)
    with pytest.raises(ClubStructureConflict, match="already on"):
        add(db_session, department, member)
    assert len(queries.get_tenure_history(db_session, department_id=department)) == 1


@pytest.mark.parametrize("role", [RoleType.LEADER, RoleType.DEPUTY])
def test_replacement_demotes_old_holder_and_promotes_member_at_one_instant(db_session, seed_refs, monkeypatch, role):
    department = seed_refs.dept_design.id
    start = datetime(2026, 1, 1, 12, 30, 1, 123456)
    replacement_time = start + timedelta(days=1, microseconds=654321)
    monkeypatch.setattr(service, "_now", lambda: start)
    old = leadership(db_session, department, seed_refs.ahmed.id, role=role)
    incoming = add(db_session, department, seed_refs.sara.id)
    monkeypatch.setattr(service, "_now", lambda: replacement_time)

    replacement = leadership(db_session, department, seed_refs.sara.id, role=role, expected=old.id)
    db_session.expire_all()
    roster = {a.member_id: a for a in queries.get_department_roster(db_session, department)}
    assert len(roster) == 2
    assert roster[seed_refs.ahmed.id].role == RoleType.MEMBER
    assert roster[seed_refs.sara.id].id == replacement.id
    assert roster[seed_refs.sara.id].role == role
    assert old.ends_at == incoming.ends_at == replacement.starts_at == replacement_time
    assert roster[seed_refs.ahmed.id].starts_at == replacement_time
    assert old.starts_at == incoming.starts_at == start
    assert old.changed_by == REPLACER
    assert incoming.changed_by == ACTOR
    assert old.ended_by == incoming.ended_by == REPLACER
    assert len(queries.get_tenure_history(db_session, department_id=department)) == 4
    assert db_session.scalars(select(Role)).all() == []


def test_replacement_failure_restores_both_former_periods(db_session, seed_refs):
    department = seed_refs.dept_design.id
    old = leadership(db_session, department, seed_refs.ahmed.id)
    incoming = add(db_session, department, seed_refs.sara.id)

    def fail_new_leader(session, flush_context, instances):
        if any(isinstance(a, ClubAssignments) and a.role == RoleType.LEADER for a in session.new):
            raise RuntimeError("cannot persist new leader")

    event.listen(db_session, "before_flush", fail_new_leader)
    try:
        with pytest.raises(RuntimeError, match="cannot persist"):
            leadership(db_session, department, seed_refs.sara.id, expected=old.id)
    finally:
        event.remove(db_session, "before_flush", fail_new_leader)

    assert old.ends_at is incoming.ends_at is None
    assert old.ended_by is incoming.ended_by is None
    assert len(queries.get_tenure_history(db_session, department_id=department)) == 2
    # The savepoint rolled back the failed operation without losing earlier work.
    assert leadership(db_session, department, seed_refs.sara.id, expected=old.id) is not None


def test_stale_replacement_and_stale_removal_cannot_change_new_tenure(db_session, seed_refs):
    department = seed_refs.dept_design.id
    original = leadership(db_session, department, seed_refs.ahmed.id)
    replacement = leadership(db_session, department, seed_refs.sara.id, expected=original.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        leadership(db_session, department, seed_refs.ahmed.id, expected=original.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        service.remove_department_member(
            db_session, department, seed_refs.ahmed.id, expected_assignment_id=original.id, changed_by=ACTOR
        )
    assert replacement.ends_at is None
    assert len(queries.get_department_roster(db_session, department)) == 2


def test_vacancy_expectation_cannot_overwrite_occupied_leadership(db_session, seed_refs):
    department = seed_refs.dept_design.id
    original = leadership(db_session, department, seed_refs.ahmed.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        leadership(db_session, department, seed_refs.sara.id)
    assert original.ends_at is None


def test_same_holder_is_noop_and_clearing_retains_regular_membership(db_session, seed_refs):
    department = seed_refs.dept_design.id
    original = leadership(db_session, department, seed_refs.ahmed.id)
    assert leadership(db_session, department, seed_refs.ahmed.id, expected=original.id).id == original.id
    assert len(queries.get_tenure_history(db_session, department_id=department)) == 1
    assert leadership(db_session, department, None, expected=original.id) is None
    roster = queries.get_department_roster(db_session, department)
    assert len(roster) == 1
    assert roster[0].member_id == seed_refs.ahmed.id
    assert roster[0].role == RoleType.MEMBER
    assert original.ends_at == roster[0].starts_at


def test_moving_between_leadership_seats_requires_explicit_clear(db_session, seed_refs):
    department = seed_refs.dept_design.id
    deputy = leadership(db_session, department, seed_refs.sara.id, role=RoleType.DEPUTY)
    with pytest.raises(ClubStructureConflict, match="other leadership seat"):
        leadership(db_session, department, seed_refs.sara.id)
    leadership(db_session, department, None, role=RoleType.DEPUTY, expected=deputy.id)
    leader = leadership(db_session, department, seed_refs.sara.id)
    assert leader.role == RoleType.LEADER
    assert len(queries.get_department_roster(db_session, department)) == 1


@pytest.mark.parametrize("role", [RoleType.MEMBER, RoleType.LEADER, RoleType.DEPUTY])
def test_removal_closes_tenure_and_rejoining_creates_new_history(db_session, seed_refs, role):
    department, member = seed_refs.dept_design.id, seed_refs.ahmed.id
    original = (
        add(db_session, department, member)
        if role == RoleType.MEMBER
        else leadership(db_session, department, member, role=role)
    )
    service.remove_department_member(
        db_session, department, member, expected_assignment_id=original.id, changed_by=ACTOR
    )
    assert queries.get_department_roster(db_session, department) == []
    assert original.ended_by == ACTOR
    assert original.ends_at is not None
    new = add(db_session, department, member)
    assert new.id != original.id
    assert new.role == RoleType.MEMBER
    assert len(queries.get_tenure_history(db_session, member_id=member)) == 2


def test_capability_flag_survives_renames_and_presidents_remain_independent(db_session, seed_refs):
    board = Departments(
        name="Renamed Board", ar_name="المجلس", type=DepartmentsType.ADMINISTRATIVE, leadership_enabled=0
    )
    db_session.add(board)
    db_session.flush()
    membership = add(db_session, board.id, seed_refs.ahmed.id)
    first = president(db_session, seed_refs.ahmed.id)
    president(db_session, seed_refs.sara.id, slot=2)
    for role in (RoleType.LEADER, RoleType.DEPUTY):
        with pytest.raises(ClubStructureConflict, match="disabled"):
            leadership(db_session, board.id, seed_refs.ahmed.id, role=role)
    service.update_department_settings(
        db_session,
        board.id,
        service.DepartmentSettings(name="Another name", ar_name="مجلس جديد", type=DepartmentsType.ADMINISTRATIVE),
    )
    assert board.leadership_enabled == 0
    with pytest.raises(ClubStructureConflict, match="disabled"):
        leadership(db_session, board.id, seed_refs.ahmed.id)
    # A matching name alone never disables a department's configured capability.
    seed_refs.dept_design.name = "Board of Directors"
    db_session.flush()
    leadership(db_session, seed_refs.dept_design.id, seed_refs.sara.id)
    president(db_session, None, expected=first.id)
    assert membership.ends_at is None
    assert queries.count_current_members(db_session) == 2


@pytest.mark.parametrize("operation", ["add", "remove", "replace", "clear"])
def test_archived_roster_rejects_all_writes_until_restored(db_session, seed_refs, operation):
    department = seed_refs.dept_design.id
    original = leadership(db_session, department, seed_refs.ahmed.id)
    service.set_department_active(db_session, department, active=False)
    with pytest.raises(ClubStructureConflict, match="archived"):
        if operation == "add":
            add(db_session, department, seed_refs.sara.id)
        elif operation == "remove":
            service.remove_department_member(
                db_session, department, seed_refs.ahmed.id, expected_assignment_id=original.id, changed_by=ACTOR
            )
        else:
            leadership(
                db_session, department, seed_refs.sara.id if operation == "replace" else None, expected=original.id
            )
    assert original.ends_at is None
    assert len(queries.get_department_roster(db_session, department)) == 1
    assert department not in queries.get_department_member_counts(db_session)
    assert queries.get_department_member_counts(db_session, include_archived=True)[department] == 1
    assert queries.count_current_members(db_session) == 0
    assert queries.count_current_members(db_session, include_archived=True) == 1
    service.set_department_active(db_session, department, active=True)
    assert queries.get_department_roster(db_session, department)[0].id == original.id
    add(db_session, department, seed_refs.sara.id)
    assert len(queries.get_tenure_history(db_session, department_id=department)) == 2


def test_occupied_president_and_same_person_in_two_slots_are_conflicts(db_session, seed_refs):
    original = president(db_session, seed_refs.ahmed.id)
    with pytest.raises(ClubStructureConflict, match="slot is occupied"):
        president(db_session, seed_refs.sara.id)
    with pytest.raises(ClubStructureConflict, match="already holds"):
        president(db_session, seed_refs.ahmed.id, slot=2)
    assert original.ends_at is None
    assert len(queries.get_presidents(db_session)) == 1


def test_president_replacement_constraint_failure_restores_old_holder(db_session, seed_refs):
    first = president(db_session, seed_refs.ahmed.id)
    second = president(db_session, seed_refs.sara.id, slot=2)
    with pytest.raises(ClubStructureConflict, match="already holds"):
        president(db_session, seed_refs.sara.id, expected=first.id)
    assert first.ends_at is second.ends_at is None
    assert first.ended_by is None
    assert len(queries.get_tenure_history(db_session, role=RoleType.PRESIDENT)) == 2


def test_president_replacement_and_return_keep_history_and_reject_stale_ids(db_session, seed_refs):
    first = president(db_session, seed_refs.ahmed.id)
    assert president(db_session, seed_refs.ahmed.id, expected=first.id).id == first.id
    second = president(db_session, seed_refs.sara.id, expected=first.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        president(db_session, None, expected=first.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        president(db_session, seed_refs.ahmed.id, slot=2, expected=second.id)
    returned = president(db_session, seed_refs.ahmed.id, expected=second.id)
    assert first.ends_at == second.starts_at
    assert second.ends_at == returned.starts_at
    assert first.ended_by == second.ended_by == REPLACER
    assert [a.id for a in queries.get_tenure_history(db_session, role=RoleType.PRESIDENT)] == [
        returned.id,
        second.id,
        first.id,
    ]


def test_president_rejects_department_assignment_as_expected_id(db_session, seed_refs):
    membership = add(db_session, seed_refs.dept_design.id, seed_refs.ahmed.id)
    with pytest.raises(ClubStructureConflict, match="has changed"):
        president(db_session, seed_refs.sara.id, expected=membership.id)
    assert membership.ends_at is None


def test_history_filters_and_pagination_keep_closed_periods_and_stable_order(db_session, seed_refs, monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 1, 1, 1, 2, 3, 456789))
    design, business = seed_refs.dept_design.id, seed_refs.dept_business.id
    first = leadership(db_session, design, seed_refs.ahmed.id)
    leadership(db_session, design, seed_refs.sara.id, expected=first.id)
    add(db_session, business, seed_refs.ahmed.id)
    president(db_session, seed_refs.ahmed.id)
    history = queries.get_tenure_history(db_session, department_id=design)
    assert len(history) == 3
    assert [a.id for a in history] == sorted([a.id for a in history], reverse=True)
    assert [a.id for a in queries.get_tenure_history(db_session, department_id=design, limit=1, offset=1)] == [
        history[1].id
    ]
    assert len(queries.get_tenure_history(db_session, member_id=seed_refs.ahmed.id, role=RoleType.LEADER)) == 1
    assert len(queries.get_tenure_history(db_session, role=RoleType.PRESIDENT)) == 1


def test_clock_cannot_close_period_before_it_started(db_session, seed_refs, monkeypatch):
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 1, 2))
    original = leadership(db_session, seed_refs.dept_design.id, seed_refs.ahmed.id)
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 1, 1))
    with pytest.raises(ClubStructureConflict, match="future"):
        leadership(db_session, seed_refs.dept_design.id, seed_refs.sara.id, expected=original.id)
    assert original.ends_at is None


def test_create_and_update_department_settings_preserves_identity(db_session):
    settings = service.DepartmentSettings(name="  Robotics  ", ar_name="الروبوتات", type=DepartmentsType.PRACTICAL)
    department = service.create_department(db_session, settings)
    identifier, created = department.id, department.created_at
    assert department.name == "Robotics"
    assert created is not None
    updated = service.update_department_settings(
        db_session,
        identifier,
        service.DepartmentSettings(
            name="Robotics Lab", ar_name="المختبر", type=DepartmentsType.PRACTICAL, color="#123AbC", icon="bot"
        ),
    )
    assert updated.id == identifier
    assert updated.created_at == created
    assert updated.color == "#123AbC"
    assert updated.icon == "bot"
    assert updated.leadership_enabled == 1


@pytest.mark.parametrize("invalid", [{"name": " "}, {"color": "red"}, {"icon": "x" * 33}, {"leadership_enabled": True}])
def test_department_settings_validate_editable_fields(invalid):
    with pytest.raises(ValidationError):
        service.DepartmentSettings(**{"name": "Test", "ar_name": "قسم", "type": "practical", **invalid})


@pytest.mark.parametrize("actor", ["", " ", "x" * 256])
def test_missing_actor_is_rejected_before_writing(db_session, seed_refs, actor):
    with pytest.raises(InvalidClubStructure):
        service.add_department_member(db_session, seed_refs.dept_design.id, seed_refs.ahmed.id, changed_by=actor)
    assert queries.get_tenure_history(db_session) == []


@pytest.mark.parametrize("slot", [0, 3, True])
def test_invalid_president_slots_are_useful_validation_errors(db_session, seed_refs, slot):
    with pytest.raises(InvalidClubStructure):
        president(db_session, seed_refs.ahmed.id, slot=slot)


def test_missing_records_and_invalid_roles_are_useful_errors(db_session, seed_refs):
    with pytest.raises(NotFound):
        add(db_session, 4294967295, seed_refs.ahmed.id)
    with pytest.raises(MemberNotFound):
        add(db_session, seed_refs.dept_design.id, 4294967295)
    with pytest.raises(InvalidClubStructure):
        leadership(db_session, seed_refs.dept_design.id, seed_refs.ahmed.id, role=RoleType.PRESIDENT)
    with pytest.raises(InvalidClubStructure):
        president(db_session, None)
