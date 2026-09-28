"""Department permissions for the events pipeline, through the real guards."""

import pytest
from sqlalchemy import select

from app.DB.club_structure import get_role_by_key
from app.DB.schema import ClubMemberships, DepartmentPermissions


@pytest.fixture
def dept(pipeline):
    return pipeline.department("AI")


def permissions_url(department_id):
    return f"/departments/{department_id}/permissions"


@pytest.mark.parametrize("role", ["leader", "vp"])
def test_leader_and_vp_can_grant_a_member_of_their_department(pipeline, dept, role):
    officer = pipeline.officer(dept, role)
    member = pipeline.join(pipeline.person("Member"), dept)
    pipeline.sign_in(officer)

    response = pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id})
    assert response.status_code == 201, response.text
    assert response.json()["member_id"] == member.id

    pipeline.sign_in(member)
    me = pipeline.client.get("/pipeline/me").json()
    assert me["has_access"] is True
    assert [d["id"] for d in me["departments"]] == [dept.id]
    assert me["departments"][0]["can_grant"] is False


def test_granted_member_cannot_pass_it_on(pipeline, dept):
    officer = pipeline.officer(dept)
    granted = pipeline.join(pipeline.person(), dept)
    other = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(officer)
    assert pipeline.client.post(permissions_url(dept.id), json={"member_id": granted.id}).status_code == 201

    pipeline.sign_in(granted)
    response = pipeline.client.post(permissions_url(dept.id), json={"member_id": other.id})
    assert response.status_code == 403
    assert response.json()["code"] == "department_forbidden"
    # but they can read the department's access list
    assert pipeline.client.get(permissions_url(dept.id)).status_code == 200


def test_granting_to_someone_outside_the_department_is_a_conflict(pipeline, dept):
    officer = pipeline.officer(dept)
    outsider = pipeline.person()
    pipeline.sign_in(officer)

    response = pipeline.client.post(permissions_url(dept.id), json={"member_id": outsider.id})
    assert response.status_code == 409
    assert response.json()["code"] == "not_a_department_member"


def test_granting_twice_is_a_conflict(pipeline, dept):
    officer = pipeline.officer(dept)
    member = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(officer)
    pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id})

    response = pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id})
    assert response.status_code == 409
    assert response.json()["code"] == "already_granted"


def test_revoked_grant_loses_access_and_can_be_granted_again(pipeline, dept):
    officer = pipeline.officer(dept)
    member = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(officer)
    grant_id = pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id}).json()["id"]

    assert pipeline.client.delete(f"{permissions_url(dept.id)}/{grant_id}").status_code == 200
    rows = pipeline.session.scalars(select(DepartmentPermissions).where(DepartmentPermissions.member_id == member.id))
    assert [r.revoked_by for r in rows] == [officer.id]

    pipeline.sign_in(member)
    assert pipeline.client.get("/pipeline/me").json()["has_access"] is False

    pipeline.sign_in(officer)
    assert pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id}).status_code == 201


def test_an_officer_of_another_department_cannot_grant_here(pipeline, dept):
    other_dept = pipeline.department("Cyber")
    other_leader = pipeline.officer(other_dept)
    member = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(other_leader)

    assert pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id}).status_code == 403
    assert pipeline.client.get(permissions_url(dept.id)).status_code == 403


def test_a_leader_whose_role_ended_loses_access(pipeline, dept):
    leader = pipeline.officer(dept)
    leader_role = get_role_by_key(pipeline.session, "leader")
    row = pipeline.session.scalar(
        select(ClubMemberships).where(ClubMemberships.member_id == leader.id, ClubMemberships.role_id == leader_role.id)
    )
    pipeline.session.delete(row)
    pipeline.sign_in(leader)

    me = pipeline.client.get("/pipeline/me").json()
    assert me["has_access"] is False
    assert me["departments"] == []


def test_super_admin_acts_for_every_department_and_sets_the_teams(pipeline, dept):
    design, logistics, media = (pipeline.department(n) for n in ("Design team", "Logistics team", "Media team"))
    admin = pipeline.person("Admin")
    member = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(admin, super_admin=True)

    me = pipeline.client.get("/pipeline/me").json()
    assert me["is_super_admin"] is True
    assert dept.id in {d["id"] for d in me["departments"]}
    assert pipeline.client.post(permissions_url(dept.id), json={"member_id": member.id}).status_code == 201

    response = pipeline.client.put(
        "/pipeline/teams", json={"design": design.id, "logistics": logistics.id, "media": media.id}
    )
    assert response.status_code == 200, response.text
    assert [(t["team"], t["department"]["id"]) for t in response.json()] == [
        ("design", design.id),
        ("logistics", logistics.id),
        ("media", media.id),
    ]

    reused = pipeline.client.put("/pipeline/teams", json={"design": design.id, "logistics": design.id})
    assert reused.status_code == 422


def test_non_super_admin_cannot_set_teams(pipeline, dept):
    pipeline.sign_in(pipeline.officer(dept))
    assert pipeline.client.put("/pipeline/teams", json={"design": dept.id}).status_code == 403


def test_permissions_list_shows_officers_grants_and_candidates(pipeline, dept):
    leader = pipeline.officer(dept, "leader")
    vp = pipeline.officer(dept, "vp", name="VP")
    granted = pipeline.join(pipeline.person(), dept)
    candidate = pipeline.join(pipeline.person(), dept)
    pipeline.sign_in(leader)
    pipeline.client.post(permissions_url(dept.id), json={"member_id": granted.id})

    body = pipeline.client.get(permissions_url(dept.id)).json()
    assert {(o["member_id"], o["role"]) for o in body["officers"]} == {(leader.id, "leader"), (vp.id, "vp")}
    assert [g["member_id"] for g in body["grants"]] == [granted.id]
    assert [c["member_id"] for c in body["candidates"]] == [candidate.id]
    assert body["can_grant"] is True
