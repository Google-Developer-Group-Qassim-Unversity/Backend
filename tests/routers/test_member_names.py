"""Public aliases never overwrite official identity or wallet preferences."""

import pytest

from app.DB.schema import Members, MembersGender
from app.DB.wallet import get_or_create_member_profile
from app.member_names import initial_public_name


@pytest.fixture
def refresh_cache(monkeypatch):
    calls = []
    monkeypatch.setattr("app.routers.members.refresh_member_names_cache", lambda: calls.append(True))
    monkeypatch.setattr("app.routers.wallet.refresh_member_names_cache", lambda: calls.append(True))
    return calls


def test_creation_defaults_and_partial_member_updates(clerk_client, refresh_cache):
    created = clerk_client.post("/members/").json()["member"]
    assert created["public_name"] == "Test Member"
    response = clerk_client.patch("/members/me", json={"public_name": "  Cloud Explorer Community  "})
    assert response.status_code == 200
    assert response.json()["public_name"] == "Cloud Explorer Community"
    assert response.json()["name"] == "Test Member"
    assert len(refresh_cache) == 1

    response = clerk_client.patch("/members/me", json={"name": "Ahmed Mohammed Ali Hassan"})
    assert response.status_code == 200
    assert response.json()["public_name"] == "Cloud Explorer Community"
    assert len(refresh_cache) == 1


def test_unconfigured_cache_does_not_turn_a_saved_name_into_an_error(clerk_client, monkeypatch):
    from app.config import MissingSettingError

    def unavailable():
        raise MissingSettingError("MEMBER_APP_URL")

    monkeypatch.setattr("app.leaderboard_cache.reset_leaderboard_cache", unavailable)
    clerk_client.post("/members/")
    response = clerk_client.patch("/members/me", json={"public_name": "Saved Alias"})
    assert response.status_code == 200
    assert clerk_client.get("/members/me").json()["public_name"] == "Saved Alias"


@pytest.mark.parametrize("value", [None, "", "   ", "a" * 151, "Name\nHidden", "Name\x00"])
@pytest.mark.parametrize("endpoint", ["/members/me", "/wallet/me"])
def test_invalid_public_names_are_rejected(clerk_client, endpoint, value, refresh_cache):
    clerk_client.post("/members/")
    response = clerk_client.patch(endpoint, json={"public_name": value})
    assert response.status_code == 422
    assert clerk_client.get("/members/me").json()["public_name"] == "Test Member"
    assert not refresh_cache


def test_profile_edit_separates_all_three_names(clerk_client, client, refresh_cache, monkeypatch):
    clerk_client.post("/members/")
    response = clerk_client.put(
        "/wallet/me",
        json={"official_name": "Ahmed Mohammed Ali Hassan", "public_name": "مستكشف السحابة", "bio": "Developer"},
    )
    assert response.status_code == 200
    assert response.json()["official_name"] == "Ahmed Mohammed Ali Hassan"
    assert response.json()["public_name"] == "مستكشف السحابة"
    assert response.json()["profile"]["custom_name"] is None
    uuid = response.json()["profile"]["uuid"]

    # Subsequent card customization and official-name correction leave the public alias alone.
    response = clerk_client.put("/wallet/me", json={"custom_name": "Ahmed on card", "official_name": "Ahmed Hassan"})
    assert response.status_code == 200
    own_profile = clerk_client.get("/wallet/me").json()
    assert own_profile["name"] == "Ahmed on card"
    assert own_profile["official_name"] == "Ahmed Hassan"
    assert own_profile["public_name"] == "مستكشف السحابة"
    assert len(refresh_cache) == 1

    public = client.get(f"/wallet/{uuid}")
    assert public.status_code == 200
    assert public.json()["name"] == "مستكشف السحابة"
    assert "official_name" not in public.json()
    assert "custom_name" not in public.json()
    assert "Ahmed Hassan" not in public.text
    assert "Ahmed on card" not in public.text

    captured = {}

    def sign(card):
        captured.update(card)
        return "https://pay.google.com/gp/v/save/test"

    monkeypatch.setattr("app.routers.wallet.generate_google_wallet_pass_url", sign)
    assert clerk_client.post("/wallet/google-pass", json={}).status_code == 200
    assert captured["fullName"] == "Ahmed on card"


def test_public_leaderboard_and_history_use_alias(client, seed_refs, db_session):
    member = seed_refs.ahmed
    member.name = "Private Official Full Name"
    member.public_name = "Public Alias With Spaces"
    db_session.commit()
    response = client.get("/points/members/total")
    assert response.status_code == 200
    item = next(row for row in response.json() if row["member_id"] == member.id)
    assert item["member_name"] == member.public_name
    assert member.name not in response.text
    history = client.get(f"/points/members/{member.id}")
    assert history.status_code == 200
    assert history.json()["member"]["member_name"] == member.public_name
    assert member.name not in history.text


def test_imports_without_profile_get_compound_name_default(db_session):
    member = Members(name="بدر خالد الدخيل الله", email="public-default@example.com", gender=MembersGender.MALE)
    db_session.add(member)
    db_session.flush()
    assert member.public_name == "بدر الدخيل الله"
    profile = get_or_create_member_profile(db_session, member.id)
    assert profile.custom_name is None


def test_missing_public_alias_never_falls_back_to_full_name(client, seed_refs, db_session):
    member = seed_refs.ahmed
    member.public_name = ""
    profile = get_or_create_member_profile(db_session, member.id)
    db_session.commit()
    assert client.get(f"/wallet/{profile.uuid}").json()["name"] == "Member"
    result = client.get(f"/points/members/{member.id}").json()
    assert result["member"]["member_name"] == "Member"


@pytest.mark.parametrize(
    ("full_name", "expected"), [("Ahmed Mohammed Hassan", "Ahmed Hassan"), ("  Ahmed  ", "Ahmed"), ("", "Member")]
)
def test_initial_public_name(full_name, expected):
    assert initial_public_name(full_name) == expected
