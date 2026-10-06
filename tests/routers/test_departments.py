from fastapi.testclient import TestClient

from app.DB.schema import Departments, DepartmentsType, SemesterDepartments
from app.DB.semesters import get_semester_by_hijri_code
from tests.utils import assert_2xx, assert_not_found


def test_lists_the_seeded_departments(client: TestClient, seed_refs):
    response = client.get("/departments")

    assert_2xx(response)
    names = {d["name"] for d in response.json()}
    assert {"Business", "Design"} <= names


def test_end_date_returns_that_semesters_roster(client: TestClient, db_session, seed_refs):
    """With ?end_date=, the list is the departments enrolled in the semester the
    date falls in - not every department ever."""
    spring = get_semester_by_hijri_code(db_session, 472)
    assert spring is not None
    winter_only = Departments(name="Winter Only", ar_name="شتوي", type=DepartmentsType.PRACTICAL)
    db_session.add(winter_only)
    db_session.flush()
    assert winter_only.id is not None
    db_session.add(SemesterDepartments(semester_id=spring.id, department_id=winter_only.id))
    db_session.add(SemesterDepartments(semester_id=spring.id, department_id=seed_refs.dept_business.id))
    db_session.flush()

    response = client.get("/departments", params={"end_date": "2026-02-01"})

    assert_2xx(response)
    ids = {d["id"] for d in response.json()}
    assert {winter_only.id, seed_refs.dept_business.id} <= ids
    assert seed_refs.dept_design.id not in ids


def test_end_date_excludes_departments_hidden_from_the_leaderboard(client: TestClient, db_session, seed_refs):
    spring = get_semester_by_hijri_code(db_session, 472)
    assert spring is not None
    hidden = Departments(
        name="Hidden Board", ar_name="مجلس خفي", type=DepartmentsType.ADMINISTRATIVE, show_in_leaderboard=0
    )
    db_session.add(hidden)
    db_session.flush()
    assert hidden.id is not None
    db_session.add(SemesterDepartments(semester_id=spring.id, department_id=hidden.id))
    db_session.add(SemesterDepartments(semester_id=spring.id, department_id=seed_refs.dept_design.id))
    db_session.flush()

    response = client.get("/departments", params={"end_date": "2026-02-01"})

    assert_2xx(response)
    ids = {d["id"] for d in response.json()}
    assert ids == {seed_refs.dept_design.id}


def test_end_date_without_a_matching_semester_is_422(client: TestClient):
    response = client.get("/departments", params={"end_date": "2000-01-01"})

    assert response.status_code == 422
    assert "No semester had started" in response.json()["detail"]


def test_without_end_date_every_department_is_returned(client: TestClient, db_session, seed_refs):
    """No ?end_date= keeps the old, unfiltered behaviour of the endpoint."""
    response = client.get("/departments")

    assert_2xx(response)
    ids = {d["id"] for d in response.json()}
    assert {seed_refs.dept_business.id, seed_refs.dept_design.id} <= ids


def test_department_payload_shape(client: TestClient, seed_refs):
    response = client.get(f"/departments/{seed_refs.dept_business.id}")

    assert_2xx(response)
    body = response.json()
    assert body["id"] == seed_refs.dept_business.id
    assert body["name"] == "Business"
    assert body["ar_name"] == "ريادة الأعمال"
    assert body["type"] == "practical"


def test_unknown_department_is_404(client: TestClient, seed_refs):
    assert_not_found(client.get("/departments/999999"))


def test_departments_are_public(client: TestClient, seed_refs):
    """The leaderboard app reads these without a token; keep them open."""
    assert_2xx(client.get("/departments"))
