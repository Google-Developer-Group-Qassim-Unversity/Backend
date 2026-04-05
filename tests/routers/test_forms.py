from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_conflict


def test_existing_form(admin_client: TestClient):
    # 1. create event with form
    event_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(event_response)

    # 2. create a form for the event
    form_response = admin_client.post("/forms", json={"form_type": "none", "event_id": event_response.json()["id"]})
    assert_conflict(form_response)
