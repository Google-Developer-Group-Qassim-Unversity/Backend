"""Who may act for a department in the events pipeline, through the real guards."""

import pytest
from sqlalchemy import select

from app.DB.club_structure import get_role_by_key
from app.DB.schema import ClubMemberships, Departments, DepartmentsType, PermissionGrants
from app.services.permissions.access import resolve_access
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


def test_super_admin_acts_for_every_department(pipeline, dept):
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)

    me = pipeline.client.get("/pipeline/me").json()
    assert me["is_super_admin"] is True
    assert dept.id in {d["id"] for d in me["departments"]}


def test_the_teams_are_this_semesters_departments_named_after_them(pipeline, dept):
    design, logistics, media = (
        pipeline.department(n) for n in ("UI/UX design", "Programs and Logistics", "Media & PR")
    )
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)

    me = pipeline.client.get("/pipeline/me").json()
    assert [(t["team"], t["department"]["id"]) for t in me["teams"]] == [
        ("design", design.id),
        ("logistics", logistics.id),
        ("media", media.id),
    ]


def test_a_department_off_this_semester_is_not_a_team(pipeline, dept):
    old_design = Departments(name="Design", ar_name="التصميم", type=DepartmentsType.PRACTICAL)
    pipeline.session.add(old_design)
    pipeline.session.flush()
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)

    assert pipeline.client.get("/pipeline/me").json()["teams"] == []


def test_two_departments_with_the_name_leave_the_team_unset(pipeline, dept):
    pipeline.department("Design")
    pipeline.department("Graphic Design")
    pipeline.department("Logistics")
    pipeline.sign_in(pipeline.person("Admin"), super_admin=True)

    # Design is ambiguous; Logistics, with one match, is unaffected.
    assert [t["team"] for t in pipeline.client.get("/pipeline/me").json()["teams"]] == ["logistics"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Design", {Perm.PIPELINE_DESIGN}),
        ("Logistics", {Perm.PIPELINE_LOGISTICS, Perm.PIPELINE_BANS}),
        ("Media", {Perm.PIPELINE_MEDIA, Perm.EMAILS_DIRECT, Perm.EMAILS_BLAST, Perm.EMAILS_LOGS}),
    ],
)
def test_a_team_departments_leader_gets_the_teams_permissions(pipeline, name, expected):
    team = pipeline.department(name)
    leader = pipeline.officer(team)
    member = pipeline.join(pipeline.person(), team)

    assert expected <= resolve_access(pipeline.session, leader).permissions()
    # Plain members of the team get them only through a grant.
    assert not expected & resolve_access(pipeline.session, member).permissions()


def test_a_department_that_is_no_team_gets_no_team_permissions(pipeline, dept):
    pipeline.department("Design")
    leader = pipeline.officer(dept)

    assert Perm.PIPELINE_DESIGN not in resolve_access(pipeline.session, leader).permissions()


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
