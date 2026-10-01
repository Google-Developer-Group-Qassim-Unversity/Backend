"""The access resolver and ``GET /access/me``: who is staff, and what each person can do."""

from datetime import date

import pytest
from sqlalchemy import select

from app import semesters as club_calendar
from app.DB.schema import SharedPermissions, SuperAdmins
from app.services.permissions.catalogue import STAFF_BASICS, Perm
from tests.access_support import SHARED
from tests.factories import make_create_event_payload


def test_a_regular_user_is_not_staff(club):
    access = club.access(club.person())
    assert access.is_staff is False
    assert access.permissions() == frozenset()
    assert access.can(Perm.ADMIN_ACCESS) is False


def test_a_signed_in_person_with_no_member_row_is_not_staff(club):
    body = club.me(None)
    assert body["member_id"] is None
    assert body["is_staff"] is False
    assert body["permissions"] == []


def test_a_plain_member_is_staff_with_the_basics_only(club):
    ai = club.department("AI")
    member = club.join(club.person(), ai)
    access = club.access(member)

    assert access.is_staff is True
    assert access.permissions() == STAFF_BASICS
    assert access.can(Perm.EVENTS_EDIT, ai.id) is False


@pytest.mark.parametrize("role", ["leader", "vp"])
def test_leader_and_vp_get_shared_and_their_departments_permissions(club, role):
    logistics = club.department("Logistics")
    club.department_permission(logistics, Perm.PIPELINE_BANS)
    officer = club.join(club.person(), logistics, role)
    access = club.access(officer)

    assert access.can(Perm.EVENTS_EDIT, logistics.id) is True
    assert access.can(Perm.PIPELINE_BANS) is True
    assert access.permissions() == STAFF_BASICS | SHARED | {Perm.PIPELINE_BANS}


def test_department_permissions_stay_with_their_department(club):
    logistics, ai = club.department("Logistics"), club.department("AI")
    club.department_permission(logistics, Perm.PIPELINE_BANS)
    ai_leader = club.join(club.person(), ai, "leader")

    assert club.access(ai_leader).can(Perm.PIPELINE_BANS) is False


def test_a_dept_permission_covers_only_the_departments_it_is_held_for(club):
    ai, robotics = club.department("AI"), club.department("Robotics")
    leader = club.join(club.person(), ai, "leader")
    access = club.access(leader)

    assert access.can(Perm.EVENTS_EDIT, ai.id) is True
    assert access.can(Perm.EVENTS_EDIT, robotics.id) is False
    # Without a department it asks "anywhere".
    assert access.can(Perm.EVENTS_EDIT) is True
    assert access.departments_for(Perm.EVENTS_EDIT) == {ai.id}


def test_a_leader_last_semester_only_is_a_regular_user(club):
    ai = club.department("AI")
    former = club.join(club.person(), ai, "leader", semester=club.previous)

    body = club.me(former)
    assert body["is_staff"] is False
    assert body["departments"] == []


def test_the_roster_follows_the_semester_on_its_start_date(club, monkeypatch):
    ai = club.department("AI")
    summer_leader = club.join(club.person(), ai, "leader")
    spring_leader = club.join(club.person(), ai, "leader", semester=club.previous)

    # Spring 2026 (472) runs until 31 May; Summer 2026 (475) starts on 28 June.
    monkeypatch.setattr(club_calendar, "today", lambda: date(2026, 6, 27))
    assert club.access(spring_leader).is_staff is True
    assert club.access(summer_leader).is_staff is False

    monkeypatch.setattr(club_calendar, "today", lambda: date(2026, 6, 28))
    assert club.access(spring_leader).is_staff is False
    assert club.access(summer_leader).is_staff is True


def test_a_super_admin_off_the_roster_can_do_everything(club):
    admin = club.super_admin(club.person())
    access = club.access(admin)

    assert access.is_staff is True
    assert access.can(Perm.PERMISSIONS_MANAGE) is True
    assert access.can(Perm.EVENTS_EDIT, club.department("AI").id) is True
    assert access.departments_for(Perm.EVENTS_EDIT) is None

    body = club.me(admin)
    assert body["is_super_admin"] is True
    assert set(body["permissions"]) == {p.value for p in Perm}


def test_the_clerk_super_admin_flag_counts_for_nothing(club):
    body = club.me(club.person(), metadata={"is_super_admin": True, "is_admin": True})
    assert body["is_staff"] is False
    assert body["is_super_admin"] is False


def test_a_grant_this_semester_counts_for_its_department(club):
    ai = club.department("AI")
    leader = club.join(club.person(), ai, "leader")
    member = club.join(club.person(), ai)
    club.grant(member, ai, Perm.ATTENDANCE_TAKE, by=leader)
    access = club.access(member)

    assert access.can(Perm.ATTENDANCE_TAKE, ai.id) is True
    assert access.can(Perm.ATTENDANCE_TAKE, club.department("Robotics").id) is False
    assert access.can(Perm.EVENTS_EDIT, ai.id) is False


def test_grants_from_last_semester_and_revoked_grants_do_not_count(club):
    ai = club.department("AI")
    leader = club.join(club.person(), ai, "leader")
    member = club.join(club.person(), ai)
    club.grant(member, ai, Perm.ATTENDANCE_TAKE, by=leader, semester=club.previous)
    club.grant(member, ai, Perm.SUBMISSIONS_REVIEW, by=leader, revoked=True)

    assert club.access(member).permissions() == STAFF_BASICS


def test_a_grant_stops_counting_when_the_member_leaves_that_department(club):
    ai, robotics = club.department("AI"), club.department("Robotics")
    leader = club.join(club.person(), ai, "leader")
    member = club.join(club.person(), robotics)
    club.grant(member, ai, Perm.ATTENDANCE_TAKE, by=leader)

    assert club.access(member).can(Perm.ATTENDANCE_TAKE) is False


def test_unknown_permission_keys_are_ignored(club):
    club.session.add(SharedPermissions(permission="something.removed"))
    ai = club.department("AI")
    leader = club.join(club.person(), ai, "leader")

    assert club.access(leader).permissions() == STAFF_BASICS | SHARED


def test_access_me_lists_departments_roles_and_their_permissions(club):
    ai = club.department("AI")
    leader = club.join(club.person(), ai, "leader")

    body = club.me(leader)
    assert body["is_staff"] is True
    assert body["is_super_admin"] is False
    assert body["semester"]["hijri_code"] == 475
    assert body["departments"] == [
        {
            "id": ai.id,
            "name": "AI",
            "ar_name": "قسم AI",
            "roles": ["leader", "member"],
            # All three shared permissions are department-scoped.
            "permissions": sorted(p.value for p in SHARED),
        }
    ]
    assert set(body["permissions"]) == {p.value for p in STAFF_BASICS | SHARED}


def test_the_migration_seeds_the_shared_permissions(db_session):
    seeded = set(db_session.scalars(select(SharedPermissions.permission)).all())
    assert {Perm.EVENTS_EDIT.value, Perm.PIPELINE_REQUEST.value, Perm.PERMISSIONS_GRANT.value} <= seeded
    assert db_session.scalars(select(SuperAdmins)).all() == []


# ---------- the guards, through real routes ----------


def test_a_leader_edits_their_departments_event_and_not_anothers(club, seed_refs):
    ai, robotics = club.department("AI"), club.department("Robotics")
    ai_event, robotics_event = club.event(ai, seed_refs), club.event(robotics, seed_refs)
    client = club.sign_in(club.join(club.person(), ai, "leader"))

    assert client.put(f"/events/{ai_event}/status", json={"status": "open"}).status_code == 200
    refused = client.put(f"/events/{robotics_event}/status", json={"status": "open"})
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"


def test_a_missing_event_is_404_not_403(club):
    client = club.sign_in(club.join(club.person(), club.department("AI"), "leader"))
    assert client.put("/events/987654/status", json={"status": "open"}).status_code == 404


def test_a_leader_cannot_create_an_event_for_another_department(club, seed_refs):
    ai, robotics = club.department("AI"), club.department("Robotics")
    client = club.sign_in(club.join(club.person(), ai, "leader"))

    refused = client.post("/events/", json=make_create_event_payload(seed_refs, department_id=robotics.id))
    assert refused.status_code == 403


def test_a_plain_member_passes_the_staff_door_but_not_a_permission(club):
    client = club.sign_in(club.join(club.person(), club.department("AI")))

    assert client.get("/semesters").status_code == 200
    assert client.get("/members/paginated").status_code == 403


def test_a_regular_user_is_refused_everywhere_in_the_admin_api(club):
    client = club.sign_in(club.person())

    assert client.get("/semesters").status_code == 403
    assert client.get("/events/paginated").status_code == 403


def test_super_admin_routes_want_a_super_admin_from_the_database(club):
    leader = club.join(club.person(), club.department("AI"), "leader")
    assert club.sign_in(leader, metadata={"is_super_admin": True}).get("/permissions/super-admins").status_code == 403

    assert club.sign_in(club.super_admin(club.person())).get("/permissions/super-admins").status_code == 200


def test_access_for_an_event_follows_its_department(club, seed_refs):
    ai, robotics = club.department("AI"), club.department("Robotics")
    ai_event, robotics_event = club.event(ai, seed_refs), club.event(robotics, seed_refs)
    client = club.sign_in(club.join(club.person(), ai, "leader"))

    mine = set(client.get(f"/access/events/{ai_event}").json()["permissions"])
    theirs = set(client.get(f"/access/events/{robotics_event}").json()["permissions"])
    assert Perm.EVENTS_EDIT.value in mine
    assert Perm.EVENTS_EDIT.value not in theirs
    assert Perm.EVENTS_VIEW.value in theirs
