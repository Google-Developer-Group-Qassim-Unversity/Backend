"""Who may act for a department in the events pipeline, through the real guards."""

import pytest
from sqlalchemy import select

from app.DB.club_structure import get_role_by_key
from app.DB.schema import ClubMemberships


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
def test_a_signed_in_non_admin_is_refused_even_as_a_leader(pipeline, dept, path):
    pipeline.sign_in(pipeline.officer(dept), admin=False)
    assert pipeline.client.get(path).status_code == 403
