"""The event lifecycle shim for iOS builds before 1.2.0 (app/legacy_app.py).

The route tests replay what the shipped app (1.1.3, `App` main) sends: its
`X-App-Version` header, the old status values in its bodies, and the reads it
makes after each change. They check it gets the old values back, and that the
Website and Admin (no header) see only the new model.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.legacy_app import is_legacy_app, legacy_event_status, to_legacy
from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx

OLD_APP = {"X-App-Version": "1.1.3", "X-App-Platform": "ios", "X-App-Build": "1"}
NEW_APP = {"X-App-Version": "1.2.0", "X-App-Platform": "ios", "X-App-Build": "1"}

# Frozen clock 2026-06-20 09:00 UTC = 12:00 Riyadh; the events below are on 2026-06-29.
UPCOMING = {"start_datetime": "2026-06-29T16:00:00", "end_datetime": "2026-06-29T18:00:00"}


# ---------------------------------------------------------------- pure rules


@pytest.mark.parametrize(
    ("version", "legacy"),
    [
        (None, False),
        ("1.2.0", False),
        ("1.10.0", False),
        ("2.0", False),
        ("1.1.3", True),
        ("1.0.2", True),
        ("0", True),
        ("garbage", True),
    ],
)
def test_is_legacy_app(version, legacy):
    assert is_legacy_app(version) is legacy


def _legacy(status="published", form_type=None, registration_status=None, now=datetime(2026, 6, 29, 12, 0)):
    return legacy_event_status(
        status=status,
        start_datetime=datetime(2026, 6, 29, 16, 0),
        end_datetime=datetime(2026, 6, 29, 18, 0),
        form_type=form_type,
        registration_status=registration_status,
        local_now=now,
    )


def test_legacy_event_status_matrix():
    live = datetime(2026, 6, 29, 17, 0)
    ended = datetime(2026, 6, 29, 18, 1)
    assert _legacy("draft") == "draft"
    assert _legacy("cancelled") == "closed"
    assert _legacy(now=ended) == "closed"
    assert _legacy(form_type="registration", registration_status="open", now=ended) == "closed"
    # With registration: open while it is open, else active (the old "registration closed").
    assert _legacy(form_type="registration", registration_status="open") == "open"
    assert _legacy(form_type="google", registration_status="open", now=live) == "open"
    assert _legacy(form_type="registration", registration_status="not_open") == "active"
    assert _legacy(form_type="google", registration_status="closed") == "active"
    # Without registration: open before the start, active while it runs.
    assert _legacy(form_type="none") == "open"
    assert _legacy(form_type=None) == "open"
    assert _legacy(form_type="none", now=live) == "active"


def test_to_legacy_reads_the_form_next_to_the_event_and_filters_the_open_view():
    now = datetime(2026, 6, 29, 12, 0)
    details = {
        "event": {"id": 1, "status": "published", "location_type": "on-site", **UPCOMING},
        "form": {"form_type": "registration", "registration_status": "closed"},
    }
    assert to_legacy(details, "/events/1/details", now)["event"]["status"] == "active"

    rows = [
        {"id": 1, "status": "published", "location_type": "online", "form_type": "registration",
         "registration_status": "open", **UPCOMING},
        {"id": 2, "status": "published", "location_type": "online", "form_type": "registration",
         "registration_status": "not_open", **UPCOMING},
        {"id": 3, "status": "published", "location_type": "on-site", "form_type": "none", **UPCOMING},
    ]  # fmt: skip
    assert [r["id"] for r in to_legacy(rows, "/events/open", now)] == [1, 3]
    # Not an event: left alone.
    assert to_legacy({"status": "published"}, "/x", now) == {"status": "published"}


# ---------------------------------------------------------------- the old app against the API


def _create(client: TestClient, status: str, form_type: str = "registration", **event) -> dict:
    res = client.post(
        "/events/",
        json=make_create_event_payload(event=make_event(status=status, **UPCOMING, **event), form_type=form_type),
        headers=OLD_APP,
    )
    assert_2xx(res)
    return res.json()


def _status_seen(client: TestClient, event_id: int, headers=OLD_APP) -> str:
    return client.get(f"/events/{event_id}/details", headers=headers).json()["event"]["status"]


def _open_ids(client: TestClient, headers=OLD_APP) -> list[int]:
    res = client.get("/events/open", headers=headers)
    assert_2xx(res)
    return [row["id"] for row in res.json()]


def test_old_app_admin_portal_status_flow(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 20, 9, 0))
    # «نشر»: the old app creates a registration event as 'open'.
    created = _create(admin_client, "open")
    event_id = created["id"]
    assert created["status"] == "open"

    # The new model underneath: published with registration open.
    new_view = admin_client.get(f"/events/{event_id}/details").json()
    assert new_view["event"]["status"] == "published"
    assert new_view["form"]["registration_status"] == "open"

    # The member side of the old app sees it as registrable.
    assert _status_seen(admin_client, event_id) == "open"
    assert admin_client.get(f"/events/{event_id}", headers=OLD_APP).json()["status"] == "open"
    assert event_id in _open_ids(admin_client)
    listed = admin_client.get("/events/", params={"semester": "all"}, headers=OLD_APP).json()
    assert {e["id"]: e["status"] for e in listed}[event_id] == "open"
    page = admin_client.get("/events/paginated", params={"page_size": 200}, headers=OLD_APP).json()
    assert {e["id"]: e["status"] for e in page["items"]}[event_id] == "open"

    # Close registration: setStatus('active').
    assert_2xx(admin_client.put(f"/events/{event_id}/status", json={"status": "active"}, headers=OLD_APP))
    assert admin_client.get(f"/events/{event_id}/form").json()["registration_status"] == "closed"
    assert _status_seen(admin_client, event_id) == "active"
    assert event_id not in _open_ids(admin_client)
    # The Website and Admin still see the event in the open view, with registration closed.
    assert event_id in _open_ids(admin_client, headers={})

    # Edit: the old app sends back the status it read ('active'); the status stays.
    update = {
        "event": make_event(status="active", name="renamed", **UPCOMING),
        "actions": [
            {"action_id": new_view["actions"][0]["action_id"], "ar_action_name": "x",
             "department_id": new_view["actions"][0].get("department_id")},
            {"action_id": new_view["actions"][1]["action_id"], "ar_action_name": "x"},
        ],
    }  # fmt: skip
    assert_2xx(admin_client.put(f"/events/{event_id}", json=update, headers=OLD_APP))
    assert _status_seen(admin_client, event_id, headers={}) == "published"

    # Reopen registration: setStatus('open').
    reopened = admin_client.put(f"/events/{event_id}/status", json={"status": "open"}, headers=OLD_APP)
    assert_2xx(reopened)
    assert admin_client.get(f"/events/{event_id}/form").json()["registration_status"] == "open"
    assert _status_seen(admin_client, event_id) == "open"

    # Close the event: setStatus('closed') closes registration and never cancels.
    assert_2xx(admin_client.put(f"/events/{event_id}/status", json={"status": "closed"}, headers=OLD_APP))
    assert _status_seen(admin_client, event_id, headers={}) == "published"
    assert admin_client.get(f"/events/{event_id}/form").json()["registration_status"] == "closed"

    # Once it ended, the old app sees it closed.
    frozen.set(datetime(2026, 6, 29, 15, 1))  # 18:01 Riyadh
    assert _status_seen(admin_client, event_id) == "closed"


def test_old_app_draft_then_publish(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 20, 9, 0))
    event_id = _create(admin_client, "draft")["id"]
    assert _status_seen(admin_client, event_id) == "draft"
    assert event_id not in _open_ids(admin_client)

    published = admin_client.put(f"/events/{event_id}/status", json={"status": "open"}, headers=OLD_APP)
    assert_2xx(published)
    assert _status_seen(admin_client, event_id, headers={}) == "published"
    assert _status_seen(admin_client, event_id) == "open"
    assert event_id in _open_ids(admin_client)


def test_old_app_event_without_registration(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 20, 9, 0))
    event_id = _create(admin_client, "open", form_type="none")["id"]
    assert _status_seen(admin_client, event_id) == "open"
    assert event_id in _open_ids(admin_client)
    # While it runs, an online event without a form stays in the old open view (the join link).
    frozen.set(datetime(2026, 6, 29, 14, 0))  # 17:00 Riyadh
    assert _status_seen(admin_client, event_id) == "active"
    assert event_id in _open_ids(admin_client)


def test_old_app_sees_a_cancelled_event_as_closed_and_cannot_uncancel_it_by_editing(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 20, 9, 0))
    event_id = _create(admin_client, "open")["id"]
    assert_2xx(admin_client.put(f"/events/{event_id}/status", json={"status": "cancelled"}))
    assert _status_seen(admin_client, event_id) == "closed"
    assert event_id not in _open_ids(admin_client)

    details = admin_client.get(f"/events/{event_id}/details").json()
    update = {
        "event": make_event(status="closed", **UPCOMING),
        "actions": [
            {"action_id": details["actions"][0]["action_id"], "ar_action_name": "x",
             "department_id": details["actions"][0].get("department_id")},
            {"action_id": details["actions"][1]["action_id"], "ar_action_name": "x"},
        ],
    }  # fmt: skip
    assert_2xx(admin_client.put(f"/events/{event_id}", json=update, headers=OLD_APP))
    assert _status_seen(admin_client, event_id, headers={}) == "cancelled"


def test_new_clients_get_the_new_model(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 20, 9, 0))
    event_id = _create(admin_client, "open")["id"]
    for headers in ({}, NEW_APP):
        assert _status_seen(admin_client, event_id, headers=headers) == "published"
        assert admin_client.get(f"/events/{event_id}", headers=headers).json()["status"] == "published"


def test_old_app_binary_and_non_event_answers_pass_through(admin_client: TestClient):
    health = admin_client.get("/health", headers=OLD_APP)
    assert_2xx(health)
    assert health.json() == admin_client.get("/health").json()
    assert health.headers["content-length"] == str(len(health.content))
