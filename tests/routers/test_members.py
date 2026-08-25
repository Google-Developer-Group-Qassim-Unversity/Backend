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


def test_create_member_without_academic_info(client: TestClient):
    """Google sign-ups may skip uni_level/uni_college entirely (e.g. non-students) -
    the backend must accept a member with no academic info rather than 500ing."""
    from app.main import app
    from app.helpers import authenticated_guard
    from fastapi_clerk_auth import HTTPAuthorizationCredentials as ClerkHTTPAuthorizationCredentials

    no_academic_creds = ClerkHTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="fake-token-no-academic",
        decoded={
            "sub": "clerk_no_academic_sub",
            "metadata": {
                "fullArabicName": "No Academic Member",
                "saudiPhone": "0501234567",
                "gender": "Male",
                "personalEmail": "no-academic@example.com",
            },
        },
    )
    app.dependency_overrides[authenticated_guard] = lambda: no_academic_creds
    try:
        response = client.post("/members/")
    finally:
        app.dependency_overrides.clear()

    assert_2xx(response)
    body = response.json()
    assert body["member"]["uni_level"] is None
    assert body["member"]["uni_college"] is None


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


# === POST /manual Tests ===


def test_create_member_manual_success(super_admin_client: TestClient):
    response = super_admin_client.post(
        "/members/manual", json={"name": "Manual Member", "email": "manual@example.com", "gender": "Male"}
    )
    assert_2xx(response)
    body = response.json()
    assert body["already_exists"] is False
    assert body["member"]["email"] == "manual@example.com"


def test_create_member_manual_email_conflict(super_admin_client: TestClient, seed_refs):
    response = super_admin_client.post(
        "/members/manual", json={"name": "Duplicate Email", "email": seed_refs.ahmed.email, "gender": "Male"}
    )
    assert response.status_code == 409
    assert "email" in response.json()["detail"].lower()


def test_create_member_manual_uni_id_conflict_checked_before_email(super_admin_client: TestClient, seed_refs):
    response = super_admin_client.post(
        "/members/manual",
        json={
            "name": "Duplicate Uni Id",
            "email": "brandnew@example.com",
            "uni_id": seed_refs.ahmed.uni_id,
            "gender": "Male",
        },
    )
    assert response.status_code == 409
    assert "uni_id" in response.json()["detail"].lower()


# === POST /batch Tests ===


def test_batch_create_members_skips_email_conflict(super_admin_client: TestClient, seed_refs):
    response = super_admin_client.post(
        "/members/batch",
        json={
            "members": [
                {"name": "New Member", "email": "brandnew2@example.com", "uni_id": "555555555", "gender": "Male"},
                {"name": "Email Clash", "email": seed_refs.ahmed.email, "uni_id": "555555556", "gender": "Male"},
            ]
        },
    )
    assert_2xx(response)
    body = response.json()
    assert body["created_count"] == 1
    assert body["failed_count"] == 1
    assert body["existing_count"] == 0


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
    assert len(members) == 2
    names = {m["name"] for m in members}
    assert "Ahmed Ali" in names
    assert "Sara Khalid" in names


def test_unauthorized_get_all_members(clerk_client: TestClient):
    response = clerk_client.get("/members/")
    assert_forbidden(response)


# === GET /uni-id/{uni_id} Tests ===


def test_get_member_by_uni_id(admin_client: TestClient, seed_refs):
    response = admin_client.get(f"/members/uni-id/{seed_refs.ahmed.uni_id}")
    assert_2xx(response)
    body = response.json()
    assert body["name"] == seed_refs.ahmed.name
    assert body["uni_id"] == seed_refs.ahmed.uni_id


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


def test_update_member_role_from_existing(super_admin_client: TestClient, db_session, seed_refs):
    db_session.add(Role(member_id=seed_refs.ahmed.id, role=RoleType.ADMIN))
    db_session.commit()

    response = super_admin_client.post(
        "/members/roles", params={"member_id": seed_refs.ahmed.id, "new_role": "super_admin"}
    )
    assert_2xx(response)
    body = response.json()
    assert body["role"] == "super_admin"


# === GET /roles Tests ===


def test_get_member_roles(super_admin_client: TestClient, db_session, seed_refs):
    db_session.add(Role(member_id=seed_refs.ahmed.id, role=RoleType.ADMIN))
    db_session.commit()

    response = super_admin_client.get("/members/roles")
    assert_2xx(response)
    roles = response.json()
    assert len(roles) >= 1, f"Expected at least 1 role but got {len(roles)}"
    admin_role = next((r for r in roles if r["id"] == seed_refs.ahmed.id), None)
    assert admin_role is not None, f"Expected to find role for member {seed_refs.ahmed.id}"
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


def test_update_member_email_conflict(clerk_client: TestClient, seed_refs):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"email": seed_refs.ahmed.email})
    assert response.status_code == 409
    assert "email" in response.json()["detail"].lower()


def test_update_member_email_unchanged_no_conflict(clerk_client: TestClient):
    create_resp = clerk_client.post("/members/")
    own_email = create_resp.json()["member"]["email"]
    response = clerk_client.patch("/members/me", json={"email": own_email, "name": "Still Me"})
    assert_2xx(response)
    assert response.json()["name"] == "Still Me"


def test_update_member_invalid_gender(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"gender": "Other"})
    assert_unprocessable(response)


def test_update_member_invalid_email(clerk_client: TestClient):
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"email": "not-an-email"})
    assert_unprocessable(response)


# === Automatic Email Fold Tests ===
# clerk_client's FAKE_CLERK_CREDENTIALS carries personalEmail "test@example.com"
# and sub "clerk_test_member_sub" (see tests/conftest.py).


def test_create_member_auto_folds_unclaimed_email_match(clerk_client: TestClient, db_session):
    """POST /members/ (the plain signup path, not /claim) should fold an unclaimed
    admin-created row into the new Clerk identity automatically when the email matches -
    no separate claim confirmation needed since Clerk verifies the signup email."""
    shadow = Members(
        name="Shadow Member",
        email="test@example.com",
        phone_number="0500000000",
        uni_id=None,
        gender=MembersGender.MALE,
        uni_level=0,
        uni_college="UNKNOWN",
        is_authenticated=False,
    )
    db_session.add(shadow)
    db_session.commit()
    shadow_id = shadow.id

    response = clerk_client.post("/members/")
    assert_2xx(response)
    body = response.json()
    assert body["already_exists"] is True
    assert body["member"]["id"] == shadow_id
    assert body["member"]["name"] == "Test Member"
    assert body["member"]["is_authenticated"] is True
    # onboarding uni_id from Clerk metadata is now recorded onto the folded row
    assert body["member"]["uni_id"] == "123456789"

    # No duplicate row was created
    me_response = clerk_client.get("/members/me")
    assert_2xx(me_response)
    assert me_response.json()["id"] == shadow_id


def test_create_member_does_not_fold_already_claimed_email(clerk_client: TestClient, db_session):
    """A row that's already claimed (has a clerk_user_id) must not be silently re-folded
    just because the email matches. Since email is unique now, a second signup sharing that
    email can't create a new row either - it should get a clean conflict, not a crash."""
    claimed = Members(
        name="Already Claimed",
        email="test@example.com",
        phone_number="0500000000",
        uni_id="999999996",
        gender=MembersGender.MALE,
        uni_level=2,
        uni_college="Science",
        is_authenticated=True,
        clerk_user_id="some_other_clerk_user",
    )
    db_session.add(claimed)
    db_session.commit()

    response = clerk_client.post("/members/")
    assert response.status_code == 409
    assert "email" in response.json()["detail"].lower()
