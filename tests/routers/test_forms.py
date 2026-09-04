from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.DB.forms import create_form, get_form_by_event_id, get_form_by_google_form_id
from app.DB.schema import Events, Forms, FormType
from app.exceptions import DataIntegrityError, FormNotFound
from app.routers.models import Form_model
from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_conflict, assert_forbidden, assert_not_found, assert_unprocessable


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


# =============================================================================
# Attach / unattach / schema
# =============================================================================
# These endpoints are the replacement for the old per-admin OAuth flow: a
# single club-owned Google account (never touched in tests) copies the
# template and invites an admin as a Drive editor. Google's client is never
# called for real here - `build` and `get_google_credentials` are monkeypatched
# to fakes that record what they were asked to do, following the same
# monkeypatch-the-module-function style test_submissions_sync.py uses rather
# than mocking google.oauth2.credentials.Credentials directly.


class _Execute:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeDrive:
    """Records every Drive call the attach/unattach endpoints make."""

    def __init__(self, copy_id="new-google-form-id", permissions=None):
        self.copy_id = copy_id
        self.permissions_list = permissions if permissions is not None else []
        self.copy_calls = 0
        self.created_permissions = []
        self.deleted_permission_ids = []

    def files(self):
        outer = self

        class _Files:
            def copy(self, fileId, fields=None):
                outer.copy_calls += 1
                return _Execute({"id": outer.copy_id})

        return _Files()

    def permissions(self):
        outer = self

        class _Permissions:
            def create(self, fileId, body, sendNotificationEmail=None):
                outer.created_permissions.append(body)
                return _Execute({})

            def list(self, fileId, fields=None):
                return _Execute({"permissions": outer.permissions_list})

            def delete(self, fileId, permissionId):
                outer.deleted_permission_ids.append(permissionId)
                return _Execute({})

        return _Permissions()


class FakeForms:
    """Records every Forms API call the attach/unattach/schema endpoints make."""

    def __init__(self, watch_id="watch-123", responder_uri="https://docs.google.com/forms/d/e/fake/viewform"):
        self.watch_id = watch_id
        self.responder_uri = responder_uri
        self.deleted_watches = []

    def forms(self):
        outer = self

        class _Forms:
            def watches(self):
                class _Watches:
                    def create(self, formId, body):
                        return _Execute({"id": outer.watch_id})

                    def delete(self, formId, watchId):
                        outer.deleted_watches.append((formId, watchId))
                        return _Execute({})

                return _Watches()

            def get(self, formId):
                return _Execute({"responderUri": outer.responder_uri})

        return _Forms()


def _patch_google_client(monkeypatch, fake_drive: FakeDrive, fake_forms: FakeForms):
    from app.routers import forms as forms_router

    def fake_build(service_name, version, credentials=None):
        return fake_drive if service_name == "drive" else fake_forms

    monkeypatch.setattr(forms_router, "build", fake_build)
    monkeypatch.setattr(forms_router, "get_google_credentials", lambda: object())
    return fake_drive, fake_forms


def test_attach_form_copies_template_and_invites_admin(admin_client: TestClient, monkeypatch):
    fake_drive, fake_forms = _patch_google_client(monkeypatch, FakeDrive(), FakeForms())

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    response = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "someone@gmail.com"})
    assert_2xx(response)
    data = response.json()
    assert data["form_type"] == "google"
    assert data["google_form_id"] == "new-google-form-id"
    assert data["google_watch_id"] == "watch-123"
    assert data["google_responders_url"] == "https://docs.google.com/forms/d/e/fake/viewform"
    assert data["admin_google_email"] == "someone@gmail.com"
    assert fake_drive.copy_calls == 1
    assert fake_drive.created_permissions == [{"role": "writer", "type": "user", "emailAddress": "someone@gmail.com"}]


def test_attach_form_is_idempotent_for_a_different_email(admin_client: TestClient, monkeypatch):
    """Re-running attach with a different email re-invites without re-copying the form."""
    fake_drive, fake_forms = _patch_google_client(monkeypatch, FakeDrive(), FakeForms())

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    first = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "first@gmail.com"})
    assert_2xx(first)

    second = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "second@gmail.com"})
    assert_2xx(second)
    data = second.json()
    assert data["google_form_id"] == "new-google-form-id"
    assert data["admin_google_email"] == "second@gmail.com"
    assert fake_drive.copy_calls == 1, "attaching a second time must not re-copy the template"
    assert [perm["emailAddress"] for perm in fake_drive.created_permissions] == ["first@gmail.com", "second@gmail.com"]


def test_attach_form_remembers_every_admin_granted_access(admin_client: TestClient, monkeypatch):
    """admin_google_email only ever remembers the latest grant - granted_emails must
    keep every admin who was actually given Drive access, or an earlier admin's
    still-valid access looks revoked to them the next time they load the page."""
    fake_drive, fake_forms = _patch_google_client(monkeypatch, FakeDrive(), FakeForms())

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    first = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "first@gmail.com"})
    assert_2xx(first)
    assert first.json()["granted_emails"] == ["first@gmail.com"]

    second = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "second@gmail.com"})
    assert_2xx(second)
    assert set(second.json()["granted_emails"]) == {"first@gmail.com", "second@gmail.com"}

    # Re-requesting for an email that already has access must not duplicate it.
    third = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "first@gmail.com"})
    assert_2xx(third)
    assert set(third.json()["granted_emails"]) == {"first@gmail.com", "second@gmail.com"}


def test_unattach_form_revokes_every_granted_admin(admin_client: TestClient, monkeypatch):
    fake_drive, fake_forms = _patch_google_client(
        monkeypatch,
        FakeDrive(
            permissions=[
                {"id": "perm-1", "emailAddress": "first@gmail.com"},
                {"id": "perm-2", "emailAddress": "second@gmail.com"},
            ]
        ),
        FakeForms(),
    )

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "first@gmail.com"})
    admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "second@gmail.com"})

    unattach_response = admin_client.post(f"/forms/{event_id}/unattach")
    assert_2xx(unattach_response)
    assert set(fake_drive.deleted_permission_ids) == {"perm-1", "perm-2"}
    assert unattach_response.json()["granted_emails"] == []


def test_attach_form_rejects_disallowed_email_domain(admin_client: TestClient, monkeypatch):
    _patch_google_client(monkeypatch, FakeDrive(), FakeForms())

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    response = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "someone@work.example"})
    assert_unprocessable(response)


def test_unauthorized_attach_form(clerk_client: TestClient):
    response = clerk_client.post("/forms/1/attach", json={"admin_google_email": "someone@gmail.com"})
    assert_forbidden(response)


def test_unattach_form_revokes_access_and_resets_row(admin_client: TestClient, monkeypatch):
    fake_drive, fake_forms = _patch_google_client(
        monkeypatch, FakeDrive(permissions=[{"id": "perm-1", "emailAddress": "someone@gmail.com"}]), FakeForms()
    )

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    attach_response = admin_client.post(f"/forms/{event_id}/attach", json={"admin_google_email": "someone@gmail.com"})
    assert_2xx(attach_response)

    unattach_response = admin_client.post(f"/forms/{event_id}/unattach")
    assert_2xx(unattach_response)
    data = unattach_response.json()
    assert data["form_type"] == "registration"
    assert data["google_form_id"] is None
    assert data["google_watch_id"] is None
    assert data["google_responders_url"] is None
    assert data["admin_google_email"] is None
    assert fake_forms.deleted_watches == [("new-google-form-id", "watch-123")]
    assert fake_drive.deleted_permission_ids == ["perm-1"]


def test_unattach_form_without_a_form_attached_is_a_noop(admin_client: TestClient, monkeypatch):
    fake_drive, fake_forms = _patch_google_client(monkeypatch, FakeDrive(), FakeForms())

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    response = admin_client.post(f"/forms/{event_id}/unattach")
    assert_2xx(response)
    assert fake_forms.deleted_watches == []
    assert fake_drive.deleted_permission_ids == []


def test_unauthorized_unattach_form(clerk_client: TestClient):
    response = clerk_client.post("/forms/1/unattach")
    assert_forbidden(response)


def test_get_form_schema(admin_client: TestClient, monkeypatch):
    from app.routers import forms as forms_router

    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="google"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    form_response = admin_client.get(f"/events/{event_id}/form")
    form_id = form_response.json()["id"]
    update_response = admin_client.put(
        f"/forms/{form_id}", json={"event_id": event_id, "form_type": "google", "google_form_id": "test_google_id"}
    )
    assert_2xx(update_response)

    monkeypatch.setattr(forms_router, "fetch_schema", lambda google_form_id: {"items": [], "form_id": google_form_id})

    response = admin_client.get(f"/forms/{form_id}/schema")
    assert_2xx(response)
    assert response.json() == {"items": [], "form_id": "test_google_id"}


def test_get_form_schema_not_yet_attached(admin_client: TestClient):
    event_response = admin_client.post("/events", json=make_create_event_payload(form_type="registration"))
    assert_2xx(event_response)
    event_id = event_response.json()["id"]

    form_response = admin_client.get(f"/events/{event_id}/form")
    form_id = form_response.json()["id"]

    response = admin_client.get(f"/forms/{form_id}/schema")
    assert_conflict(response)


def test_get_form_schema_nonexistent_form(admin_client: TestClient):
    response = admin_client.get("/forms/9999/schema")
    assert_not_found(response)


def test_unauthorized_get_form_schema(clerk_client: TestClient):
    response = clerk_client.get("/forms/1/schema")
    assert_forbidden(response)
