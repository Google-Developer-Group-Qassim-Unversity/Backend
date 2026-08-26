from fastapi.testclient import TestClient

from tests.utils import assert_2xx, assert_not_found


def test_lists_the_seeded_departments(client: TestClient, seed_refs):
    response = client.get("/departments")

    assert_2xx(response)
    names = {d["name"] for d in response.json()}
    assert {"Business", "Design"} <= names


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
