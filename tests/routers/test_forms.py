from fastapi.testclient import TestClient
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_existing
from app.routers.models import Form_model

# Note: Did not create factory for forms becauase as of now there are not a lot of tests
# if the tests grow in the future we can consider creating a factory for forms


def test_existing_form(admin_client: TestClient):
    # 1. create event with form
    event_response = admin_client.post("/events", json=make_create_event_payload())
    assert_2xx(event_response)

    # 2. create a form for the event
    form_response = admin_client.post(
        "/forms", json=Form_model(form_type="none", event_id=event_response.json()["id"]).model_dump(mode="json")
    )
    assert_existing(form_response)
