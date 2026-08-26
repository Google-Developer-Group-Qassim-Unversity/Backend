"""The public leaderboard endpoints, and the semester visibility rules on them.

Everything here is reachable without a token - the leaderboard app reads it -
so the interesting behaviour is which semester an anonymous caller is shown.
"""

from fastapi.testclient import TestClient

from tests.utils import assert_2xx, assert_not_found

PUBLIC_ENDPOINTS = [
    "/points/members/total",
    "/points/departments/total",
]


def semesters(client: TestClient) -> dict:
    response = client.get("/points/semesters")
    assert_2xx(response)
    return response.json()


# ---------- shape ----------


def test_semesters_advertises_a_usable_default(client: TestClient):
    body = semesters(client)
    assert body["current_semester"] in body["semesters"]
    assert {d["id"] for d in body["details"]} == set(body["semesters"])


def test_member_totals_are_a_list(client: TestClient, seed_refs):
    response = client.get("/points/members/total")

    assert_2xx(response)
    assert isinstance(response.json(), list)


def test_department_totals_are_split_by_type(client: TestClient, seed_refs):
    response = client.get("/points/departments/total")

    assert_2xx(response)
    body = response.json()
    assert set(body) == {"administrative", "practical"}
    types = {d["department_type"] for d in body["administrative"]}
    assert types <= {"administrative"}


def test_member_history_has_member_and_events(client: TestClient, seed_refs):
    response = client.get(f"/points/members/{seed_refs.ahmed.id}")

    assert_2xx(response)
    body = response.json()
    assert body["member"]["member_id"] == seed_refs.ahmed.id
    assert isinstance(body["events"], list)


def test_department_history_has_department_and_events(client: TestClient, seed_refs):
    response = client.get(f"/points/departments/{seed_refs.dept_business.id}")

    assert_2xx(response)
    body = response.json()
    assert body["department"]["department_id"] == seed_refs.dept_business.id
    assert isinstance(body["events"], list)


def test_unknown_member_is_404(client: TestClient, seed_refs):
    assert_not_found(client.get("/points/members/999999"))


def test_unknown_department_is_404(client: TestClient, seed_refs):
    assert_not_found(client.get("/points/departments/999999"))


# ---------- access without a token ----------


def test_public_endpoints_need_no_token(client: TestClient, seed_refs):
    for endpoint in PUBLIC_ENDPOINTS:
        assert_2xx(client.get(endpoint))


def test_explicit_public_semester_is_allowed(client: TestClient, seed_refs):
    public_id = semesters(client)["semesters"][0]

    for endpoint in PUBLIC_ENDPOINTS:
        assert_2xx(client.get(f"{endpoint}?semester={public_id}"))


def test_private_semester_is_refused_without_a_token(client: TestClient, db_session, seed_refs):
    """The auth fixtures share one app and one overrides dict, so a private
    semester is created directly here rather than via super_admin_client - which
    would authenticate this request too."""
    from app.DB.schema import Semesters

    semester = db_session.get(Semesters, 471)
    semester.is_public = False
    db_session.commit()

    response = client.get("/points/members/total?semester=471")

    assert response.status_code == 403
    assert "not publicly accessible" in response.json()["detail"]


def test_super_admin_sees_a_private_semester(super_admin_client: TestClient, db_session, seed_refs):
    from app.DB.schema import Semesters

    semester = db_session.get(Semesters, 471)
    semester.is_public = False
    db_session.commit()

    assert_2xx(super_admin_client.get("/points/members/total?semester=471"))


def test_private_current_semester_does_not_break_anonymous_callers(
    client: TestClient, super_admin_client: TestClient
):
    """Making the current semester private must not start 403ing the public
    leaderboard - it falls back to the newest public semester instead."""
    assert_2xx(
        super_admin_client.post(
            "/semesters",
            json={
                "id": 902,
                "name": "Hidden current",
                "start_date": "2027-07-01",
                "end_date": "2027-12-01",
                "is_public": False,
                "is_current": True,
            },
        )
    )

    assert_2xx(client.get("/points/members/total"))
    body = semesters(client)
    assert body["current_semester"] != 902, "a private semester must not be advertised as the default"


def test_unknown_semester_is_rejected(client: TestClient, seed_refs):
    response = client.get("/points/members/total?semester=999999")
    assert response.status_code in (404, 409)
