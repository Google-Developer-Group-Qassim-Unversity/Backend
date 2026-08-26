from fastapi.testclient import TestClient

from tests.factories import make_create_event_payload
from tests.utils import assert_2xx, assert_bad_request, assert_forbidden, assert_not_found


def make_action(**overrides):
    payload = {
        "action_name": "ran a workshop",
        "ar_action_name": "أقام ورشة عمل",
        "action_type": "department",
        "points": 15,
    }
    payload.update(overrides)
    return payload


def create(client: TestClient, **overrides):
    response = client.post("/actions", json=make_action(**overrides))
    assert_2xx(response)
    return response.json()


# ---------- reads ----------


def test_categorised_actions_split_by_type(client: TestClient, seed_refs):
    response = client.get("/actions")

    assert_2xx(response)
    body = response.json()
    assert set(body) == {"composite_actions", "department_actions", "member_actions", "custom_actions"}
    dept_ids = {a["id"] for a in body["department_actions"]}
    member_ids = {a["id"] for a in body["member_actions"]}
    assert seed_refs.dept_action.id in dept_ids
    assert seed_refs.member_action.id in member_ids
    assert not dept_ids & member_ids


def test_all_actions_include_a_usage_count(client: TestClient, seed_refs):
    response = client.get("/actions/all")

    assert_2xx(response)
    by_id = {a["id"]: a for a in response.json()}
    assert seed_refs.dept_action.id in by_id
    assert by_id[seed_refs.dept_action.id]["usage_count"] >= 0


def test_reads_are_public(client: TestClient, seed_refs):
    assert_2xx(client.get("/actions"))
    assert_2xx(client.get("/actions/all"))


# ---------- writes ----------


def test_create_returns_the_stored_action(admin_points_client: TestClient):
    created = create(admin_points_client)

    assert created["action_name"] == "ran a workshop"
    assert created["ar_action_name"] == "أقام ورشة عمل"
    assert created["points"] == 15
    assert created["action_type"] == "department"

    listed = {a["id"] for a in admin_points_client.get("/actions/all").json()}
    assert created["id"] in listed


def test_update_changes_only_what_was_sent(admin_points_client: TestClient):
    created = create(admin_points_client)

    response = admin_points_client.put(f"/actions/{created['id']}", json={"points": 42})

    assert_2xx(response)
    updated = response.json()
    assert updated["points"] == 42
    assert updated["action_name"] == created["action_name"], "unset fields must not be cleared"


def test_update_can_hide_an_action(admin_points_client: TestClient):
    created = create(admin_points_client)

    response = admin_points_client.put(f"/actions/{created['id']}", json={"is_hidden": True})

    assert_2xx(response)
    assert response.json()["is_hidden"] is True


def test_update_unknown_action_is_404(admin_points_client: TestClient):
    assert_not_found(admin_points_client.put("/actions/999999", json={"points": 1}))


def test_reorder_persists_the_new_order(admin_points_client: TestClient):
    first = create(admin_points_client, action_name="first")
    second = create(admin_points_client, action_name="second")

    response = admin_points_client.put(
        "/actions/reorder",
        json={"action_orders": [{"id": first["id"], "order": 5}, {"id": second["id"], "order": 2}]},
    )

    assert_2xx(response)
    assert response.json()["message"] == "Actions reordered successfully"
    by_id = {a["id"]: a for a in admin_points_client.get("/actions/all").json()}
    assert by_id[first["id"]]["order"] == 5
    assert by_id[second["id"]]["order"] == 2


def test_reorder_is_not_swallowed_by_the_id_route(admin_points_client: TestClient):
    """`/actions/reorder` sits after `/actions/{action_id:int}` in the file; the
    int converter is what stops it being captured as an id."""
    response = admin_points_client.put("/actions/reorder", json={"action_orders": []})
    assert_2xx(response)


def test_delete_unused_action(admin_points_client: TestClient):
    created = create(admin_points_client)

    response = admin_points_client.delete(f"/actions/{created['id']}")

    assert_2xx(response)
    remaining = {a["id"] for a in admin_points_client.get("/actions/all").json()}
    assert created["id"] not in remaining


def test_delete_unknown_action_is_404(admin_points_client: TestClient):
    assert_not_found(admin_points_client.delete("/actions/999999"))


def test_delete_with_unknown_replacement_is_404(admin_points_client: TestClient):
    created = create(admin_points_client)

    response = admin_points_client.delete(f"/actions/{created['id']}?replacement_id=999999")

    assert_not_found(response)
    assert "Replacement" in response.json()["detail"]


def test_used_action_cannot_be_deleted_without_a_replacement(
    admin_points_client: TestClient, admin_client: TestClient, seed_refs
):
    """Creating an event writes log rows against its actions, which is what
    "used" means here. Going through the real endpoint keeps the test from
    depending on whatever else happens to be in the database."""
    assert_2xx(admin_client.post("/events", json=make_create_event_payload(seed_refs)))

    response = admin_points_client.delete(f"/actions/{seed_refs.dept_action.id}")

    assert_bad_request(response)
    assert "replacement_id" in response.json()["detail"]


def test_used_action_can_be_deleted_with_a_replacement(
    admin_points_client: TestClient, admin_client: TestClient, seed_refs
):
    """The replacement takes over the existing log rows rather than losing them."""
    assert_2xx(admin_client.post("/events", json=make_create_event_payload(seed_refs)))
    replacement = create(admin_points_client, action_name="stand-in")

    before = {a["id"]: a["usage_count"] for a in admin_points_client.get("/actions/all").json()}
    moved = before[seed_refs.dept_action.id]
    assert moved > 0

    response = admin_points_client.delete(
        f"/actions/{seed_refs.dept_action.id}?replacement_id={replacement['id']}"
    )

    assert_2xx(response)
    after = {a["id"]: a["usage_count"] for a in admin_points_client.get("/actions/all").json()}
    assert seed_refs.dept_action.id not in after
    assert after[replacement["id"]] == moved


# ---------- auth ----------


def test_writes_require_points_admin(admin_client: TestClient):
    """`admin` is not enough: /points is gated to admin_points and super_admin."""
    assert_forbidden(admin_client.post("/actions", json=make_action()))
    assert_forbidden(admin_client.put("/actions/1", json={"points": 1}))
    assert_forbidden(admin_client.delete("/actions/1"))


def test_writes_reject_plain_members(clerk_client: TestClient):
    assert_forbidden(clerk_client.post("/actions", json=make_action()))
