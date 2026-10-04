"""The public leaderboard endpoints, and the semester visibility rules on them.

Everything here is reachable without a token - the leaderboard app reads it -
so the interesting behaviour is which semester an anonymous caller is shown.
"""

from datetime import date

from fastapi.testclient import TestClient

import app.semesters
from app.DB.schema import DepartmentsLogs, Events, EventsLocationType, EventsStatus, Logs, SemesterDepartments
from app.DB.semesters import get_semester_by_hijri_code

from tests.utils import assert_2xx, assert_not_found, semester_id_on

PUBLIC_ENDPOINTS = ["/points/members/total", "/points/departments/total"]


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
    from app.DB.semesters import get_semester_by_hijri_code

    semester = get_semester_by_hijri_code(db_session, 471)
    assert semester is not None
    semester.is_public = 0
    db_session.commit()

    response = client.get("/points/members/total?semester=471")

    assert response.status_code == 403
    assert "not publicly accessible" in response.json()["detail"]


def test_super_admin_sees_a_private_semester(super_admin_client: TestClient, db_session, seed_refs):
    from app.DB.semesters import get_semester_by_hijri_code

    semester = get_semester_by_hijri_code(db_session, 471)
    assert semester is not None
    semester.is_public = 0
    db_session.commit()

    assert_2xx(super_admin_client.get("/points/members/total?semester=471"))


def test_private_current_semester_does_not_break_anonymous_callers(
    client: TestClient, super_admin_client: TestClient, monkeypatch
):
    """Making the current semester private must not start 403ing the public
    leaderboard - it falls back to the newest public semester instead."""
    # A private Fall 2026 that has already started by the pinned day, so by the
    # calendar it - not 475 - is the current one.
    assert_2xx(
        super_admin_client.post(
            "/semesters",
            json={
                "term": "first",
                "hijri_year": 1448,
                "academic_year_start": 2026,
                "start_date": "2026-08-23",
                "end_date": "2026-12-17",
                "is_public": False,
            },
        )
    )
    monkeypatch.setattr(app.semesters, "today", lambda: date(2026, 9, 1))

    assert_2xx(client.get("/points/members/total"))
    body = semesters(client)
    assert body["current_semester"] == 475, "a private semester must not be advertised as the default"


def test_unknown_semester_is_rejected(client: TestClient, seed_refs):
    response = client.get("/points/members/total?semester=999999")
    assert response.status_code in (404, 409)


# ---------- which departments a semester's ranking lists ----------


def _department_points(client: TestClient, semester: int) -> dict[int, dict]:
    response = client.get("/points/departments/total", params={"semester": semester})
    assert_2xx(response)
    body = response.json()
    return {d["department_id"]: d for d in body["administrative"] + body["practical"]}


def _award_department_points(db_session, seed_refs, department_id: int, day: str) -> None:
    event = Events(
        name="Ranked workshop",
        location_type=EventsLocationType.ON_SITE,
        location="Hall",
        start_datetime=f"{day} 10:00:00",
        end_datetime=f"{day} 12:00:00",
        status=EventsStatus.CLOSED,
        semester_id=semester_id_on(db_session, day),
    )
    db_session.add(event)
    db_session.flush()
    log = Logs(action_id=seed_refs.dept_action.id, event_id=event.id)
    db_session.add(log)
    db_session.flush()
    db_session.add(DepartmentsLogs(department_id=department_id, log_id=log.id))
    db_session.flush()


def test_ranking_lists_the_semesters_departments_and_any_with_points(client, db_session, seed_refs):
    summer = get_semester_by_hijri_code(db_session, 475)
    db_session.add(SemesterDepartments(semester_id=summer.id, department_id=seed_refs.dept_design.id))
    db_session.flush()
    assert set(_department_points(client, 475)) == {seed_refs.dept_design.id}

    # Business is not part of 475, but points it earned there still show.
    _award_department_points(db_session, seed_refs, seed_refs.dept_business.id, "2026-07-01")
    ranking = _department_points(client, 475)
    assert set(ranking) == {seed_refs.dept_design.id, seed_refs.dept_business.id}
    assert ranking[seed_refs.dept_business.id]["total_points"] == seed_refs.dept_action.points
    # A semester the departments had nothing to do with lists neither.
    assert set(_department_points(client, 472)) == set()


def test_ranking_leaves_out_departments_not_shown_in_the_leaderboard(client, db_session, seed_refs):
    summer = get_semester_by_hijri_code(db_session, 475)
    db_session.add(SemesterDepartments(semester_id=summer.id, department_id=seed_refs.dept_design.id))
    seed_refs.dept_design.show_in_leaderboard = 0
    db_session.flush()
    _award_department_points(db_session, seed_refs, seed_refs.dept_design.id, "2026-07-01")
    # The Leadership department (part of 475 from the migration) is left out the same way.
    assert _department_points(client, 475) == {}


def test_ranking_uses_the_name_a_department_had_that_semester(client, db_session, seed_refs):
    spring = get_semester_by_hijri_code(db_session, 472)
    db_session.add(
        SemesterDepartments(
            semester_id=spring.id, department_id=seed_refs.dept_design.id, name="Old Design", ar_name="التصميم القديم"
        )
    )
    db_session.flush()
    ranking = _department_points(client, 472)[seed_refs.dept_design.id]
    assert (ranking["department_name"], ranking["ar_department_name"]) == ("Old Design", "التصميم القديم")
    assert _department_points(client, 475) == {}
