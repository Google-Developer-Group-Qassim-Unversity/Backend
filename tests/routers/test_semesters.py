"""Semesters: the admin CRUD, the generated codes, the calendar-derived current
semester, and how events are filed under a semester.

The seeded semesters come from migration c6d7e8f9a0b1 - 471 (Fall 2025),
472 (Spring 2026) and 475 (Summer 2026) - and conftest pins today to
2026-07-15, inside 475.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.semesters
from app.DB.schema import Semesters
from app.DB.semesters import get_semester_by_hijri_code
from tests.factories import make_create_event_payload, make_event
from tests.utils import (
    assert_2xx,
    assert_bad_request,
    assert_conflict,
    assert_forbidden,
    assert_not_found,
    assert_unprocessable,
)


def make_semester(**overrides):
    """Fall 2026 - 481 / 261 - which the seed does not include."""
    defaults = {
        "term": "first",
        "hijri_year": 1448,
        "academic_year_start": 2026,
        "start_date": "2026-08-23",
        "end_date": "2026-12-17",
        "is_public": True,
    }
    defaults.update(overrides)
    return defaults


def semester(db_session, hijri_code: int) -> Semesters:
    row = get_semester_by_hijri_code(db_session, hijri_code)
    assert row is not None
    return row


def as_payload(row: Semesters, **overrides):
    payload = {
        "term": row.term.value,
        "hijri_year": row.hijri_year,
        "academic_year_start": row.academic_year_start,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "is_public": bool(row.is_public),
    }
    payload.update(overrides)
    return payload


def pin_today(monkeypatch, day: str):
    monkeypatch.setattr(app.semesters, "today", lambda: date.fromisoformat(day))


# ---------- codes and names ----------


def test_list_semesters_includes_seeded_with_generated_codes(admin_client: TestClient):
    response = admin_client.get("/semesters")
    assert_2xx(response)
    rows = response.json()
    assert [(s["hijri_code"], s["gregorian_code"], s["name"]) for s in rows] == [
        (475, 253, "Summer 2026"),
        (472, 252, "Spring 2026"),
        (471, 251, "Fall 2025"),
    ]
    assert all(len(s["id"]) == 36 for s in rows), "ids are UUIDs, not term codes"


def test_list_semesters_requires_auth(client: TestClient):
    assert client.get("/semesters").status_code in (401, 403)


def test_create_semester_generates_codes_and_name(super_admin_client: TestClient):
    response = super_admin_client.post("/semesters", json=make_semester())
    assert_2xx(response)
    body = response.json()
    assert (body["hijri_code"], body["gregorian_code"], body["name"]) == (481, 261, "Fall 2026")
    assert body["is_current"] is False, "today (2026-07-15) is before its start date"


@pytest.mark.parametrize(
    ("term", "hijri_year", "academic_year_start", "codes", "name"),
    [
        ("first", 1446, 2024, (461, 241), "Fall 2024"),
        ("second", 1446, 2024, (462, 242), "Spring 2025"),
        ("summer", 1446, 2024, (465, 243), "Summer 2025"),
    ],
)
def test_code_pattern(super_admin_client, term, hijri_year, academic_year_start, codes, name):
    """Hijri code: year's last two digits + 1/2/5. Gregorian: start year's last two digits + 1/2/3."""
    response = super_admin_client.post(
        "/semesters",
        json=make_semester(
            term=term,
            hijri_year=hijri_year,
            academic_year_start=academic_year_start,
            start_date="2024-09-01",
            end_date="2024-10-01",
        ),
    )
    assert_2xx(response)
    body = response.json()
    assert (body["hijri_code"], body["gregorian_code"]) == codes
    assert body["name"] == name


def test_create_duplicate_term(super_admin_client: TestClient):
    """1447 first term is 471, already seeded."""
    response = super_admin_client.post(
        "/semesters",
        json=make_semester(hijri_year=1447, academic_year_start=2025, start_date="2027-01-01", end_date="2027-02-01"),
    )
    assert_conflict(response)


def test_create_overlapping_semester(super_admin_client: TestClient):
    assert_conflict(super_admin_client.post("/semesters", json=make_semester(start_date="2026-08-01")))


def test_create_semester_with_end_before_start(super_admin_client: TestClient):
    response = super_admin_client.post("/semesters", json=make_semester(start_date="2026-09-01", end_date="2026-08-01"))
    assert_bad_request(response)


def test_create_semester_rejects_implausible_years(super_admin_client: TestClient):
    assert_bad_request(super_admin_client.post("/semesters", json=make_semester(hijri_year=48)))


# ---------- current semester ----------


def test_public_semesters_endpoint(client: TestClient):
    response = client.get("/points/semesters")
    assert_2xx(response)
    body = response.json()
    assert body["current_semester"] == 475
    assert body["semesters"] == [475, 472, 471]
    assert body["details"][0] == {
        "id": 475,
        "gregorian_code": 253,
        "name": "Summer 2026",
        "start_date": "2026-06-28",
        "end_date": "2026-08-20",
        "is_current": True,
    }


def test_current_semester_follows_the_calendar(client: TestClient, monkeypatch):
    pin_today(monkeypatch, "2026-02-01")
    assert client.get("/points/semesters").json()["current_semester"] == 472


def test_between_terms_the_ended_term_stays_current(client: TestClient, monkeypatch):
    """472 ends 2026-05-31 and 475 starts 2026-06-28: the gap belongs to 472, not the next term."""
    pin_today(monkeypatch, "2026-06-10")
    assert client.get("/points/semesters").json()["current_semester"] == 472


def test_a_new_semester_becomes_current_on_its_start_date(super_admin_client: TestClient, monkeypatch):
    assert_2xx(super_admin_client.post("/semesters", json=make_semester()))
    pin_today(monkeypatch, "2026-08-22")
    assert super_admin_client.get("/points/semesters").json()["current_semester"] == 475
    pin_today(monkeypatch, "2026-08-23")
    assert super_admin_client.get("/points/semesters").json()["current_semester"] == 481


# ---------- update / delete ----------


def test_update_semester_dates(super_admin_client: TestClient, db_session):
    summer = semester(db_session, 475)
    response = super_admin_client.put(
        f"/semesters/{summer.id}", json=as_payload(summer, start_date="2026-06-01", end_date="2026-08-10")
    )
    assert_2xx(response)
    body = response.json()
    assert (body["id"], body["hijri_code"]) == (summer.id, 475)
    assert (body["start_date"], body["end_date"]) == ("2026-06-01", "2026-08-10")


def test_update_requires_explicit_visibility(super_admin_client: TestClient, db_session):
    """Omitting is_public must not silently flip a private semester back to public."""
    summer = semester(db_session, 475)
    payload = as_payload(summer)
    del payload["is_public"]
    assert_unprocessable(super_admin_client.put(f"/semesters/{summer.id}", json=payload))


def test_update_unknown_semester(super_admin_client: TestClient):
    response = super_admin_client.put("/semesters/00000000-0000-0000-0000-000000000000", json=make_semester())
    assert_not_found(response)


def test_semester_path_takes_a_uuid_not_a_code(super_admin_client: TestClient):
    assert_unprocessable(super_admin_client.put("/semesters/475", json=make_semester()))


def test_delete_semester_without_events(super_admin_client: TestClient):
    created = super_admin_client.post("/semesters", json=make_semester()).json()
    assert_2xx(super_admin_client.delete(f"/semesters/{created['id']}"))
    assert 481 not in super_admin_client.get("/points/semesters").json()["semesters"]


def test_cannot_delete_a_semester_with_events(admin_client, super_admin_client: TestClient, db_session, seed_refs):
    assert_2xx(admin_client.post("/events/", json=make_create_event_payload(seed_refs)))
    response = super_admin_client.delete(f"/semesters/{semester(db_session, 475).id}")
    assert_conflict(response)
    assert response.json()["code"] == "semester_has_events"


# ---------- events are filed under a semester ----------


def create_event(client: TestClient, seed_refs, **event_fields) -> dict:
    response = client.post("/events/", json=make_create_event_payload(seed_refs, event=make_event(**event_fields)))
    assert_2xx(response)
    return response.json()


def test_event_is_filed_under_the_semester_of_its_end_date(admin_client, db_session, seed_refs):
    event = create_event(
        admin_client, seed_refs, start_datetime="2026-03-01T10:00:00", end_datetime="2026-03-01T12:00:00"
    )
    assert event["semester_id"] == semester(db_session, 472).id


def test_event_between_terms_belongs_to_the_ended_term(admin_client, db_session, seed_refs):
    event = create_event(
        admin_client, seed_refs, start_datetime="2026-06-10T10:00:00", end_datetime="2026-06-10T12:00:00"
    )
    assert event["semester_id"] == semester(db_session, 472).id


def test_event_semester_can_be_chosen_explicitly(admin_client, db_session, seed_refs):
    fall = semester(db_session, 471).id
    event = create_event(
        admin_client,
        seed_refs,
        start_datetime="2026-03-01T10:00:00",
        end_datetime="2026-03-01T12:00:00",
        semester_id=fall,
    )
    assert event["semester_id"] == fall


def test_event_before_the_oldest_semester_is_refused(admin_client, seed_refs):
    event = make_event(start_datetime="2024-01-01T10:00:00", end_datetime="2024-01-01T12:00:00")
    response = admin_client.post("/events/", json=make_create_event_payload(seed_refs, event=event))
    assert_unprocessable(response)
    assert response.json()["code"] == "no_semester_for_date"


def test_editing_a_semester_never_moves_its_events(admin_client, super_admin_client, db_session, seed_refs):
    event = create_event(
        admin_client, seed_refs, start_datetime="2026-07-01T10:00:00", end_datetime="2026-07-01T12:00:00"
    )
    summer = semester(db_session, 475)
    assert event["semester_id"] == summer.id

    # Move Summer's start past the event: under the old date-window filter the
    # event would silently drop out of 475.
    assert_2xx(super_admin_client.put(f"/semesters/{summer.id}", json=as_payload(summer, start_date="2026-07-10")))

    assert super_admin_client.get(f"/events/{event['id']}").json()["semester_id"] == summer.id
    listed = super_admin_client.get("/events?semester=475").json()
    assert [e["id"] for e in listed] == [event["id"]]


def test_events_filter_by_semester(admin_client: TestClient, seed_refs):
    assert admin_client.get("/events?semester=475").json() == []
    event = create_event(admin_client, seed_refs)  # factory default ends 2026-06-29, in 475
    assert [e["id"] for e in admin_client.get("/events?semester=475").json()] == [event["id"]]
    assert admin_client.get("/events?semester=472").json() == []


# ---------- visibility ----------
# NOTE: the /points routes depend on `config.CLERK_GUARD_optional` directly rather than on
# helpers.optional_clerk_guard, which is what conftest overrides - so every /points request
# below is seen by the app as unauthenticated, whichever client fixture issues it.


def test_private_semester_is_hidden_from_public_endpoint(super_admin_client: TestClient, db_session):
    fall = semester(db_session, 471)
    assert_2xx(super_admin_client.put(f"/semesters/{fall.id}", json=as_payload(fall, is_public=False)))
    assert 471 not in super_admin_client.get("/points/semesters").json()["semesters"]


def test_public_endpoint_falls_back_when_the_current_semester_is_private(super_admin_client, db_session):
    summer = semester(db_session, 475)
    assert_2xx(super_admin_client.put(f"/semesters/{summer.id}", json=as_payload(summer, is_public=False)))
    body = super_admin_client.get("/points/semesters").json()
    assert body["current_semester"] == 472
    assert 475 not in body["semesters"]


def test_points_use_the_current_semester_by_default(client: TestClient):
    assert_2xx(client.get("/points/members/total"))
    assert_2xx(client.get("/points/departments/total"))


def test_points_reject_unknown_semester(client: TestClient):
    assert_not_found(client.get("/points/members/total?semester=999"))


def test_points_reject_a_private_semester_asked_for_by_code(client: TestClient, db_session):
    """Flipped directly rather than through the API: the auth fixtures share one
    app, so asking for `super_admin_client` here would authenticate this request too."""
    semester(db_session, 471).is_public = 0
    db_session.commit()
    assert_forbidden(client.get("/points/members/total?semester=471"))


def test_points_default_falls_back_when_the_current_semester_is_private(super_admin_client, db_session):
    """A private current semester must not 403 every default /points request."""
    summer = semester(db_session, 475)
    assert_2xx(super_admin_client.put(f"/semesters/{summer.id}", json=as_payload(summer, is_public=False)))
    assert_2xx(super_admin_client.get("/points/members/total"))
    assert_2xx(super_admin_client.get("/points/departments/total"))
