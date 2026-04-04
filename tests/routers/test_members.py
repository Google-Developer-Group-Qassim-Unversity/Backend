from fastapi.testclient import TestClient
from tests.utils import assert_2xx, assert_forbidden, assert_not_found


def test_create_member(clerk_client: TestClient):
    response = clerk_client.post("/members/")
    assert_2xx(response)
    body = response.json()
    assert body["member"]["uni_id"] == "123456789"
    assert body["member"]["name"] == "Test Member"
    assert body["already_exists"] is False


def test_update_member_role_success(super_admin_client: TestClient):
    # 1. create member (self)
    create_response = super_admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. update member role to "admin"
    response = super_admin_client.post(
        "/members/roles", params={"member_id": member_id, "new_role": "admin"}
    )
    assert_2xx(response)
    body = response.json()
    assert body["role"] == "admin"
    assert body["id"] == member_id


def test_update_role_member_not_found(super_admin_client: TestClient):
    response = super_admin_client.post(
        "/members/roles", params={"member_id": 9999, "new_role": "admin"}
    )
    assert_not_found(response)


def test_update_member_role_member_unauthorized(clerk_client: TestClient):
    # 1. create member (self)
    create_response = clerk_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. attempt to update member role to "admin"
    response = clerk_client.post(
        "/members/roles", params={"member_id": member_id, "new_role": "admin"}
    )
    assert_forbidden(response)

def test_update_member_role_admin_unauthorized(admin_client: TestClient):
    # 1. create member (self)
    create_response = admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. update admin role to "super_admin"
    response = admin_client.post(
        "/members/roles", params={"member_id": member_id, "new_role": "super_admin"}
    )
    assert_forbidden(response)

def test_update_member_role_invalid_role(super_admin_client: TestClient):
    # 1. create member (self)
    create_response = super_admin_client.post("/members/")
    assert_2xx(create_response)
    member_id = create_response.json()["member"]["id"]

    # 2. attempt to update member role to invalid role "invalid_role"
    response = super_admin_client.post(
        "/members/roles", params={"member_id": member_id, "new_role": "invalid_role"}
    )
    assert response.status_code == 422, f"Expected 422 Unprocessable Content for invalid role, got {response.status_code}, response body: {response.json()}"