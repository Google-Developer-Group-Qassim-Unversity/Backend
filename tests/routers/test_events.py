from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx, assert_forbidden, assert_not_found


def test_authorized_create_event(admin_client: TestClient):
    response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "my event", f"Expected event name 'my event' but got '{body['name']}'"


def test_unauthorized_create_event(client: TestClient):
    response = client.post("/events", json=make_create_event_payload())
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
