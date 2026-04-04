from fastapi.testclient import TestClient
from tests.utils import assert_2xx, assert_forbidden, assert_not_found, assert_unprocessable


def test_create_member(clerk_client: TestClient):
    response = clerk_client.post("/members/")
    assert_2xx(response)
    body = response.json()
    assert body["member"]["uni_id"] == "123456789"
    assert body["member"]["name"] == "Test Member"
    assert body["already_exists"] is False


# === Member Role Tests ===


def test_update_member_role_success(super_admin_client: TestClient):
    # 1. create member (self)
    create_response = super_admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. update member role to "admin"
    response = super_admin_client.post("/members/roles", params={"member_id": member_id, "new_role": "admin"})
    assert_2xx(response)
    body = response.json()
    assert body["role"] == "admin"
    assert body["id"] == member_id


def test_update_role_member_not_found(super_admin_client: TestClient):
    response = super_admin_client.post("/members/roles", params={"member_id": 9999, "new_role": "admin"})
    assert_not_found(response)


def test_update_member_role_member_unauthorized(clerk_client: TestClient):
    # 1. create member (self)
    create_response = clerk_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. attempt to update member role to "admin"
    response = clerk_client.post("/members/roles", params={"member_id": member_id, "new_role": "admin"})
    assert_forbidden(response)


def test_update_member_role_admin_unauthorized(admin_client: TestClient):
    # 1. create member (self)
    create_response = admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. update admin role to "super_admin"
    response = admin_client.post("/members/roles", params={"member_id": member_id, "new_role": "super_admin"})
    assert_forbidden(response)


def test_update_member_role_invalid_role(super_admin_client: TestClient):
    # 1. create member (self)
    create_response = super_admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. attempt to update member role to invalid role "invalid_role"
    response = super_admin_client.post("/members/roles", params={"member_id": member_id, "new_role": "invalid_role"})
    assert_unprocessable(response)


# === Member Update Tests ===


def test_update_member_success(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"name": "Updated Name"})
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "Updated Name"
    assert body["uni_id"] == "123456789"


def test_update_member_not_found(clerk_client: TestClient):
    response = clerk_client.patch("/members/me", json={"name": "Updated Name"})
    assert_not_found(response)


def test_update_member_partial_update(clerk_client: TestClient):
    create_resp = clerk_client.post("/members/")
    original = create_resp.json()["member"]
    response = clerk_client.patch("/members/me", json={"phone_number": "0550000000"})
    assert_2xx(response)
    body = response.json()
    assert body["phone_number"] == "0550000000"
    assert body["name"] == original["name"]
    assert body["email"] == original["email"]


def test_update_member_multiple_fields(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"name": "New Name", "email": "new@example.com", "uni_level": 6})
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "New Name"
    assert body["email"] == "new@example.com"
    assert body["uni_level"] == 6


def test_update_member_invalid_gender(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"gender": "Other"})
    assert_unprocessable(response)


def test_update_member_invalid_email(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"email": "not-an-email"})
    assert_unprocessable(response)
