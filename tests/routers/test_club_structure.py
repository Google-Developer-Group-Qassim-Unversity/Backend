"""Exercise real guards and the Step 2 services through the HTTP boundary."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import config
from app.DB.schema import ClubAssignments, Departments, Members, Role
from app.main import app
from app.routers import club_structure as router
from app.services import club_structure as service

PREFIX = "/club-structure"
SETTINGS = {"name": "Robotics", "ar_name": "الروبوتات", "type": "practical", "color": "#123abc", "icon": "bot"}
READS = ["", "/departments/{department_id}", "/departments/{department_id}/roster", "/history"]
WRITES = [
    ("POST", "/departments", SETTINGS),
    ("PUT", "/departments/{department_id}", SETTINGS),
    ("POST", "/departments/{department_id}/archive", None),
    ("POST", "/departments/{department_id}/restore", None),
    ("POST", "/departments/{department_id}/members", {"member_id": 1}),
    ("DELETE", "/departments/{department_id}/members/{member_id}?expected_assignment_id=1", None),
    ("PUT", "/departments/{department_id}/leadership/leader", {"member_id": 1, "expected_assignment_id": None}),
    ("PUT", "/presidents/1", {"member_id": 1, "expected_assignment_id": None}),
]


@pytest.fixture(autouse=True)
def cache_reset(monkeypatch):
    reset = Mock(return_value={"revalidated": True})
    monkeypatch.setattr(router, "reset_leaderboard_cache", reset)
    return reset


@pytest.fixture
def sign_in(client):
    """Override JWT verification only; the application's actual guards still run."""
    bearer = config.CLERK_GUARD

    def sign_in_as(role="super", subject="clerk_structure_admin"):
        metadata = {
            "member": {},
            "admin": {"is_admin": True},
            "points": {"is_admin_points": True},
            "super": {"is_super_admin": True},
        }[role]
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="test-token", decoded={"sub": subject, "metadata": metadata}
        )
        app.dependency_overrides[bearer] = lambda: credentials
        return client

    yield sign_in_as
    app.dependency_overrides.pop(bearer, None)


def url(path, refs):
    return PREFIX + path.format(department_id=refs.dept_design.id, member_id=refs.ahmed.id)


def add(client, department_id, member_id):
    response = client.post(f"{PREFIX}/departments/{department_id}/members", json={"member_id": member_id})
    assert response.status_code == 201, response.text
    return response.json()


def replace(client, path, member_id, expected=None):
    return client.put(PREFIX + path, json={"member_id": member_id, "expected_assignment_id": expected})


@pytest.mark.parametrize("path", READS)
def test_reads_reject_anonymous_callers(client, seed_refs, path):
    assert client.get(url(path, seed_refs)).status_code == 403


@pytest.mark.parametrize("role", ["member", "admin", "points", "super"])
@pytest.mark.parametrize("path", READS)
def test_read_permissions_use_real_admin_guard(sign_in, seed_refs, path, role):
    response = sign_in(role).get(url(path, seed_refs))
    assert response.status_code == (403 if role == "member" else 200), response.text


@pytest.mark.parametrize("method,path,payload", WRITES)
def test_writes_reject_anonymous_callers(client, seed_refs, method, path, payload):
    assert client.request(method, url(path, seed_refs), json=payload).status_code == 403


@pytest.mark.parametrize("role", ["member", "admin", "points"])
@pytest.mark.parametrize("method,path,payload", WRITES)
def test_writes_reject_non_super_admins(sign_in, seed_refs, role, method, path, payload, db_session):
    response = sign_in(role).request(method, url(path, seed_refs), json=payload)
    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(ClubAssignments)) == 0


def test_empty_overview_has_two_equal_vacant_seats_and_zero_counts(sign_in, seed_refs):
    body = sign_in("admin").get(PREFIX).json()
    assert body["presidents"] == [{"slot": 1, "assignment": None}, {"slot": 2, "assignment": None}]
    assert body["total_members"] == 0
    assert {d["id"] for d in body["departments"]} == {seed_refs.dept_design.id, seed_refs.dept_business.id}
    assert all(d["member_count"] == 0 and d["leader"] is None and d["deputy"] is None for d in body["departments"])


def test_public_structure_is_anonymous_active_and_display_only(sign_in, seed_refs, db_session):
    client = sign_in()
    design = seed_refs.dept_design
    business = seed_refs.dept_business
    design.name = "Operations"
    design.ar_name = "قسم التشغيل"
    design.color = "#22c55e"
    design.icon = "users"
    seed_refs.ahmed.name = "Ahmed Mohammed Ali"
    seed_refs.sara.name = "Sara Abdullah Khalid"
    board = Departments(
        name="Board of Directors",
        ar_name="مجلس الإدارة",
        type=design.type,
        color="#4285f4",
        icon="users",
        leadership_enabled=0,
    )
    db_session.add(board)
    db_session.flush()

    add(client, design.id, seed_refs.ahmed.id)
    assert replace(client, f"/departments/{design.id}/leadership/leader", seed_refs.sara.id).status_code == 200
    add(client, business.id, seed_refs.sara.id)
    add(client, board.id, seed_refs.ahmed.id)
    assert replace(client, "/presidents/1", seed_refs.ahmed.id).status_code == 200

    app.dependency_overrides.pop(config.CLERK_GUARD, None)
    response = client.get(PREFIX + "/public")
    assert response.status_code == 200
    body = response.json()
    assert body["presidents"] == ["Ahmed Ali"]
    cards = {department["id"]: department for department in body["departments"]}
    assert set(cards) == {design.id, business.id, board.id}
    assert cards[design.id] == {
        "id": design.id,
        "name": "Operations",
        "ar_name": "قسم التشغيل",
        "type": design.type.value,
        "color": "#22c55e",
        "icon": "users",
        "leadership_enabled": True,
        "leader": "Sara Khalid",
        "deputy": None,
        "members": ["Ahmed Ali"],
    }
    assert cards[business.id]["members"] == ["Sara Khalid"]
    assert cards[board.id]["members"] == ["Ahmed Ali", "جود الفرم"]
    for private_field in ("member_id", "role", "starts_at", "ends_at", "changed_by", "ended_by"):
        assert private_field not in response.text


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
    assert router._public_name(full_name) == public_name


def test_super_admin_creates_and_updates_department_and_refreshes_public_cache(sign_in, db_session, cache_reset):
    client = sign_in()
    response = client.post(f"{PREFIX}/departments", json=SETTINGS)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["active"] is created["leadership_enabled"] is True
    assert created["created_at"].endswith("Z")
    assert created["updated_at"].endswith("Z")
    response = client.put(f"{PREFIX}/departments/{created['id']}", json={**SETTINGS, "name": "Robotics Lab"})
    assert response.status_code == 200
    assert response.json()["created_at"] == created["created_at"]
    assert db_session.get(Departments, created["id"]).name == "Robotics Lab"
    assert cache_reset.call_count == 2


def test_assignment_actor_comes_from_clerk_without_creating_a_member(sign_in, seed_refs, db_session, monkeypatch):
    client = sign_in(subject="clerk_without_member_row")
    monkeypatch.setattr(service, "_now", lambda: datetime(2026, 1, 1, 12, 0, 0, 123456))
    before = db_session.scalar(select(func.count()).select_from(Members))
    assignment = add(client, seed_refs.dept_design.id, seed_refs.ahmed.id)
    assert assignment["changed_by"] == "clerk_without_member_row"
    assert assignment["starts_at"] == "2026-01-01T12:00:00.123456Z"
    assert assignment["ends_at"] is assignment["ended_by"] is None
    assert assignment["member"] == {"id": seed_refs.ahmed.id, "name": seed_refs.ahmed.name}
    assert "current_scope_id" not in assignment
    assert db_session.scalar(select(func.count()).select_from(Members)) == before
    assert db_session.scalars(select(Role)).all() == []


@pytest.mark.parametrize("role", ["leader", "deputy"])
def test_leadership_replacement_retains_roster_and_detects_stale_request(sign_in, seed_refs, role):
    client = sign_in()
    department = seed_refs.dept_design.id
    path = f"/departments/{department}/leadership/{role}"
    first = replace(client, path, seed_refs.ahmed.id)
    assert first.status_code == 200, first.text
    old = first.json()
    membership = add(client, department, seed_refs.sara.id)
    sign_in(subject="clerk_replacing_admin")
    replacement = replace(client, path, seed_refs.sara.id, old["id"])
    assert replacement.status_code == 200, replacement.text
    assert replace(client, path, seed_refs.ahmed.id, old["id"]).status_code == 409
    assert replace(client, path, seed_refs.ahmed.id).status_code == 409
    roster = client.get(f"{PREFIX}/departments/{department}/roster").json()
    assert {(a["member_id"], a["role"]) for a in roster} == {(seed_refs.ahmed.id, "member"), (seed_refs.sara.id, role)}
    history = client.get(f"{PREFIX}/history", params={"department_id": department}).json()["items"]
    closed = {a["id"]: a for a in history if a["ends_at"] is not None}
    assert closed[old["id"]]["ends_at"] == replacement.json()["starts_at"] == closed[membership["id"]]["ends_at"]
    assert closed[old["id"]]["changed_by"] == "clerk_structure_admin"
    assert closed[old["id"]]["ended_by"] == "clerk_replacing_admin"
    cleared = replace(client, path, None, replacement.json()["id"])
    assert cleared.status_code == 200 and cleared.json() is None
    assert all(a["role"] == "member" for a in client.get(f"{PREFIX}/departments/{department}/roster").json())


def test_removal_requires_exact_current_assignment_and_records_actor(sign_in, seed_refs):
    client = sign_in()
    department = seed_refs.dept_design.id
    assignment = add(client, department, seed_refs.ahmed.id)
    path = f"{PREFIX}/departments/{department}/members/{seed_refs.ahmed.id}"
    assert client.delete(path).status_code == 422
    assert client.delete(path, params={"expected_assignment_id": assignment["id"] + 1}).status_code == 409
    response = client.delete(path, params={"expected_assignment_id": assignment["id"]})
    assert response.status_code == 200
    assert response.json()["ended_by"] == "clerk_structure_admin"
    assert response.json()["ends_at"].endswith("Z")
    rejoined = add(client, department, seed_refs.ahmed.id)
    assert client.delete(path, params={"expected_assignment_id": assignment["id"]}).status_code == 409
    assert rejoined["id"] != assignment["id"]


def test_overview_counts_distinct_people_and_populates_leadership_cards(sign_in, seed_refs):
    client = sign_in()
    design, business = seed_refs.dept_design.id, seed_refs.dept_business.id
    add(client, design, seed_refs.ahmed.id)
    add(client, business, seed_refs.ahmed.id)
    assert replace(client, f"/departments/{design}/leadership/deputy", seed_refs.sara.id).status_code == 200
    assert replace(client, "/presidents/2", seed_refs.ahmed.id).status_code == 200
    body = client.get(PREFIX).json()
    cards = {d["id"]: d for d in body["departments"]}
    assert body["total_members"] == 2
    assert cards[design]["member_count"] == 2
    assert cards[business]["member_count"] == 1
    assert cards[design]["deputy"]["member"]["id"] == seed_refs.sara.id
    assert body["presidents"][0]["assignment"] is None
    assert body["presidents"][1]["assignment"]["member_id"] == seed_refs.ahmed.id


def test_president_conflicts_rollback_and_board_membership_is_independent(sign_in, seed_refs, db_session):
    client = sign_in()
    board = seed_refs.dept_design
    board.leadership_enabled = 0
    db_session.flush()
    membership = add(client, board.id, seed_refs.ahmed.id)
    first = replace(client, "/presidents/1", seed_refs.ahmed.id).json()
    second = replace(client, "/presidents/2", seed_refs.sara.id).json()
    assert replace(client, "/presidents/1", seed_refs.sara.id).status_code == 409
    conflict = replace(client, "/presidents/1", seed_refs.sara.id, first["id"])
    assert conflict.status_code == 409
    assert "already holds" in conflict.json()["detail"]
    assert [s["assignment"]["id"] for s in client.get(PREFIX).json()["presidents"]] == [first["id"], second["id"]]
    assert replace(client, "/presidents/2", None, first["id"]).status_code == 409
    assert replace(client, "/presidents/1", None, first["id"]).status_code == 200
    assert client.get(f"{PREFIX}/departments/{board.id}/roster").json()[0]["id"] == membership["id"]


@pytest.mark.parametrize("role", ["leader", "deputy"])
def test_disabled_leadership_uses_flag_and_settings_cannot_enable_it(sign_in, seed_refs, db_session, role):
    department = seed_refs.dept_design
    department.leadership_enabled = 0
    department.created_at = None
    db_session.flush()
    client = sign_in()
    path = f"/departments/{department.id}"
    response = replace(client, path + f"/leadership/{role}", seed_refs.ahmed.id)
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]
    assert client.put(PREFIX + path, json={**SETTINGS, "leadership_enabled": True}).status_code == 422
    response = client.put(PREFIX + path, json=SETTINGS)
    assert response.status_code == 200
    assert response.json()["leadership_enabled"] is False
    assert response.json()["created_at"] is None
    add(client, department.id, seed_refs.ahmed.id)


def test_archive_blocks_roster_mutations_but_preserves_reads_and_restores(sign_in, seed_refs, cache_reset):
    client = sign_in()
    department = seed_refs.dept_design.id
    path = f"/departments/{department}"
    original = replace(client, path + "/leadership/leader", seed_refs.ahmed.id).json()
    response = client.post(PREFIX + path + "/archive")
    assert response.status_code == 200 and response.json()["active"] is False
    assert client.post(PREFIX + path + "/members", json={"member_id": seed_refs.sara.id}).status_code == 409
    assert replace(client, path + "/leadership/leader", seed_refs.sara.id, original["id"]).status_code == 409
    assert replace(client, path + "/leadership/leader", None, original["id"]).status_code == 409
    assert (
        client.delete(
            PREFIX + path + f"/members/{seed_refs.ahmed.id}", params={"expected_assignment_id": original["id"]}
        ).status_code
        == 409
    )
    assert client.get(PREFIX + path).status_code == 200
    assert client.get(PREFIX + path + "/roster").json()[0]["id"] == original["id"]
    assert client.get(PREFIX + "/history", params={"department_id": department}).json()["items"][0]["ends_at"] is None
    assert client.get(PREFIX).json()["total_members"] == 0
    archived = client.get(PREFIX, params={"include_archived": True}).json()
    assert archived["total_members"] == 1
    assert next(d for d in archived["departments"] if d["id"] == department)["leader"]["id"] == original["id"]
    restored = client.post(PREFIX + path + "/restore")
    assert restored.status_code == 200 and restored.json()["active"] is True
    assert replace(client, path + "/leadership/leader", seed_refs.sara.id, original["id"]).status_code == 200
    assert cache_reset.call_count == 4


def test_history_filters_and_pagination(sign_in, seed_refs):
    client = sign_in()
    department = seed_refs.dept_design.id
    first = replace(client, f"/departments/{department}/leadership/leader", seed_refs.ahmed.id).json()
    assert (
        replace(client, f"/departments/{department}/leadership/leader", seed_refs.sara.id, first["id"]).status_code
        == 200
    )
    assert replace(client, "/presidents/1", seed_refs.ahmed.id).status_code == 200
    params = {"department_id": department, "limit": 2}
    page = client.get(PREFIX + "/history", params=params).json()
    assert len(page["items"]) == 2 and page["has_more"] is True
    last = client.get(PREFIX + "/history", params={**params, "offset": 2}).json()
    assert len(last["items"]) == 1 and last["has_more"] is False
    assert {a["id"] for a in page["items"]}.isdisjoint(a["id"] for a in last["items"])
    filtered = client.get(PREFIX + "/history", params={"member_id": seed_refs.ahmed.id, "role": "leader"}).json()
    assert [a["id"] for a in filtered["items"]] == [first["id"]]
    assert len(client.get(PREFIX + "/history", params={"role": "president"}).json()["items"]) == 1


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"department_id": 0}, {"member_id": 0}, {"role": "owner"}]
)
def test_history_validates_filters(sign_in, params):
    assert sign_in().get(PREFIX + "/history", params=params).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"member_id": 1},
        {"expected_assignment_id": None},
        {"member_id": True, "expected_assignment_id": None},
        {"member_id": "1", "expected_assignment_id": None},
        {"member_id": 1, "expected_assignment_id": False},
        {"member_id": 1, "expected_assignment_id": None, "changed_by": "forged"},
        {"member_id": 1, "expected_assignment_id": None, "starts_at": "2020-01-01"},
    ],
)
def test_replacements_require_explicit_valid_ids_and_forbid_spoofing(sign_in, payload):
    assert sign_in().put(PREFIX + "/presidents/1", json=payload).status_code == 422


@pytest.mark.parametrize("payload", [{"member_id": 0}, {"member_id": True}, {"member_id": 1, "changed_by": "forged"}])
def test_membership_payload_rejects_invalid_ids_and_actor_spoofing(sign_in, seed_refs, payload):
    assert sign_in().post(f"{PREFIX}/departments/{seed_refs.dept_design.id}/members", json=payload).status_code == 422


@pytest.mark.parametrize("subject", [None, "", " ", 123])
def test_assignment_writes_require_valid_clerk_subject(sign_in, seed_refs, subject):
    response = sign_in(subject=subject).post(
        f"{PREFIX}/departments/{seed_refs.dept_design.id}/members", json={"member_id": seed_refs.ahmed.id}
    )
    assert response.status_code == 401


def test_unknown_records_return_404_and_invalid_scopes_return_422(sign_in, seed_refs):
    client = sign_in()
    unknown = 4294967295
    for suffix in ("", "/roster"):
        assert client.get(f"{PREFIX}/departments/{unknown}{suffix}").status_code == 404
    assert client.get(f"{PREFIX}/history", params={"department_id": unknown}).status_code == 404
    assert client.get(f"{PREFIX}/history", params={"member_id": unknown}).status_code == 404
    assert client.put(f"{PREFIX}/departments/{unknown}", json=SETTINGS).status_code == 404
    assert client.post(f"{PREFIX}/departments/{unknown}/archive").status_code == 404
    assert client.post(f"{PREFIX}/departments/{unknown}/restore").status_code == 404
    assert (
        client.post(f"{PREFIX}/departments/{unknown}/members", json={"member_id": seed_refs.ahmed.id}).status_code
        == 404
    )
    assert (
        client.post(f"{PREFIX}/departments/{seed_refs.dept_design.id}/members", json={"member_id": unknown}).status_code
        == 404
    )
    assert replace(client, "/presidents/1", unknown).status_code == 404
    assert replace(client, "/presidents/3", seed_refs.ahmed.id).status_code == 422
    assert (
        replace(client, f"/departments/{seed_refs.dept_design.id}/leadership/president", seed_refs.ahmed.id).status_code
        == 422
    )
    assert client.get(f"{PREFIX}/departments/0/roster").status_code == 422


def test_update_requires_full_settings_and_rejects_protected_fields(sign_in, seed_refs):
    client = sign_in()
    path = f"{PREFIX}/departments/{seed_refs.dept_design.id}"
    assert client.put(path, json={"name": "Name", "ar_name": "اسم", "type": "practical"}).status_code == 422
    for protected in ("id", "created_at", "updated_at", "active", "leadership_enabled"):
        assert client.put(path, json={**SETTINGS, protected: 1}).status_code == 422


def test_cache_failure_does_not_turn_committed_change_into_error(sign_in, seed_refs, db_session, cache_reset):
    cache_reset.side_effect = RuntimeError("cache unavailable")
    response = sign_in().post(f"{PREFIX}/departments/{seed_refs.dept_design.id}/archive")
    assert response.status_code == 200 and response.json()["active"] is False
    db_session.refresh(seed_refs.dept_design)
    assert seed_refs.dept_design.active == 0


def test_commit_failure_rolls_back_mutation_without_cache_refresh(sign_in, db_session, cache_reset, monkeypatch):
    def failed_commit(session):
        raise OperationalError("COMMIT", {}, Exception("connection lost"))

    monkeypatch.setattr(Session, "commit", failed_commit)
    response = sign_in().post(PREFIX + "/departments", json=SETTINGS)
    assert response.status_code == 503
    assert db_session.scalar(select(Departments).where(Departments.name == SETTINGS["name"])) is None
    cache_reset.assert_not_called()


def test_existing_public_departments_keep_original_payload(client, seed_refs):
    for body in (client.get("/departments").json()[0], client.get(f"/departments/{seed_refs.dept_design.id}").json()):
        assert set(body) == {"id", "name", "ar_name", "type"}


def test_openapi_describes_required_replacement_fields_and_bounded_history(client):
    schema = client.get("/openapi.json").json()
    replacement = schema["components"]["schemas"]["ReplaceAssignmentRequest"]
    assert set(replacement["required"]) == {"member_id", "expected_assignment_id"}
    assert replacement["additionalProperties"] is False
    history = schema["paths"]["/club-structure/history"]["get"]
    limit = next(p for p in history["parameters"] if p["name"] == "limit")
    assert limit["schema"]["maximum"] == 100


def test_repeated_archive_restore_retains_points_and_assignment_history(sign_in, seed_refs, outbound):
    """Club status must preserve both explicit tenure and legacy event points."""
    from tests.factories import make_create_event_payload, make_event

    client = sign_in()
    department_id = seed_refs.dept_design.id
    path = f"{PREFIX}/departments/{department_id}"
    semesters = client.get("/points/semesters").json()
    semester_id = semesters["current_semester"]
    semester = next(s for s in semesters["details"] if s["id"] == semester_id)
    event_date = (datetime.fromisoformat(semester["start_date"]) + timedelta(days=1)).isoformat()
    original = replace(client, f"/departments/{department_id}/leadership/leader", seed_refs.ahmed.id).json()
    response = client.post(
        "/events/",
        json=make_create_event_payload(
            seed_refs,
            event=make_event(status="open", start_datetime=event_date, end_datetime=event_date),
            department_id=department_id,
        ),
    )
    assert response.status_code == 201, response.text

    def department_totals():
        response = client.get("/points/departments/total", params={"semester": semester_id})
        assert response.status_code == 200, response.text
        return [row for rows in response.json().values() for row in rows]

    points_before = next(row for row in department_totals() if row["department_id"] == department_id)
    assert points_before["total_points"] == seed_refs.dept_action.points
    event_history_before = client.get(f"/points/departments/{department_id}", params={"semester": semester_id}).json()
    assert event_history_before["events"][0]["event_id"] == response.json()["id"]
    tenure_before = client.get(f"{PREFIX}/history", params={"department_id": department_id}).json()

    for _ in range(2):
        assert client.post(path + "/archive").status_code == 200
        assert all(row["department_id"] != department_id for row in department_totals())
        assert client.get(path + "/roster").json() == [original]
        assert client.get(f"{PREFIX}/history", params={"department_id": department_id}).json() == tenure_before
        assert client.post(path + "/restore").status_code == 200
        assert next(row for row in department_totals() if row["department_id"] == department_id) == points_before
        assert (
            client.get(f"/points/departments/{department_id}", params={"semester": semester_id}).json()
            == event_history_before
        )
        assert client.get(path + "/roster").json() == [original]
