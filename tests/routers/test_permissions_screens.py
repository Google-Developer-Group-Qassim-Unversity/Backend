"""The permissions screens: assignments, super admins and grants, through the real guards and rosters."""

import pytest

from app.services.permissions.catalogue import Perm
from tests.access_support import SHARED

GRANTS = "/permissions/departments/{}/grants"


@pytest.fixture
def ai(club):
    department = club.department("AI")
    leader = club.join(club.person(), department, "leader")
    member = club.join(club.person(), department)
    return department, leader, member


def test_a_leader_grants_a_member_and_the_member_gains_it(club, ai):
    department, leader, member = ai
    client = club.sign_in(leader)

    body = client.get(GRANTS.format(department.id)).json()
    assert Perm.EVENTS_EDIT.value in body["grantable"]
    assert Perm.PERMISSIONS_GRANT.value not in body["grantable"]
    assert [m["member_id"] for m in body["members"]] == [member.id]

    response = client.post(GRANTS.format(department.id), json={"member_id": member.id, "permission": "events.edit"})
    assert response.status_code == 201, response.text
    assert response.json()["granted_by"]["member_id"] == leader.id
    assert club.access(member).can(Perm.EVENTS_EDIT, department.id) is True


def test_a_leader_cannot_grant_what_they_do_not_hold(club, ai):
    department, leader, member = ai
    response = club.sign_in(leader).post(
        GRANTS.format(department.id), json={"member_id": member.id, "permission": "points.custom"}
    )
    assert response.status_code == 403


def test_permissions_to_grant_are_never_grantable(club, ai):
    department, leader, member = ai
    response = club.sign_in(leader).post(
        GRANTS.format(department.id), json={"member_id": member.id, "permission": "permissions.grant"}
    )
    assert response.status_code == 403


def test_only_plain_members_of_that_department_can_be_granted(club, ai):
    department, leader, _member = ai
    outsider = club.join(club.person(), club.department("Robotics"))
    client = club.sign_in(leader)

    for member_id in (outsider.id, leader.id):
        response = client.post(GRANTS.format(department.id), json={"member_id": member_id, "permission": "events.edit"})
        assert response.status_code == 409
        assert response.json()["code"] == "not_a_plain_member"


def test_granting_twice_is_a_conflict_and_revoking_keeps_history(club, ai):
    department, leader, member = ai
    client = club.sign_in(leader)
    payload = {"member_id": member.id, "permission": "events.edit"}
    grant_id = client.post(GRANTS.format(department.id), json=payload).json()["id"]
    assert client.post(GRANTS.format(department.id), json=payload).json()["code"] == "already_granted"

    assert client.delete(f"{GRANTS.format(department.id)}/{grant_id}").status_code == 200
    assert client.get(GRANTS.format(department.id)).json()["grants"] == []
    history = client.get(GRANTS.format(department.id), params={"history": True}).json()["grants"]
    assert history[0]["revoked_by"]["member_id"] == leader.id
    assert club.access(member).can(Perm.EVENTS_EDIT, department.id) is False
    assert client.post(GRANTS.format(department.id), json=payload).status_code == 201


def test_a_leader_cannot_grant_in_another_department(club, ai):
    _department, leader, _member = ai
    robotics = club.department("Robotics")
    assert club.sign_in(leader).get(GRANTS.format(robotics.id)).status_code == 403


def test_a_plain_member_cannot_open_grants(club, ai):
    department, _leader, member = ai
    assert club.sign_in(member).get(GRANTS.format(department.id)).status_code == 403


def test_super_admin_sets_shared_and_department_permissions(club):
    logistics = club.department("Logistics")
    admin = club.super_admin(club.person())
    client = club.sign_in(admin)

    shared = sorted(p.value for p in SHARED | {Perm.MEMBERS_VIEW})
    assert client.put("/permissions/shared", json={"permissions": shared}).json()["shared"] == shared
    body = client.put(f"/permissions/departments/{logistics.id}", json={"permissions": ["pipeline.bans"]}).json()
    assert next(d for d in body["departments"] if d["department_id"] == logistics.id)["permissions"] == [
        "pipeline.bans"
    ]

    leader = club.join(club.person(), logistics, "leader")
    assert club.access(leader).can(Perm.PIPELINE_BANS) is True
    assert club.access(leader).can(Perm.MEMBERS_VIEW) is True


def test_unknown_permission_keys_are_refused(club):
    client = club.sign_in(club.super_admin(club.person()))
    response = client.put("/permissions/shared", json={"permissions": ["events.edit", "made.up"]})
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_permission"


def test_a_leader_cannot_change_assignments(club, ai):
    _department, leader, _member = ai
    assert club.sign_in(leader).put("/permissions/shared", json={"permissions": []}).status_code == 403


def test_super_admins_add_each_other_but_never_remove_themselves_or_the_last(club):
    first = club.super_admin(club.person())
    other = club.person()
    client = club.sign_in(first)

    added = client.post("/permissions/super-admins", json={"member_id": other.id})
    assert added.status_code == 201
    assert {row["member_id"] for row in added.json()} == {first.id, other.id}
    assert client.delete(f"/permissions/super-admins/{first.id}").json()["code"] == "cannot_remove_self"

    assert client.delete(f"/permissions/super-admins/{other.id}").status_code == 200
    only = club.sign_in(first).get("/permissions/super-admins").json()
    assert [row["member_id"] for row in only] == [first.id]


def test_the_last_super_admin_stays(club):
    first, second = club.super_admin(club.person()), club.super_admin(club.person())
    assert club.sign_in(first).delete(f"/permissions/super-admins/{second.id}").status_code == 200
    # first is now the only one; nobody else is a super admin to remove them, and they cannot remove themselves.
    assert club.sign_in(first).delete(f"/permissions/super-admins/{first.id}").json()["code"] == "cannot_remove_self"


def test_the_catalogue_lists_every_permission_with_labels(club, ai):
    _department, _leader, member = ai
    catalogue = club.sign_in(member).get("/permissions/catalogue").json()
    assert {row["key"] for row in catalogue} == {p.value for p in Perm}
    assert all(row["ar_label"] for row in catalogue)


# ---------- one member's access ----------

MEMBER = "/permissions/members/{}"


def _department(body: dict, department_id: int) -> dict:
    return next(d for d in body["departments"] if d["department_id"] == department_id)


def _sources(department: dict) -> dict[str, list[str]]:
    return {p["permission"]: p["sources"] for p in department["permissions"]}


def test_member_access_says_where_each_permission_comes_from(club, ai):
    department, leader, member = ai
    robotics = club.department("Robotics")
    club.join(leader, robotics)
    club.department_permission(department, Perm.POINTS_CUSTOM)
    club.department_permission(department, Perm.EVENTS_EDIT)  # also shared: both sources show
    club.grant(leader, robotics, Perm.EVENTS_EDIT, by=leader)
    admin = club.super_admin(club.person())

    body = club.sign_in(admin).get(MEMBER.format(leader.id)).json()
    assert body["member"] == {"member_id": leader.id, "name": leader.name}
    assert body["is_staff"] is True and body["is_super_admin"] is False
    assert body["semester"]["id"] == club.current.id
    assert set(body["basics"]) == {"admin.access", "events.view", "club_structure.view", "uploads"}

    ai_row = _department(body, department.id)
    assert ai_row["roles"] == ["leader", "member"]
    assert ai_row["color"] == department.color and ai_row["icon"] == department.icon
    sources = _sources(ai_row)
    assert sources["events.edit"] == ["department", "shared"]
    assert sources["points.custom"] == ["department"]
    assert set(sources) == {p.value for p in SHARED} | {"points.custom"}

    robotics_row = _department(body, robotics.id)
    assert robotics_row["roles"] == ["member"]
    [granted] = robotics_row["permissions"]
    assert granted["permission"] == "events.edit" and granted["sources"] == ["grant"]
    assert granted["granted_by"]["member_id"] == leader.id

    # It is the same answer the real checks give.
    assert set(body["permissions"]) == {p.value for p in club.access(leader).permissions()}


def test_member_access_for_someone_off_the_roster_is_empty(club):
    admin = club.super_admin(club.person())
    outsider = club.person()
    body = club.sign_in(admin).get(MEMBER.format(outsider.id)).json()
    assert body["is_staff"] is False
    assert body["basics"] == [] and body["departments"] == [] and body["permissions"] == []


def test_member_access_for_a_super_admin_lists_everything(club):
    admin = club.super_admin(club.person())
    body = club.sign_in(admin).get(MEMBER.format(admin.id)).json()
    assert body["is_super_admin"] is True and body["is_staff"] is True
    assert set(body["permissions"]) == {p.value for p in Perm}


def test_member_access_is_for_super_admins_only(club, ai):
    _department, leader, member = ai
    assert club.sign_in(leader).get(MEMBER.format(member.id)).status_code == 403


def test_member_access_for_an_unknown_member_is_404(club):
    admin = club.super_admin(club.person())
    assert club.sign_in(admin).get(MEMBER.format(999_999_999)).status_code == 404
