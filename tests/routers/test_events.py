import pytest
from fastapi.testclient import TestClient
from app.routers.models import Events_model, LocationTypeEnum
from datetime import datetime
from tests.utils import assert_2xx, assert_forbidden

# ======= Factory functions to create test data =======

def make_event(**overrides):
    defaults = {
        "name": "my event",
        "description": "my description",
        "location_type": LocationTypeEnum.online,
        "location": "space",
        "start_datetime": datetime.fromisoformat("2026-03-01T00:00:00"),
        "end_datetime": datetime.fromisoformat("2026-03-01T00:00:00"),
        "status": "draft",
        "image_url": None,
        "is_official": 0,
        "created_at": None,
    }
    defaults.update(overrides)
    return Events_model(**defaults).model_dump(mode="json")


def make_create_event_payload(**overrides):
    event = overrides.pop("event", None) or make_event()
    defaults = {
        "event": event,
        "form_type": "none",
        "department_action_id": 1,
        "member_action_id": 2,
        "department_id": 1,
    }
    defaults.update(overrides)
    return defaults

# ======= End of factory functions =======


def test_authorized_create_event(admin_client: TestClient):
    response = admin_client.post('/events', json=make_create_event_payload())
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "my event", f"Expected event name 'my event' but got '{body['name']}'"
    


def test_unauthorized_create_event(client: TestClient):
    response = client.post('/events', json=make_create_event_payload())
    assert_forbidden(response)


def test_update_event_status(admin_client: TestClient):
    # 1. create event
    create_response = admin_client.post('/events', json=make_create_event_payload())
    assert_2xx(create_response)
    event_id = create_response.json()["id"]

    # 2. update event status to "active"
    update_response = admin_client.put(f'/events/{event_id}/status', json={"status": "open"})
    assert_2xx(update_response)
    updated_body = update_response.json()
    assert updated_body["status"] == "open", f"Expected event status 'active' but got '{updated_body['status']}'"

def test_points_admin_create_event(admin_client: TestClient):
    # 1. create event using default action
    event_response = admin_client.post('/events', json=make_create_event_payload())
    assert_2xx(event_response)

    # 2. update event status to "open" to trigger points allocation
    update_response = admin_client.put(f'/events/{event_response.json()["id"]}/status', json={"status": "open"})
    assert_2xx(update_response)

    # 3. get events to confirm 1 event was created
    events_response = admin_client.get('/events')
    assert_2xx(events_response)
    events_body = events_response.json()
    assert len(events_body) == 1, f"Expected 1 event but got {len(events_body)}. Response body: {events_body}"

    # 4. assert that points were given to the department
    points_response = admin_client.get('/points/departments/1')
    assert_2xx(points_response)
    points_body = points_response.json()
    assert points_body["department"]["total_points"] == 10, f"Expected 10 points for department, got {points_body['department']['total_points']}, \nFull response: {points_body}"