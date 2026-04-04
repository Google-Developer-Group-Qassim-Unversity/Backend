from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden


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

    # 4. assert that points were given to the department
    points_response = admin_client.get('/points/departments/1')
    assert_2xx(points_response)
    points_body = points_response.json()
    assert points_body["department"]["total_points"] == 10, f"Expected 10 points for department, got {points_body['department']['total_points']}, \nFull response: {points_body}"