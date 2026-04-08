from fastapi.testclient import TestClient
from tests.factories import make_member
from tests.utils import assert_2xx, assert_forbidden, assert_not_found, assert_unprocessable
from app.DB.schema import Members, MembersGender, Role, RoleType


def test_create_member(clerk_client: TestClient):
    response = clerk_client.post("/members/")
    assert_2xx(response)
    body = response.json()
    assert body["member"]["uni_id"] == "123456789"
    assert body["member"]["name"] == "Test Member"
    assert body["already_exists"] is False


def test_create_member_already_exists(clerk_client: TestClient, db_session):
    # 1. insert member into DB
    member = Members(
        name="Existing",
        email="existing@example.com",
        phone_number="0500000000",
        uni_id="123456789",
        gender=MembersGender.MALE,
        uni_level=2,
        uni_college="Science",
    )
    db_session.add(member)
    db_session.commit()

    # 2. create member via API with same uni_id
    response = clerk_client.post("/members/")
    assert_2xx(response)
    body = response.json()
    assert body["already_exists"] is True
    assert body["member"]["name"] == "Test Member"


# === GET /me Tests ===


def test_get_current_member(clerk_client: TestClient):
    # 1. create member (self)
    clerk_client.post("/members/")
    # 2. get current member (self)
    response = clerk_client.get("/members/me")
    assert_2xx(response)
    body = response.json()
    assert body["uni_id"] == "123456789"
    assert body["name"] == "Test Member"


def test_get_current_member_not_found(clerk_client: TestClient):
    response = clerk_client.get("/members/me")
    assert_not_found(response)


# === GET / (all members) Tests ===


def test_get_all_members(admin_client: TestClient):
    response = admin_client.get("/members/")
    assert_2xx(response)
    members = response.json()
    assert len(members) == 2, f"Expected 2 seeded members but got {len(members)}"
    names = {m["name"] for m in members}
    assert "Ahmed Ali" in names
    assert "Sara Khalid" in names


def test_unauthorized_get_all_members(clerk_client: TestClient):
    response = clerk_client.get("/members/")
    assert_forbidden(response)


# === GET /uni-id/{uni_id} Tests ===


def test_get_member_by_uni_id(admin_client: TestClient):
    response = admin_client.get("/members/uni-id/111111111")
    assert_2xx(response)
    body = response.json()
    assert body["name"] == "Ahmed Ali"
    assert body["uni_id"] == "111111111"


def test_get_member_by_uni_id_not_found(admin_client: TestClient):
    response = admin_client.get("/members/uni-id/000000000")
    assert_not_found(response)


def test_unauthorized_get_member_by_uni_id(clerk_client: TestClient):
    response = clerk_client.get("/members/uni-id/111111111")
    assert_forbidden(response)


# === GET /{member_id} Tests ===


def test_get_member_by_id(admin_client: TestClient, db_session):
    member = Members(
        name="Fetchable",
        email="fetch@example.com",
        phone_number="0501112233",
        uni_id="888888888",
        gender=MembersGender.MALE,
        uni_level=3,
        uni_college="Engineering",
    )
    db_session.add(member)
    db_session.commit()

    response = admin_client.get(f"/members/{member.id}")
    assert_2xx(response)
    body = response.json()
    assert body["id"] == member.id


def test_get_member_by_id_not_found(admin_client: TestClient):
    response = admin_client.get("/members/9999")
    assert_not_found(response)


def test_unauthorized_get_member_by_id(clerk_client: TestClient):
    response = clerk_client.get("/members/1")
    assert_forbidden(response)


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


def test_update_member_role_from_existing(super_admin_client: TestClient, db_session):
    db_session.add(Role(member_id=1, role=RoleType.ADMIN))
    db_session.commit()

    response = super_admin_client.post("/members/roles", params={"member_id": 1, "new_role": "super_admin"})
    assert_2xx(response)
    body = response.json()
    assert body["role"] == "super_admin"


# === GET /roles Tests ===


def test_get_member_roles(super_admin_client: TestClient, db_session):
    db_session.add(Role(member_id=1, role=RoleType.ADMIN))
    db_session.commit()

    response = super_admin_client.get("/members/roles")
    assert_2xx(response)
    roles = response.json()
    assert len(roles) >= 1, f"Expected at least 1 role but got {len(roles)}"
    admin_role = next((r for r in roles if r["id"] == 1), None)
    assert admin_role is not None, "Expected to find role for member 1"
    assert admin_role["role"] == "admin"


def test_get_member_roles_empty(super_admin_client: TestClient):
    response = super_admin_client.get("/members/roles")
    assert_2xx(response)
    assert response.json() == []


def test_unauthorized_get_member_roles(admin_client: TestClient):
    response = admin_client.get("/members/roles")
    assert_forbidden(response)


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
