"""Who may act for a department in the events pipeline, through the real guards."""

import pytest
from sqlalchemy import select

from app.DB.club_structure import get_role_by_key
from app.DB.schema import ClubMemberships, PermissionGrants
from app.services.permissions.catalogue import Perm


@pytest.fixture
def dept(pipeline):
    return pipeline.department("AI")


@pytest.mark.parametrize("role", ["leader", "vp"])
def test_leader_and_vp_act_for_their_department(pipeline, dept, role):
    pipeline.sign_in(pipeline.officer(dept, role))

    me = pipeline.client.get("/pipeline/me").json()
    assert me["has_access"] is True
    assert [d["id"] for d in me["departments"]] == [dept.id]
    assert me["departments"][0]["is_officer"] is True


def test_a_plain_member_does_not_act_for_their_department(pipeline, dept):
    pipeline.sign_in(pipeline.join(pipeline.person(), dept))

    me = pipeline.client.get("/pipeline/me").json()
    assert me["has_access"] is False
    assert me["departments"] == []


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
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)

    me = pipeline.client.get("/pipeline/me").json()
    assert me["is_super_admin"] is True
    assert dept.id in {d["id"] for d in me["departments"]}

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


@pytest.mark.parametrize(
    "path",
    ["/pipeline/me", "/pipeline/calendar?from=2026-07-01&to=2026-07-31", "/pipeline/requests", "/pipeline/inbox"],
)
def test_someone_off_this_semesters_roster_is_refused(pipeline, dept, path):
    pipeline.sign_in(pipeline.person())
    response = pipeline.client.get(path)
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_member_granted_design_works_designs_inbox(pipeline, dept):
    design = pipeline.department("Design team")
    pipeline.teams(design, pipeline.department("Logistics team"), pipeline.department("Media team"))
    leader = pipeline.officer(design)
    member = pipeline.join(pipeline.person(), design)
    pipeline.session.add(
        PermissionGrants(
            semester_id=pipeline.semester_id,
            department_id=design.id,
            member_id=member.id,
            permission=Perm.PIPELINE_DESIGN.value,
            granted_by=leader.id,
        )
    )
    pipeline.sign_in(member)

    me = pipeline.client.get("/pipeline/me").json()
    assert me["has_access"] is True
    assert pipeline.client.get("/pipeline/inbox").status_code == 200
    # Working Design's inbox is not requesting events for Design.
    booking = pipeline.client.post(
        "/pipeline/requests", json={"department_id": design.id, "start_date": "2026-07-20", "end_date": "2026-07-20"}
    )
    assert booking.status_code == 403
