from datetime import datetime

from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx, assert_forbidden, assert_not_found, assert_bad_request
from app.DB.schema import Events, Submissions


def test_authorized_create_event(admin_client: TestClient):
    response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "my event", f"Expected event name 'my event' but got '{body['name']}'"


def test_unauthorized_create_event(clerk_client: TestClient):
    response = clerk_client.post("/events", json=make_create_event_payload())
    assert_forbidden(response)


def test_update_event_status(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    update_response = admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})
    assert_2xx(update_response)
    assert update_response.json()["status"] == "open"


def test_points_admin_create_event(admin_client: TestClient, seed_refs):
    event = admin_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert_2xx(points_response)
    points_body = points_response.json()
    assert points_body["department"]["total_points"] == seed_refs.dept_action.points, (
        f"Expected {seed_refs.dept_action.points} points for department, "
        f"got {points_body['department']['total_points']}"
    )


def test_get_all_events(admin_client: TestClient):
    event_1 = admin_client.post("/events", json=make_create_event_payload())
    event_2 = admin_client.post("/events", json=make_create_event_payload(event=make_event(name="another event")))
    event_3 = admin_client.post(
        "/events",
        json=make_create_event_payload(
            event=make_event(
                name="yet another event", start_datetime="2026-04-01T00:00:00", end_datetime="2026-04-02T00:00:00"
            )
        ),
    )
    assert_2xx(event_1)
    assert_2xx(event_2)
    assert_2xx(event_3)

    get_response = admin_client.get("/events")
    assert_2xx(get_response)
    events = get_response.json()
    assert len(events) == 3, f"Expected exactly three events in the response, but got {len(events)}"

    data = event_3.json()
    assert data["name"] == "yet another event"
    assert data["start_datetime"] == "2026-04-01T00:00:00"
    assert data["end_datetime"] == "2026-04-02T00:00:00"


def test_get_event_by_id(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    get_response = admin_client.get(f"/events/{event['id']}")
    assert_2xx(get_response)
    assert get_response.json()["id"] == event["id"]


def test_get_nonexistent_event(admin_client: TestClient):
    get_response = admin_client.get("/events/9999")
    assert_not_found(get_response)


def test_get_event_form(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload(form_type="google")).json()
    form_response = admin_client.get(f"/events/{event['id']}/form")
    assert_2xx(form_response)
    assert form_response.json()["form_type"] == "google"


def test_get_event_form_nonexistent_event(admin_client: TestClient):
    form_response = admin_client.get("/events/9999/form")
    assert_not_found(form_response)
    assert form_response.json()["detail"].startswith("Event")


def test_get_event_details(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload(form_type="google")).json()
    details_response = admin_client.get(f"/events/{event['id']}/details")
    assert_2xx(details_response)
    body = details_response.json()
    assert "event" in body
    assert "actions" in body
    assert body["event"]["id"] == event["id"]
    assert body["event"]["name"] == "my event"
    assert len(body["actions"]) == 2


def test_update_event(admin_client: TestClient, seed_refs):
    event = admin_client.post(
        "/events", json=make_create_event_payload(seed_refs=seed_refs, department_id=seed_refs.dept_business.id)
    ).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})

    dept1_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert_2xx(dept1_response)
    assert dept1_response.json()["department"]["total_points"] == seed_refs.dept_action.points

    details = admin_client.get(f"/events/{event['id']}/details").json()
    actions = details["actions"]

    update_payload = {
        "event": make_event(
            name="my event", start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-03T00:00:00", status="open"
        ),
        "actions": [
            {
                "action_id": actions[0]["action_id"],
                "ar_action_name": actions[0]["ar_action_name"],
                "department_id": seed_refs.dept_design.id,
            },
            actions[1],
        ],
    }
    update_response = admin_client.put(f"/events/{event['id']}", json=update_payload)
    assert_2xx(update_response)
    updated = update_response.json()
    assert updated["start_datetime"] == "2026-03-01T00:00:00"
    assert updated["end_datetime"] == "2026-03-03T00:00:00"

    dept1_after = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert_2xx(dept1_after)
    assert dept1_after.json()["department"]["total_points"] == 0

    dept2_response = admin_client.get(f"/points/departments/{seed_refs.dept_design.id}")
    assert_2xx(dept2_response)
    assert dept2_response.json()["department"]["total_points"] == seed_refs.dept_action.points * 3


def test_unauthorized_update_event(clerk_client: TestClient, db_session):
    event = Events(
        name="test event",
        description="test description",
        start_datetime=datetime(2026, 3, 1, 0, 0, 0),
        end_datetime=datetime(2026, 3, 2, 0, 0),
        status="draft",
        location_type="on-site",
        location="the moon",
    )
    db_session.add(event)
    db_session.commit()

    update_payload = {
        "event": make_event(
            name="updated event",
            start_datetime="2026-03-01T00:00:00",
            end_datetime="2026-03-02T00:00:00",
            status="draft",
        ),
        "actions": [],
    }
    update_response = clerk_client.put(f"/events/{event.id}", json=update_payload)
    assert_forbidden(update_response)


def test_delete_event(admin_client: TestClient, seed_refs):
    event = admin_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert_2xx(points_response)
    assert points_response.json()["department"]["total_points"] == seed_refs.dept_action.points

    admin_client.put(f"/events/{event['id']}/status", json={"status": "draft"})
    delete_response = admin_client.delete(f"/events/{event['id']}")
    assert_2xx(delete_response)
    assert delete_response.json()["detail"] == "Event deleted successfully"

    assert_not_found(admin_client.get(f"/events/{event['id']}"))

    points_after = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert_2xx(points_after)
    assert points_after.json()["department"]["total_points"] == 0


def test_unauthorized_delete_event(clerk_client: TestClient, db_session):
    event = Events(
        name="test event",
        description="test description",
        start_datetime=datetime(2026, 3, 1, 0, 0, 0),
        end_datetime=datetime(2026, 3, 2, 0, 0),
        status="draft",
        location_type="on-site",
        location="the moon",
    )
    db_session.add(event)
    db_session.commit()

    delete_response = clerk_client.delete(f"/events/{event.id}")
    assert_forbidden(delete_response)


def test_get_submissions_by_event(admin_client: TestClient, db_session, seed_refs):
    event = admin_client.post("/events", json=make_create_event_payload(form_type="registration")).json()
    form_response = admin_client.get(f"/events/{event['id']}/form")
    assert_2xx(form_response)
    form_id = form_response.json()["id"]

    db_session.add(
        Submissions(
            form_id=form_id, member_id=seed_refs.ahmed.id, submission_type="registration", is_accepted=0, is_invited=0
        )
    )
    db_session.add(
        Submissions(
            form_id=form_id, member_id=seed_refs.sara.id, submission_type="registration", is_accepted=1, is_invited=0
        )
    )
    db_session.commit()

    submissions_response = admin_client.get(f"/events/submissions/{event['id']}")
    assert_2xx(submissions_response)
    submissions = submissions_response.json()
    assert len(submissions) == 2

    s1 = next(s for s in submissions if s["member"]["id"] == seed_refs.ahmed.id)
    assert s1["submission_type"] == "registration"
    assert s1["is_accepted"] is False
    assert s1["member"]["name"] == seed_refs.ahmed.name

    s2 = next(s for s in submissions if s["member"]["id"] == seed_refs.sara.id)
    assert s2["submission_type"] == "registration"
    assert s2["is_accepted"] is True
    assert s2["member"]["name"] == seed_refs.sara.name


def test_get_submissions_empty(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload(form_type="google")).json()
    submissions_response = admin_client.get(f"/events/submissions/{event['id']}")
    assert_2xx(submissions_response)
    assert submissions_response.json() == []


def test_get_events_invalid_semester(admin_client: TestClient):
    response = admin_client.get("/events?semester=999")
    assert_bad_request(response)
    assert "not found" in response.json()["detail"].lower()


def test_get_events_valid_semester(admin_client: TestClient):
    response = admin_client.get("/events?semester=472")
    assert_2xx(response)
    assert response.json() == []


def test_get_registrable_events(admin_client: TestClient):
    response = admin_client.get("/events/open")
    assert_2xx(response)
    assert response.json() == []


def test_delete_non_draft_event(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})
    delete_response = admin_client.delete(f"/events/{event['id']}")
    assert_bad_request(delete_response)
    assert "only draft events" in delete_response.json()["detail"].lower()


def test_update_event_invalid_dates(admin_client: TestClient):
    event = admin_client.post("/events", json=make_create_event_payload()).json()
    details = admin_client.get(f"/events/{event['id']}/details").json()

    update_payload = {
        "event": make_event(status="draft", start_datetime="2026-03-05T00:00:00", end_datetime="2026-03-01T00:00:00"),
        "actions": details["actions"],
    }
    update_response = admin_client.put(f"/events/{event['id']}", json=update_payload)
    assert_bad_request(update_response)
    assert "end datetime" in update_response.json()["detail"].lower()


def test_update_nonexistent_event(admin_client: TestClient, seed_refs):
    details_payload = {
        "event": make_event(),
        "actions": [
            {
                "action_id": seed_refs.dept_action.id,
                "ar_action_name": "test",
                "department_id": seed_refs.dept_business.id,
            },
            {"action_id": seed_refs.member_action.id, "ar_action_name": "test", "department_id": None},
        ],
    }
    response = admin_client.put("/events/9999", json=details_payload)
    assert_not_found(response)


def test_delete_nonexistent_event(admin_client: TestClient):
    assert_not_found(admin_client.delete("/events/9999"))


def test_update_status_nonexistent_event(admin_client: TestClient):
    assert_not_found(admin_client.put("/events/9999/status", json={"status": "open"}))


def test_get_event_details_nonexistent_event(admin_client: TestClient):
    assert_not_found(admin_client.get("/events/9999/details"))


def test_update_event_days_increase(admin_client: TestClient, seed_refs):
    event = admin_client.post(
        "/events", json=make_create_event_payload(seed_refs=seed_refs, department_id=seed_refs.dept_business.id)
    ).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert points_response.json()["department"]["total_points"] == seed_refs.dept_action.points

    details = admin_client.get(f"/events/{event['id']}/details").json()
    update_payload = {
        "event": make_event(
            name="my event", start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-03T00:00:00", status="open"
        ),
        "actions": details["actions"],
    }
    update_response = admin_client.put(f"/events/{event['id']}", json=update_payload)
    assert_2xx(update_response)

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert points_response.json()["department"]["total_points"] == seed_refs.dept_action.points * 3


def test_update_event_days_decrease(admin_client: TestClient, seed_refs):
    event = admin_client.post(
        "/events",
        json=make_create_event_payload(
            seed_refs=seed_refs,
            department_id=seed_refs.dept_business.id,
            event=make_event(start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-03T00:00:00"),
        ),
    ).json()
    admin_client.put(f"/events/{event['id']}/status", json={"status": "open"})

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert points_response.json()["department"]["total_points"] == seed_refs.dept_action.points * 3

    details = admin_client.get(f"/events/{event['id']}/details").json()
    update_payload = {
        "event": make_event(
            name="my event", start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-01T00:00:00", status="open"
        ),
        "actions": details["actions"],
    }
    update_response = admin_client.put(f"/events/{event['id']}", json=update_payload)
    assert_2xx(update_response)

    points_response = admin_client.get(f"/points/departments/{seed_refs.dept_business.id}")
    assert points_response.json()["department"]["total_points"] == seed_refs.dept_action.points
