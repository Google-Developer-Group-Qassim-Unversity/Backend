from datetime import datetime

from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx, assert_forbidden, assert_not_found
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
    # 1. create event
    create_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. update event status to "active"
    update_response = admin_client.put(f"/events/{event_id}/status", json={"status": "open"})
    assert_2xx(update_response)
    updated_body = update_response.json()
    assert updated_body["status"] == "open", f"Expected event status 'active' but got '{updated_body['status']}'"


def test_points_admin_create_event(admin_client: TestClient):
    # 1. create event using default action
    event_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(event_response)

    # 2. update event status to "open" to trigger points allocation
    update_response = admin_client.put(f"/events/{event_response.json()['id']}/status", json={"status": "open"})
    assert_2xx(update_response)

    # 4. assert that points were given to the department
    points_response = admin_client.get("/points/departments/1")
    assert_2xx(points_response)
    points_body = points_response.json()
    assert points_body["department"]["total_points"] == 10, (
        f"Expected 10 points for department, got {points_body['department']['total_points']}, \nFull response: {points_body}"
    )


def test_get_all_events(admin_client: TestClient):
    # 1. create 3 events
    event_1 = admin_client.post("/events", json=make_create_event_payload())
    event_2 = admin_client.post("/events", json=make_create_event_payload(overrides={"name": "another event"}))
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

    # 2. get all events
    get_response = admin_client.get("/events")
    assert_2xx(get_response)
    events = get_response.json()
    assert len(events) == 3, f"Expected exactly three events in the response, but got {len(events)}"

    # 3. assert response contains the created events
    data = event_3.json()
    assert data["name"] == "yet another event", f"Expected event name 'yet another event' but got '{data['name']}'"
    assert data["start_datetime"] == "2026-04-01T00:00:00", (
        f"Expected event start_datetime '2026-04-01T00:00:00' but got '{data['start_datetime']}'"
    )
    assert data["end_datetime"] == "2026-04-02T00:00:00", (
        f"Expected event end_datetime '2026-04-02T00:00:00' but got '{data['end_datetime']}'"
    )


def test_get_event_by_id(admin_client: TestClient):
    # 1. create an event
    event_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    # 2. get the event by id
    get_response = admin_client.get(f"/events/{event_id}")
    assert_2xx(get_response)
    data = get_response.json()
    assert data["id"] == event_id, f"Expected event id '{event_id}' but got '{data['id']}'"


def test_get_nonexistent_event(admin_client: TestClient):
    get_response = admin_client.get("/events/9999")
    assert_not_found(get_response)


def test_get_event_form(admin_client: TestClient):
    # 1. create an event
    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    # 2. get the event form
    form_response = admin_client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    form_data = form_response.json()
    assert form_data["form_type"] == "google", f"Expected form type 'google' but got '{form_data['form_type']}'"


def test_get_event_form_nonexistent_event(admin_client: TestClient):
    form_response = admin_client.get("/events/9999/form")
    assert_not_found(form_response)
    data = form_response.json()
    assert data["detail"].startswith("Event"), f"Expected EventNotFound error but got '{data['detail']}'"


def test_get_event_details(admin_client: TestClient):
    # 1. create event with google form
    create_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. get event details
    details_response = admin_client.get(f"/events/{event_id}/details")
    assert_2xx(details_response)
    body = details_response.json()
    assert "event" in body, f"Expected 'event' key in response, got keys: {list(body.keys())}"
    assert "actions" in body, f"Expected 'actions' key in response, got keys: {list(body.keys())}"
    assert body["event"]["id"] == event_id, f"Expected event id {event_id} but got {body['event']['id']}"
    assert body["event"]["name"] == "my event", f"Expected event name 'my event' but got '{body['event']['name']}'"
    assert len(body["actions"]) == 2, f"Expected 2 actions but got {len(body['actions'])}"


def test_update_event(admin_client: TestClient):
    # 1. create event with department 1, 1-day event
    create_response = admin_client.post("/events", json=make_create_event_payload(overrides={"department_id": 1}))
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. update status to open to trigger points allocation
    status_response = admin_client.put(f"/events/{event_id}/status", json={"status": "open"})
    assert_2xx(status_response)

    # 3. assert department 1 has 10 points (1 day × 10 points)
    points_response = admin_client.get("/points/departments/1")
    assert_2xx(points_response)
    assert points_response.json()["department"]["total_points"] == 10, (
        f"Expected dept 1 to have 10 points, got {points_response.json()['department']['total_points']}"
    )

    # 4. get event details to get current actions for the update payload
    details_response = admin_client.get(f"/events/{event_id}/details")
    assert_2xx(details_response)
    details = details_response.json()
    actions = details["actions"]

    # 5. update event: change department to 2, extend to 3 days, set status open
    update_payload = {
        "event": make_event(
            name="my event", start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-03T00:00:00", status="open"
        ),
        "actions": [
            # departmetn action
            {"action_id": actions[0]["action_id"], "ar_action_name": actions[0]["ar_action_name"], "department_id": 2},
            # member action, passed as is (endpoint requires both in the update payload)
            actions[1],
        ],
    }
    update_response = admin_client.put(f"/events/{event_id}", json=update_payload)
    assert_2xx(update_response)
    updated = update_response.json()
    assert updated["start_datetime"] == "2026-03-01T00:00:00"
    assert updated["end_datetime"] == "2026-03-03T00:00:00"

    # 6. assert department 1 now has 0 points
    dept1_response = admin_client.get("/points/departments/1")
    assert_2xx(dept1_response)
    assert dept1_response.json()["department"]["total_points"] == 0, (
        f"Expected dept 1 to have 0 points after update, got {dept1_response.json()['department']['total_points']}"
    )

    # 7. assert department 2 has 30 points (3 days × 10 points)
    dept2_response = admin_client.get("/points/departments/2")
    assert_2xx(dept2_response)
    assert dept2_response.json()["department"]["total_points"] == 30, (
        f"Expected dept 2 to have 30 points, got {dept2_response.json()['department']['total_points']}"
    )


def test_unauthorized_update_event(clerk_client: TestClient, db_session):
        # 1. insert an event in the DB
    event = Events(
        name="test event",
        description="test description",
        start_datetime=datetime(2026, 3, 1, 0, 0, 0),
        end_datetime=datetime(2026, 3, 2, 0, 0),
        status="draft",
        location_type = "on-site",
        location = "the moon"
    )
    db_session.add(event)
    db_session.commit()

    # 2. attempt to update the event with a clerk client (non-admin)
    update_payload = { 
        "event": make_event(
            name="updated event", start_datetime="2026-03-01T00:00:00", end_datetime="2026-03-02T00:00:00", status="draft"
        ),
        "actions": []
    }
    update_response = clerk_client.put(f"/events/{event.id}", json=update_payload)
    assert_forbidden(update_response)

def test_delete_event(admin_client: TestClient):
    # 1. create event
    create_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. update status to open to trigger points allocation
    status_response = admin_client.put(f"/events/{event_id}/status", json={"status": "open"})
    assert_2xx(status_response)

    # 3. assert department 1 has 10 points
    points_response = admin_client.get("/points/departments/1")
    assert_2xx(points_response)
    assert points_response.json()["department"]["total_points"] == 10, (
        f"Expected dept 1 to have 10 points before delete, got {points_response.json()['department']['total_points']}"
    )

    # 4. set status back to draft so we can delete (only draft events can be deleted)
    status_response = admin_client.put(f"/events/{event_id}/status", json={"status": "draft"})
    assert_2xx(status_response)

    # 5. delete event
    delete_response = admin_client.delete(f"/events/{event_id}")
    assert_2xx(delete_response)
    assert delete_response.json()["detail"] == "Event deleted successfully"

    # 6. assert the event no longer exists
    get_response = admin_client.get(f"/events/{event_id}")
    assert_not_found(get_response)

    # 7. assert department 1 now has 0 points
    points_after_response = admin_client.get("/points/departments/1")
    assert_2xx(points_after_response)
    assert points_after_response.json()["department"]["total_points"] == 0, (
        f"Expected dept 1 to have 0 points after delete, got {points_after_response.json()['department']['total_points']}"
    )


def test_unauthorized_delete_event(clerk_client: TestClient, db_session):
    # 1. insert an event in the DB
    event = Events(
        name="test event",
        description="test description",
        start_datetime=datetime(2026, 3, 1, 0, 0, 0),
        end_datetime=datetime(2026, 3, 2, 0, 0),
        status="draft",
        location_type = "on-site",
        location = "the moon"
    )
    db_session.add(event)
    db_session.commit()

    # 2. attempt to delete the event with a clerk client (non-admin)
    delete_response = clerk_client.delete(f"/events/{event.id}")
    assert_forbidden(delete_response)


def test_get_submissions_by_event(admin_client: TestClient, db_session):

    # 1. create event with registration form
    create_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. get the form id for this event
    form_response = admin_client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    form_id = form_response.json()["id"]

    # 3. insert submissions directly (member 1 and member 2 are seeded in conftest)
    db_session.add(
        Submissions(form_id=form_id, member_id=1, submission_type="registration", is_accepted=0, is_invited=0)
    )
    db_session.add(
        Submissions(form_id=form_id, member_id=2, submission_type="registration", is_accepted=1, is_invited=0)
    )
    db_session.commit()

    # 4. get submissions for this event
    submissions_response = admin_client.get(f"/events/submissions/{event_id}")
    assert_2xx(submissions_response)
    submissions = submissions_response.json()
    assert len(submissions) == 2, f"Expected 2 submissions but got {len(submissions)}"

    # 5. assert first submission (member 1, not accepted)
    s1 = next(s for s in submissions if s["member"]["id"] == 1)
    assert s1["submission_type"] == "registration"
    assert s1["is_accepted"] is False
    assert s1["member"]["name"] == "Ahmed Ali"

    # 6. assert second submission (member 2, accepted)
    s2 = next(s for s in submissions if s["member"]["id"] == 2)
    assert s2["submission_type"] == "registration"
    assert s2["is_accepted"] is True
    assert s2["member"]["name"] == "Sara Khalid"


def test_get_submissions_empty(admin_client: TestClient):
    # create event, no submissions
    create_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    submissions_response = admin_client.get(f"/events/submissions/{event_id}")
    assert_2xx(submissions_response)
    assert submissions_response.json() == [], f"Expected empty list but got {submissions_response.json()}"
