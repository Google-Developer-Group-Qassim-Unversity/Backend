from fastapi.testclient import TestClient

from tests.utils import (
    assert_2xx,
    assert_bad_request,
    assert_conflict,
    assert_forbidden,
    assert_not_found,
    assert_unprocessable,
)


def make_semester(**overrides):
    defaults = {
        "id": 481,
        "name": "Fall 2026",
        "start_date": "2026-08-30",
        "end_date": "2027-01-14",
        "is_public": True,
        "is_current": False,
    }
    defaults.update(overrides)
    return defaults


def test_list_semesters_includes_seeded(admin_client: TestClient):
    response = admin_client.get("/semesters")
    assert_2xx(response)
    ids = [semester["id"] for semester in response.json()]
    assert ids == [475, 472, 471]


def test_list_semesters_requires_auth(client: TestClient):
    assert client.get("/semesters").status_code in (401, 403)


def test_public_semesters_endpoint_reads_from_db(client: TestClient):
    response = client.get("/points/semesters")
    assert_2xx(response)
    body = response.json()
    assert body["current_semester"] == 475
    assert body["semesters"] == [475, 472, 471]
    assert body["details"][0]["start_date"] == "2026-06-28"


def test_create_semester(super_admin_client: TestClient):
    response = super_admin_client.post("/semesters", json=make_semester())
    assert_2xx(response)
    body = response.json()
    assert body["id"] == 481
    assert body["start_date"] == "2026-08-30"
    assert body["is_current"] is False


def test_create_duplicate_semester(super_admin_client: TestClient):
    assert_conflict(super_admin_client.post("/semesters", json=make_semester(id=475)))


def test_create_semester_with_end_before_start(super_admin_client: TestClient):
    response = super_admin_client.post("/semesters", json=make_semester(start_date="2026-09-01", end_date="2026-08-01"))
    assert_bad_request(response)


def test_create_semester_as_current_demotes_the_old_one(super_admin_client: TestClient):
    assert_2xx(super_admin_client.post("/semesters", json=make_semester(is_current=True)))

    semesters = {semester["id"]: semester for semester in super_admin_client.get("/points/semesters").json()["details"]}
    assert semesters[481]["is_current"] is True
    assert semesters[475]["is_current"] is False


def test_update_semester_dates(super_admin_client: TestClient):
    response = super_admin_client.put(
        "/semesters/475",
        json={"name": "Summer 2026", "start_date": "2026-06-01", "end_date": "2026-09-10", "is_public": True},
    )
    assert_2xx(response)
    assert response.json()["start_date"] == "2026-06-01"
    assert response.json()["end_date"] == "2026-09-10"


def test_update_requires_explicit_visibility(super_admin_client: TestClient):
    """Omitting is_public must not silently flip a private semester back to public."""
    response = super_admin_client.put(
        "/semesters/475", json={"name": "Summer 2026", "start_date": "2026-06-28", "end_date": "2026-08-20"}
    )
    assert_unprocessable(response)


def test_update_unknown_semester(super_admin_client: TestClient):
    response = super_admin_client.put(
        "/semesters/999", json={"start_date": "2026-06-01", "end_date": "2026-09-10", "is_public": True}
    )
    assert_not_found(response)


def test_set_current_semester(super_admin_client: TestClient):
    assert_2xx(super_admin_client.put("/semesters/471/current"))

    semesters = {semester["id"]: semester for semester in super_admin_client.get("/points/semesters").json()["details"]}
    assert semesters[471]["is_current"] is True
    assert semesters[475]["is_current"] is False
    assert super_admin_client.get("/points/semesters").json()["current_semester"] == 471


def test_delete_semester(super_admin_client: TestClient):
    assert_2xx(super_admin_client.post("/semesters", json=make_semester()))
    assert_2xx(super_admin_client.delete("/semesters/481"))
    assert 481 not in [s["id"] for s in super_admin_client.get("/points/semesters").json()["details"]]


def test_cannot_delete_current_semester(super_admin_client: TestClient):
    assert_bad_request(super_admin_client.delete("/semesters/475"))


def test_private_semester_is_hidden_from_public_endpoint(super_admin_client: TestClient):
    assert_2xx(
        super_admin_client.put(
            "/semesters/471",
            json={"name": "Fall 2025", "start_date": "2025-08-24", "end_date": "2026-01-17", "is_public": False},
        )
    )
    assert 471 not in super_admin_client.get("/points/semesters").json()["semesters"]


def test_public_endpoint_falls_back_when_the_current_semester_is_private(super_admin_client: TestClient):
    assert_2xx(
        super_admin_client.put(
            "/semesters/475",
            json={"name": "Summer 2026", "start_date": "2026-06-28", "end_date": "2026-08-20", "is_public": False},
        )
    )
    body = super_admin_client.get("/points/semesters").json()
    assert body["current_semester"] == 472
    assert 475 not in body["semesters"]


def test_points_use_the_current_semester_by_default(client: TestClient):
    """No ?semester means "whatever is flagged current" - resolved per request, not at import time."""
    assert_2xx(client.get("/points/members/total"))
    assert_2xx(client.get("/points/departments/total"))


def test_points_reject_unknown_semester(client: TestClient):
    assert_not_found(client.get("/points/members/total?semester=999"))


# NOTE: the /points routes depend on `config.CLERK_GUARD_optional` directly rather than on
# helpers.optional_clerk_guard, which is what conftest overrides - so every /points request
# below is seen by the app as unauthenticated, whichever client fixture issues it.


def test_super_admin_can_read_a_private_semester(super_admin_client: TestClient):
    """This file previously asserted the super admin got a 403 here, which
    contradicts `_validate_semester_access`. It passed only because the
    super_admin_client fixture did not make the optional Clerk guard return
    super-admin credentials, so the route saw a plain member instead.
    """
    assert_2xx(
        super_admin_client.put(
            "/semesters/471",
            json={"name": "Fall 2025", "start_date": "2025-08-24", "end_date": "2026-01-17", "is_public": False},
        )
    )
    assert_2xx(super_admin_client.get("/points/members/total?semester=471"))


def test_points_reject_a_private_semester_asked_for_by_id(client: TestClient, db_session):
    """Anonymous callers must not see a private semester.

    The flag is flipped directly rather than through the API, because the auth
    fixtures all share one app and one dependency_overrides dict - asking for
    `super_admin_client` here would authenticate this request too.
    """
    from app.DB.schema import Semesters

    semester = db_session.get(Semesters, 471)
    semester.is_public = False
    db_session.commit()

    assert_forbidden(client.get("/points/members/total?semester=471"))


def test_points_default_falls_back_when_the_current_semester_is_private(super_admin_client: TestClient):
    """A private current semester must not 403 every default /points request."""
    assert_2xx(
        super_admin_client.put(
            "/semesters/475",
            json={"name": "Summer 2026", "start_date": "2026-06-28", "end_date": "2026-08-20", "is_public": False},
        )
    )
    assert_2xx(super_admin_client.get("/points/members/total"))
    assert_2xx(super_admin_client.get("/points/departments/total"))


def test_events_filter_uses_db_semester_dates(admin_client: TestClient):
    response = admin_client.get("/events?semester=475")
    assert_2xx(response)
    assert response.json() == []
