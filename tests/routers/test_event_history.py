"""Who did what to an event from /events: GET /events/{id}/history."""

from fastapi.testclient import TestClient

from tests.factories import make_create_event_payload, make_event
from tests.utils import assert_2xx


def history(client: TestClient, event_id: int):
    response = client.get(f"/events/{event_id}/history")
    assert_2xx(response)
    return response.json()


def test_every_admin_change_says_who_and_what(admin_client: TestClient, seed_refs):
    me = admin_client.get("/members/me").json()
    me_ref = {"member_id": me["id"], "name": me["name"]}
    payload = make_create_event_payload(
        seed_refs=seed_refs, event=make_event(name="Workshop", location_type="online", location="Online")
    )
    event_id = admin_client.post("/events", json=payload).json()["id"]

    details = admin_client.get(f"/events/{event_id}/details").json()
    edited = admin_client.put(
        f"/events/{event_id}",
        json={
            "event": make_event(name="Workshop: part 1", location_type="online", location="Online"),
            "actions": [{**details["actions"][0], "department_id": seed_refs.dept_design.id}, details["actions"][1]],
        },
    )
    assert_2xx(edited)
    admin_client.put(f"/events/{event_id}/status", json={"status": "open"})
    admin_client.put(f"/events/{event_id}/meeting-url", json={"meeting_url": "https://meet.google.com/abc"})

    body = history(admin_client, event_id)
    assert body["pipeline_request_id"] is None
    items = body["items"]
    assert [i["action"] for i in items] == ["created", "edited", "status_changed", "meeting_url_changed"]
    assert all(i["actor"] == me_ref and i["at"] for i in items)
    assert items[0]["details"]["department_id"] == seed_refs.dept_business.id
    assert items[1]["details"] == {
        "name": ["Workshop", "Workshop: part 1"],
        "department_id": [seed_refs.dept_business.id, seed_refs.dept_design.id],
    }
    assert items[2]["details"] == {"status": ["draft", "open"]}
    assert items[3]["details"] == {"meeting_url": [None, "https://meet.google.com/abc"]}


def test_saving_without_changes_records_nothing(admin_client: TestClient, seed_refs):
    event_id = admin_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()["id"]
    details = admin_client.get(f"/events/{event_id}/details").json()
    admin_client.put(f"/events/{event_id}", json={"event": make_event(), "actions": details["actions"]})
    admin_client.put(f"/events/{event_id}/status", json={"status": "draft"})
    assert [i["action"] for i in history(admin_client, event_id)["items"]] == ["created"]


def test_the_history_outlives_a_deleted_event(admin_client: TestClient, seed_refs):
    event_id = admin_client.post("/events", json=make_create_event_payload(seed_refs=seed_refs)).json()["id"]
    assert_2xx(admin_client.delete(f"/events/{event_id}"))
    items = history(admin_client, event_id)["items"]
    assert [i["action"] for i in items] == ["created", "deleted"]
