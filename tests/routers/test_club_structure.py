"""The club structure per semester, through the HTTP boundary with the real guards."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from app.config import config
from app.DB.schema import (
    ClubMembershipChanges,
    ClubMemberships,
    Departments,
    DepartmentsLogs,
    DepartmentsType,
    Events,
    EventsLocationType,
    EventsStatus,
    Logs,
    Members,
    SemesterDepartments,
    Semesters,
    SemesterTerm,
)
from app.DB.semesters import get_semester_by_hijri_code
from app.member_names import initial_public_name
from app.main import app
from app.routers import club_structure as router
from app.services.permissions.access import Access
from app.services.permissions.dependencies import get_access
from tests.access_doubles import ADMIN, POINTS_ADMIN, SUPER_ADMIN
from tests.factories import make_member

PREFIX = "/club-structure"
SETTINGS = {
    "name": "Robotics",
    "ar_name": "الروبوتات",
    "type": "practical",
    "color": "#123abc",
    "icon": "bot",
    "show_in_leaderboard": True,
}
SCOPE = "/semesters/{semester}/departments/{department}"
READS = ["", "/roles", "/departments/{department}", "/departments/{department}/roster", "/history"]
WRITES = [
    ("POST", "/departments", SETTINGS),
    ("PUT", "/departments/{department}", SETTINGS),
    ("POST", "/departments/{department}/archive", None),
    ("POST", "/departments/{department}/restore", None),
    ("POST", SCOPE, None),
    ("DELETE", SCOPE, None),
    ("POST", SCOPE + "/members", {"member_id": 1}),
    ("DELETE", SCOPE + "/members/{member}", None),
    ("PUT", SCOPE + "/members/{member}/roles/leader", {}),
    ("DELETE", SCOPE + "/members/{member}/roles/leader", None),
    ("POST", "/semesters/{semester}/copy-from/{semester}", None),
]


@pytest.fixture(autouse=True)
def cache_reset(monkeypatch):
    reset = Mock(return_value={"revalidated": True})
    monkeypatch.setattr(router, "reset_leaderboard_cache", reset)
    return reset


@pytest.fixture
def sign_in(client):
    """Override JWT verification and the caller's access; the application's guards still run."""
    bearer = config.CLERK_GUARD

    def sign_in_as(role="super", subject="clerk_structure_admin"):
        access = {"member": Access(), "admin": ADMIN, "points": POINTS_ADMIN, "super": SUPER_ADMIN}[role]
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="test-token", decoded={"sub": subject, "metadata": {}}
        )
        app.dependency_overrides[bearer] = lambda: credentials
        app.dependency_overrides[get_access] = lambda: access
        return client

    yield sign_in_as
    app.dependency_overrides.pop(bearer, None)
    app.dependency_overrides.pop(get_access, None)


@pytest.fixture
def club(db_session, seed_refs):
    """The seeded departments as part of the current semester (475, with today pinned to 2026-07-15)."""
    current = semester_by_code(db_session, 475)
    leadership = db_session.scalar(select(Departments).where(Departments.is_club_leadership == 1))
    for department in (seed_refs.dept_design, seed_refs.dept_business):
        db_session.add(SemesterDepartments(semester_id=current.id, department_id=department.id))
    db_session.flush()
    return SimpleNamespace(
        semester=current,
        design=seed_refs.dept_design,
        business=seed_refs.dept_business,
        leadership=leadership,
        ahmed=seed_refs.ahmed,
        sara=seed_refs.sara,
    )


def semester_by_code(db_session, hijri_code: int) -> Semesters:
    row = get_semester_by_hijri_code(db_session, hijri_code)
    assert row is not None
    return row


def scope(club, department=None, semester=None):
    return f"{PREFIX}/semesters/{(semester or club.semester).id}/departments/{(department or club.design).id}"


def add(client, club, member, department=None, semester=None):
    response = client.post(scope(club, department, semester) + "/members", json={"member_id": member.id})
    assert response.status_code == 201, response.text
    return response.json()


def grant(client, club, member, role="leader", department=None, replaces=None):
    return client.put(
        f"{scope(club, department)}/members/{member.id}/roles/{role}", json={"replaces_member_id": replaces}
    )


def roster(client, club, department=None, semester=None):
    response = client.get(
        f"{PREFIX}/departments/{(department or club.design).id}/roster",
        params={"semester_id": (semester or club.semester).id},
    )
    assert response.status_code == 200, response.text
    return {entry["member"]["id"]: sorted(entry["roles"]) for entry in response.json()}


def url(path, club):
    return PREFIX + path.format(semester=club.semester.id, department=club.design.id, member=club.ahmed.id)


def make_person(db_session, name, n):
    person = Members(**make_member(name=name, uni_id=f"club-{n}", email=f"club-{n}@example.com"))
    db_session.add(person)
    db_session.flush()
    return person


def make_fall_2026(db_session) -> Semesters:
    semester = Semesters(
        term=SemesterTerm.FIRST,
        hijri_year=1448,
        academic_year_start=2026,
        start_date=date(2026, 8, 23),
        end_date=date(2026, 12, 17),
        is_public=True,
    )
    db_session.add(semester)
    db_session.flush()
    return semester


# ---------- guards ----------


@pytest.mark.parametrize("path", READS)
def test_reads_reject_anonymous_callers(client, club, path):
    assert client.get(url(path, club)).status_code == 403


@pytest.mark.parametrize("role", ["member", "admin", "points", "super"])
@pytest.mark.parametrize("path", READS)
def test_reads_need_club_structure_view(sign_in, club, path, role):
    response = sign_in(role).get(url(path, club))
    assert response.status_code == (403 if role == "member" else 200), response.text


@pytest.mark.parametrize("method,path,payload", WRITES)
def test_writes_reject_anonymous_callers(client, club, method, path, payload):
    assert client.request(method, url(path, club), json=payload).status_code == 403


@pytest.mark.parametrize("role", ["member", "admin", "points"])
@pytest.mark.parametrize("method,path,payload", WRITES)
def test_writes_reject_non_super_admins(sign_in, club, role, method, path, payload, db_session):
    response = sign_in(role).request(method, url(path, club), json=payload)
    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(ClubMemberships)) == 0


# ---------- overview ----------


def test_overview_defaults_to_the_current_semester(sign_in, club):
    body = sign_in("admin").get(PREFIX).json()
    assert body["semester"]["hijri_code"] == 475
    assert body["semester"]["name"] == "Summer 2026"
    assert {d["id"] for d in body["departments"]} == {club.design.id, club.business.id, club.leadership.id}
    assert body["total_members"] == 0
    assert [role["key"] for role in body["roles"]] == ["leader", "vp", "member"]
    cards = {d["id"]: d for d in body["departments"]}
    assert cards[club.design.id]["roles"] == [
        {"key": "leader", "max_holders": 1, "holders": []},
        {"key": "vp", "max_holders": 1, "holders": []},
    ]
    # Leadership's own seat limit: two leaders.
    assert cards[club.leadership.id]["roles"][0]["max_holders"] == 2
    assert cards[club.leadership.id]["is_club_leadership"] is True
    assert cards[club.leadership.id]["show_in_leaderboard"] is False


def test_overview_of_another_semester_is_separate(sign_in, club, db_session):
    client = sign_in()
    add(client, club, club.ahmed)
    spring = semester_by_code(db_session, 472)
    body = client.get(PREFIX, params={"semester_id": spring.id}).json()
    assert body["semester"]["hijri_code"] == 472
    assert body["total_members"] == 0
    assert club.design.id not in {d["id"] for d in body["departments"]}
    assert club.design.id in {d["id"] for d in body["available_departments"]}


def test_overview_shows_the_name_a_department_had_that_semester(sign_in, club, db_session):
    row = db_session.get(SemesterDepartments, (club.semester.id, club.design.id))
    row.name, row.ar_name = "Old Design", "التصميم القديم"
    db_session.flush()
    cards = {d["id"]: d for d in sign_in("admin").get(PREFIX).json()["departments"]}
    assert cards[club.design.id]["name"] == "Design"
    assert cards[club.design.id]["semester_name"] == "Old Design"
    assert cards[club.design.id]["semester_ar_name"] == "التصميم القديم"


def test_unknown_or_malformed_semester(sign_in, club):
    client = sign_in("admin")
    assert client.get(PREFIX, params={"semester_id": "00000000-0000-4000-8000-000000000000"}).status_code == 404
    assert client.get(PREFIX, params={"semester_id": "475"}).status_code == 422


# ---------- roster ----------


def test_add_member_and_refuse_duplicates(sign_in, club):
    client = sign_in(subject="clerk_without_member_row")
    row = add(client, club, club.ahmed)
    assert row["created_by"] == "clerk_without_member_row"
    assert row["created_at"].endswith("Z")
    assert roster(client, club) == {club.ahmed.id: ["member"]}
    response = client.post(scope(club) + "/members", json={"member_id": club.ahmed.id})
    assert response.status_code == 409


def test_adding_an_unknown_member_is_404(sign_in, club):
    response = sign_in().post(scope(club) + "/members", json={"member_id": 999999})
    assert response.status_code == 404


def test_department_must_be_part_of_the_semester(sign_in, club, db_session):
    spring = semester_by_code(db_session, 472)
    response = sign_in().post(scope(club, semester=spring) + "/members", json={"member_id": club.ahmed.id})
    assert response.status_code == 409
    assert "not part of this semester" in response.json()["detail"]


def test_leader_is_also_a_member_explicitly(sign_in, club, db_session):
    client = sign_in()
    response = grant(client, club, club.sara)
    assert response.status_code == 200, response.text
    assert roster(client, club) == {club.sara.id: ["leader", "member"]}
    rows = db_session.scalars(select(ClubMemberships).where(ClubMemberships.member_id == club.sara.id)).all()
    assert sorted(row.role.key for row in rows) == ["leader", "member"]

    body = client.get(PREFIX).json()
    card = next(d for d in body["departments"] if d["id"] == club.design.id)
    assert card["member_count"] == 1
    assert card["roles"][0]["holders"] == [{"id": club.sara.id, "name": club.sara.name}]
    assert body["total_members"] == 1


def test_granting_a_held_role_again_changes_nothing(sign_in, club, db_session):
    client = sign_in()
    first = grant(client, club, club.sara).json()
    assert grant(client, club, club.sara).json()["id"] == first["id"]
    assert db_session.scalar(select(func.count()).select_from(ClubMemberships)) == 2


def test_a_full_seat_is_refused_unless_the_holder_is_named(sign_in, club):
    client = sign_in()
    assert grant(client, club, club.sara).status_code == 200
    refused = grant(client, club, club.ahmed)
    assert refused.status_code == 409
    assert "taken" in refused.json()["detail"]

    stale = grant(client, club, club.ahmed, replaces=club.ahmed.id)
    assert stale.status_code == 409

    assert grant(client, club, club.ahmed, replaces=club.sara.id).status_code == 200
    # The former leader stays a member.
    assert roster(client, club) == {club.sara.id: ["member"], club.ahmed.id: ["leader", "member"]}


def test_one_person_can_be_leader_and_vp_only_through_two_seats(sign_in, club):
    client = sign_in()
    assert grant(client, club, club.sara, "leader").status_code == 200
    assert grant(client, club, club.sara, "vp").status_code == 200
    assert roster(client, club) == {club.sara.id: ["leader", "member", "vp"]}


def test_leadership_department_has_two_leader_seats(sign_in, club, db_session):
    client = sign_in()
    third = make_person(db_session, "Third Person", 3)
    assert grant(client, club, club.ahmed, department=club.leadership).status_code == 200
    assert grant(client, club, club.sara, department=club.leadership).status_code == 200
    assert grant(client, club, third, department=club.leadership).status_code == 409


def test_revoking_a_role_keeps_the_member(sign_in, club):
    client = sign_in()
    grant(client, club, club.sara, "vp")
    response = client.delete(f"{scope(club)}/members/{club.sara.id}/roles/vp")
    assert response.status_code == 200, response.text
    assert roster(client, club) == {club.sara.id: ["member"]}
    assert client.delete(f"{scope(club)}/members/{club.sara.id}/roles/vp").status_code == 409


def test_member_role_is_managed_through_the_roster_only(sign_in, club):
    client = sign_in()
    add(client, club, club.ahmed)
    assert grant(client, club, club.ahmed, "member").status_code == 422
    assert client.delete(f"{scope(club)}/members/{club.ahmed.id}/roles/member").status_code == 422


def test_unknown_role_is_404(sign_in, club):
    assert grant(sign_in(), club, club.ahmed, "treasurer").status_code == 404


def test_removing_a_member_removes_every_role_they_hold_there(sign_in, club):
    client = sign_in()
    grant(client, club, club.sara, "leader")
    grant(client, club, club.sara, "vp")
    add(client, club, club.sara, department=club.business)
    response = client.delete(f"{scope(club)}/members/{club.sara.id}")
    assert response.json() == {"removed": 3}
    assert roster(client, club) == {}
    # Other departments are untouched.
    assert roster(client, club, department=club.business) == {club.sara.id: ["member"]}
    assert client.delete(f"{scope(club)}/members/{club.sara.id}").status_code == 409


def test_every_change_is_logged_with_its_actor(sign_in, club, db_session):
    client = sign_in(subject="clerk_logger")
    grant(client, club, club.sara)
    client.delete(f"{scope(club)}/members/{club.sara.id}")
    changes = db_session.scalars(select(ClubMembershipChanges).order_by(ClubMembershipChanges.created_at)).all()
    assert sorted((c.action.value, c.role.key) for c in changes) == [
        ("added", "leader"),
        ("added", "member"),
        ("removed", "leader"),
        ("removed", "member"),
    ]
    assert {c.actor for c in changes} == {"clerk_logger"}

    history = client.get(f"{PREFIX}/history", params={"semester_id": club.semester.id, "limit": 3}).json()
    assert len(history["items"]) == 3 and history["has_more"] is True
    assert history["items"][0]["member"]["id"] == club.sara.id


# ---------- departments in a semester ----------


def test_create_department_joins_the_semester(sign_in, club, db_session, cache_reset):
    client = sign_in()
    response = client.post(f"{PREFIX}/departments", json=SETTINGS)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["active"] is True and created["show_in_leaderboard"] is True
    assert created["created_at"].endswith("Z")
    assert db_session.get(SemesterDepartments, (club.semester.id, created["id"])) is not None

    response = client.put(
        f"{PREFIX}/departments/{created['id']}", json={**SETTINGS, "name": "Robotics Lab", "show_in_leaderboard": False}
    )
    assert response.status_code == 200
    assert response.json()["show_in_leaderboard"] is False
    assert db_session.get(Departments, created["id"]).name == "Robotics Lab"
    assert cache_reset.call_count == 2


def test_update_requires_every_setting(sign_in, club):
    settings = {key: value for key, value in SETTINGS.items() if key != "show_in_leaderboard"}
    assert sign_in().put(f"{PREFIX}/departments/{club.design.id}", json=settings).status_code == 422


def test_add_and_remove_a_department_for_a_semester(sign_in, club, db_session):
    client = sign_in()
    spring = semester_by_code(db_session, 472)
    assert client.post(scope(club, semester=spring)).status_code == 201
    assert client.post(scope(club, semester=spring)).status_code == 409
    assert client.delete(scope(club, semester=spring)).status_code == 200
    assert db_session.get(SemesterDepartments, (spring.id, club.design.id)) is None


def test_an_archived_department_cannot_join_a_semester(sign_in, club, db_session):
    client = sign_in()
    spring = semester_by_code(db_session, 472)
    assert client.post(f"{PREFIX}/departments/{club.design.id}/archive").status_code == 200
    assert client.post(scope(club, semester=spring)).status_code == 409


def test_a_department_with_a_roster_stays_in_the_semester(sign_in, club):
    client = sign_in()
    add(client, club, club.ahmed)
    assert client.delete(scope(club)).status_code == 409


def test_a_department_with_points_stays_in_the_semester(sign_in, club, db_session, seed_refs):
    event = Events(
        name="Workshop",
        location_type=EventsLocationType.ON_SITE,
        location="Hall",
        start_datetime="2026-07-01 10:00:00",
        end_datetime="2026-07-01 12:00:00",
        status=EventsStatus.CLOSED,
        semester_id=club.semester.id,
    )
    db_session.add(event)
    db_session.flush()
    log = Logs(action_id=seed_refs.dept_action.id, event_id=event.id)
    db_session.add(log)
    db_session.flush()
    db_session.add(DepartmentsLogs(department_id=club.design.id, log_id=log.id))
    db_session.flush()
    response = sign_in().delete(scope(club))
    assert response.status_code == 409
    assert "points" in response.json()["detail"]


# ---------- copying a semester ----------


def test_copy_structure_into_an_empty_semester(sign_in, club, db_session):
    client = sign_in(subject="clerk_copier")
    grant(client, club, club.sara)
    add(client, club, club.ahmed)
    add(client, club, club.ahmed, department=club.business)
    fall = make_fall_2026(db_session)

    response = client.post(f"{PREFIX}/semesters/{fall.id}/copy-from/{club.semester.id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"copied": 4}
    assert roster(client, club, semester=fall) == {club.sara.id: ["leader", "member"], club.ahmed.id: ["member"]}
    assert roster(client, club, department=club.business, semester=fall) == {club.ahmed.id: ["member"]}
    # The source is untouched, and editing the copy leaves it alone.
    client.delete(f"{scope(club, semester=fall)}/members/{club.sara.id}")
    assert roster(client, club) == {club.sara.id: ["leader", "member"], club.ahmed.id: ["member"]}

    again = client.post(f"{PREFIX}/semesters/{fall.id}/copy-from/{club.semester.id}")
    assert again.status_code == 409


def test_copy_skips_archived_departments(sign_in, club, db_session):
    client = sign_in()
    add(client, club, club.ahmed, department=club.business)
    add(client, club, club.sara)
    client.post(f"{PREFIX}/departments/{club.business.id}/archive")
    fall = make_fall_2026(db_session)
    assert client.post(f"{PREFIX}/semesters/{fall.id}/copy-from/{club.semester.id}").json() == {"copied": 1}
    assert db_session.get(SemesterDepartments, (fall.id, club.business.id)) is None


def test_copy_from_itself_is_refused(sign_in, club):
    response = sign_in().post(f"{PREFIX}/semesters/{club.semester.id}/copy-from/{club.semester.id}")
    assert response.status_code == 422


def test_a_semester_with_a_roster_cannot_be_deleted(sign_in, club, super_admin_client):
    add(sign_in(), club, club.ahmed)
    response = super_admin_client.delete(f"/semesters/{club.semester.id}")
    assert response.status_code == 409
    assert response.json()["code"] == "semester_has_roster"


def test_deleting_an_empty_semester_takes_its_department_list(sign_in, club, db_session, super_admin_client):
    fall = make_fall_2026(db_session)
    client = sign_in()
    assert client.post(scope(club, semester=fall)).status_code == 201
    add(client, club, club.ahmed, semester=fall)
    client.delete(f"{scope(club, semester=fall)}/members/{club.ahmed.id}")
    assert super_admin_client.delete(f"/semesters/{fall.id}").status_code == 200


# ---------- public ----------


def test_public_structure_uses_chosen_aliases_without_shortening(sign_in, club, db_session):
    client = sign_in()
    club.ahmed.public_name = "Public President Alias"
    club.sara.public_name = "Public Officer Alias"
    db_session.flush()
    add(client, club, club.ahmed, department=club.business)
    assert grant(client, club, club.sara).status_code == 200
    assert grant(client, club, club.sara, role="vp", department=club.business).status_code == 200
    assert grant(client, club, club.ahmed, department=club.leadership).status_code == 200
    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    response = client.get(PREFIX + "/public")
    assert response.status_code == 200
    body = response.json()
    assert body["presidents"] == ["Public President Alias"]
    cards = {item["id"]: item for item in body["departments"]}
    assert cards[club.design.id]["leader"] == "Public Officer Alias"
    assert cards[club.business.id]["deputy"] == "Public Officer Alias"
    assert cards[club.business.id]["members"] == ["Public President Alias"]
    assert club.ahmed.name not in response.text
    assert club.sara.name not in response.text


def test_public_structure_is_anonymous_and_display_only(sign_in, club, db_session):
    client = sign_in()
    club.design.name, club.design.ar_name, club.design.color = "Operations", "قسم التشغيل", "#22c55e"
    club.ahmed.name = "Ahmed Mohammed Ali"
    club.sara.name = "Sara Abdullah Khalid"
    board = Departments(
        name="Board of Directors", ar_name="مجلس الإدارة", type=DepartmentsType.ADMINISTRATIVE, show_in_leaderboard=0
    )
    db_session.add(board)
    db_session.flush()
    client.post(scope(club, department=board))

    add(client, club, club.ahmed)
    grant(client, club, club.sara)
    add(client, club, club.sara, department=club.business)
    add(client, club, club.ahmed, department=board)
    grant(client, club, club.ahmed, department=club.leadership)

    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    response = client.get(PREFIX + "/public")
    assert response.status_code == 200
    body = response.json()
    assert body["semester"] == {"code": 475, "gregorian_code": 253, "name": "Summer 2026"}
    assert body["presidents"] == ["Ahmed Ali"]
    cards = {department["id"]: department for department in body["departments"]}
    # Leadership is shown as the presidents, not as a department.
    assert set(cards) == {club.design.id, club.business.id, board.id}
    assert cards[club.design.id] == {
        "id": club.design.id,
        "name": "Operations",
        "ar_name": "قسم التشغيل",
        "type": "administrative",
        "color": "#22c55e",
        "icon": "users",
        "show_in_leaderboard": True,
        "leadership_enabled": True,
        "leader": "Sara Khalid",
        "deputy": None,
        # The leader's own member row does not list her twice.
        "members": ["Ahmed Ali"],
    }
    assert cards[club.business.id]["members"] == ["Sara Khalid"]
    assert cards[club.business.id]["leadership_enabled"] is False
    assert cards[board.id]["show_in_leaderboard"] is False
    assert cards[board.id]["members"] == ["Ahmed Ali"]
    for private_field in ("member_id", "role_id", "created_by", "created_at"):
        assert private_field not in response.text


def test_public_structure_of_a_past_semester(sign_in, club, db_session):
    anonymous = sign_in()
    add(anonymous, club, club.ahmed)
    spring = semester_by_code(db_session, 472)
    db_session.add(SemesterDepartments(semester_id=spring.id, department_id=club.design.id, name="Design 2026"))
    db_session.flush()
    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    response = anonymous.get(PREFIX + "/public", params={"semester": 472})
    assert response.status_code == 200
    body = response.json()
    assert body["semester"]["code"] == 472
    assert body["departments"][0]["name"] == "Design 2026"
    assert body["departments"][0]["members"] == []
    assert anonymous.get(PREFIX + "/public", params={"semester": 999}).status_code == 404


@pytest.mark.parametrize(
    ("full_name", "public_name"),
    [
        ("Ahmed", "Ahmed"),
        ("Ahmed Ali", "Ahmed Ali"),
        ("  Ahmed   Mohammed   Ali  ", "Ahmed Ali"),
        ("بدر خالد الدخيل الله", "بدر الدخيل الله"),
    ],
)
def test_public_name_keeps_only_first_and_family_name(full_name, public_name):
    assert initial_public_name(full_name) == public_name
