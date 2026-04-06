from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.DB.forms import create_form, get_form_by_event_id, get_form_by_google_form_id
from app.DB.schema import Events, Forms, FormType
from app.exceptions import DataIntegrityError, FormNotFound
from app.routers.models import Form_model
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_forbidden, assert_not_found


def make_form_payload(event_id: int, **overrides):
    defaults = {"event_id": event_id, "form_type": "registration"}
    defaults.update(overrides)
    return defaults


def test_get_all_forms(admin_client: TestClient):
    event_1 = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    event_2 = admin_client.post("/events", json=make_create_event_payload(overrides={"name": "another event"}))
    assert_2xx(event_1)
    assert_2xx(event_2)

    forms_response = admin_client.get("/forms/")
    assert_2xx(forms_response)
    forms = forms_response.json()
    assert len(forms) == 2, f"Expected 2 forms but got {len(forms)}"


def test_get_all_forms_empty(admin_client: TestClient):
    forms_response = admin_client.get("/forms/")
    assert_2xx(forms_response)
    assert forms_response.json() == [], f"Expected empty list but got {forms_response.json()}"


def test_get_form_by_id(admin_client: TestClient):
    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    form_response = admin_client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    form_id = form_response.json()["id"]

    get_response = admin_client.get(f"/forms/{form_id}")
    assert_2xx(get_response)
    data = get_response.json()
    assert data["id"] == form_id, f"Expected form id {form_id} but got {data['id']}"
    assert data["event_id"] == event_id, f"Expected event id {event_id} but got {data['event_id']}"


def test_get_nonexistent_form(admin_client: TestClient):
    get_response = admin_client.get("/forms/9999")
    assert_not_found(get_response)
    data = get_response.json()
    assert data["detail"].startswith("Form"), f"Expected FormNotFound error but got '{data['detail']}'"


def test_update_form(admin_client: TestClient):
    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    form_response = admin_client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    form_id = form_response.json()["id"]

    update_response = admin_client.put(
        f"/forms/{form_id}", json={"event_id": event_id, "form_type": "google", "google_form_id": "test_form_id"}
    )
    assert_2xx(update_response)
    data = update_response.json()
    assert data["form_type"] == "google", f"Expected form_type 'google' but got {data['form_type']}"
    assert data["google_form_id"] == "test_form_id", (
        f"Expected google_form_id 'test_form_id' but got {data.get('google_form_id')}"
    )


def test_unauthorized_update_form(clerk_client: TestClient):
    update_response = clerk_client.put("/forms/1", json={"event_id": 1, "form_type": "registration"})
    assert_forbidden(update_response)


def test_update_nonexistent_form(admin_client: TestClient):
    update_response = admin_client.put("/forms/9999", json={"event_id": 1, "form_type": "registration"})
    assert_not_found(update_response)
    data = update_response.json()
    assert data["detail"].startswith("Form"), f"Expected FormNotFound error but got '{data['detail']}'"


# =============================================================================
# Direct DB Query Tests
# =============================================================================
# NOTE: These tests directly query the database layer because the endpoint that
# uses this function (Google Forms webhook in submissions.py) cannot be easily
# tested without mocking Google's OAuth flow. We test the DB query directly to
# ensure it works correctly when called by the webhook handler.


def test_get_form_by_google_form_id_found(admin_client: TestClient, db_session):
    """Test fetching form by Google Form ID - happy path where form exists."""
    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    form_response = admin_client.get(f"/events/{event_id}/form")
    assert_2xx(form_response)
    form_id = form_response.json()["id"]

    update_response = admin_client.put(
        f"/forms/{form_id}", json={"event_id": event_id, "form_type": "google", "google_form_id": "test_google_id_123"}
    )
    assert_2xx(update_response)

    form = get_form_by_google_form_id(db_session, "test_google_id_123")
    assert form is not None
    assert form.id == form_id
    assert form.event_id == event_id


def test_get_form_by_google_form_id_not_found(db_session):
    """Test fetching form by Google Form ID - returns None when not found."""
    form = get_form_by_google_form_id(db_session, "nonexistent_google_id")
    assert form is None


# =============================================================================
# Data Integrity Violation Tests
# =============================================================================
# NOTE: These tests intentionally corrupt the database to test defensive code
# that should never execute under normal circumstances. They verify that when
# impossible states occur, we raise clear 500 errors rather than silently fail.


def test_create_form_integrity_violation(db_session):
    """Test create_form raises DataIntegrityError when form already exists for event."""
    event = Events(
        name="test event",
        description="test",
        start_datetime=datetime(2026, 3, 1),
        end_datetime=datetime(2026, 3, 2),
        status="draft",
        location_type="online",
        location="space",
    )
    db_session.add(event)
    db_session.flush()

    form1 = Forms(event_id=event.id, form_type=FormType.REGISTRATION)
    db_session.add(form1)
    db_session.flush()

    new_form = Form_model(event_id=event.id, form_type=FormType.GOOGLE)
    with pytest.raises(DataIntegrityError) as exc_info:
        create_form(db_session, new_form)

    assert f"Cannot create form for event {event.id}" in str(exc_info.value.detail)


def test_get_form_by_event_id_missing_form(db_session):
    """Test get_form_by_event_id raises FormNotFound for legacy events without forms."""
    event = Events(
        name="orphan event",
        description="test",
        start_datetime=datetime(2026, 3, 1),
        end_datetime=datetime(2026, 3, 2),
        status="draft",
        location_type="online",
        location="space",
    )
    db_session.add(event)
    db_session.flush()

    with pytest.raises(FormNotFound) as exc_info:
        get_form_by_event_id(db_session, event.id)

    assert f"Form for event with id '{event.id}'" in str(exc_info.value.detail)
