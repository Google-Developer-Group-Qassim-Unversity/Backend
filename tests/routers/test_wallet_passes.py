"""Apple's PassKit web service under /wallet/apple/v1, and the welcome from the card.

Wallet is played by the test client: it registers a pass with the pass's own
token, asks which passes changed and fetches the latest one. Pass update
pushes go to a fake APNs (``pass_apns``); the clock is frozen at
2026-07-20 09:00 UTC. See app/services/wallet_updates.py and docs/WALLET.md.
"""

import asyncio
import itertools
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import config, reload_settings
from app.DB import members as member_queries
from app.DB import wallet_passes as pass_queries
from app.DB.schema import MemberProfiles, Members, MembersGender, WalletPassRegistrations
from app.DB.wallet import get_or_create_member_profile
from app.services import wallet_push, wallet_updates
from app.services.push import clock
from app.wallet_signer import pass_serial_number
from tests.utils import assert_2xx
from tests.wallet_support import WEB_SERVICE_URL, pass_json

CLERK_SUB = "clerk_test_member_sub"
DEVICE = "6d1c1a6e7f3b4b0f9c1e2d3a4b5c6d7e"
DEVICE_2 = "0f9e8d7c6b5a49382716051423324150"
PUSH = "ab" * 32
PUSH_2 = "cd" * 32
NOW = datetime(2026, 7, 20, 9, 0)

_numbers = itertools.count(1)


@pytest.fixture
def web_service(pass_updates_on, signing_certs, pass_apns, frozen):
    """Pass updates on, a signing certificate, a fake APNs and the frozen clock."""
    return pass_apns


def make_profile(session, *, uuid: str | None = None, clerk_user_id: str | None = None) -> MemberProfiles:
    n = next(_numbers)
    member = Members(
        name=f"Wallet Holder {n}",
        email=f"wallet-holder-{n}@example.com",
        uni_id=f"44{n:07d}",
        clerk_user_id=clerk_user_id,
        gender=MembersGender.MALE,
    )
    session.add(member)
    session.flush()
    profile = get_or_create_member_profile(session, member.id)
    if uuid is not None:
        profile.uuid = uuid
    session.commit()
    return profile


def auth(profile: MemberProfiles) -> dict[str, str]:
    return {"Authorization": f"ApplePass {wallet_updates.authentication_token(profile.uuid)}"}


def registration_url(profile: MemberProfiles, device: str = DEVICE, pass_type_id: str | None = None) -> str:
    pass_type_id = pass_type_id or config.APPLE_PASS_TYPE_ID
    return f"/wallet/apple/v1/devices/{device}/registrations/{pass_type_id}/{pass_serial_number(profile.uuid)}"


def pass_url(profile: MemberProfiles) -> str:
    return f"/wallet/apple/v1/passes/{config.APPLE_PASS_TYPE_ID}/{pass_serial_number(profile.uuid)}"


def list_url(device: str = DEVICE, pass_type_id: str | None = None) -> str:
    return f"/wallet/apple/v1/devices/{device}/registrations/{pass_type_id or config.APPLE_PASS_TYPE_ID}"


def register(client: TestClient, profile: MemberProfiles, *, device: str = DEVICE, push: str = PUSH, headers=None):
    return client.post(
        registration_url(profile, device),
        json={"pushToken": push},
        headers=auth(profile) if headers is None else headers,
    )


def registrations(session, profile: MemberProfiles | None = None) -> list[WalletPassRegistrations]:
    session.expire_all()
    statement = select(WalletPassRegistrations).order_by(WalletPassRegistrations.created_at)
    if profile is not None:
        statement = statement.where(WalletPassRegistrations.member_id == profile.member_id)
    return list(session.scalars(statement).all())


def fresh(session, profile: MemberProfiles) -> MemberProfiles:
    session.expire_all()
    return session.get_one(MemberProfiles, profile.id)


def deliver() -> wallet_push.PassPushResult:
    return asyncio.run(wallet_push.deliver_due())


# ====================== off until configured ======================


def test_every_route_answers_404_while_off(client: TestClient, db_session):
    profile = make_profile(db_session)
    headers = {"Authorization": "ApplePass whatever-it-is-1234"}

    assert client.post(registration_url(profile), json={"pushToken": PUSH}, headers=headers).status_code == 404
    assert client.delete(registration_url(profile), headers=headers).status_code == 404
    assert client.get(list_url()).status_code == 404
    assert client.get(pass_url(profile), headers=headers).status_code == 404
    assert client.post("/wallet/apple/v1/log", json={"logs": ["x"]}).status_code == 404
    assert registrations(db_session) == []


def test_the_url_without_the_secret_is_still_off(client: TestClient, db_session, monkeypatch):
    monkeypatch.setenv("APPLE_PASS_WEB_SERVICE_URL", WEB_SERVICE_URL)
    monkeypatch.delenv("APPLE_PASS_AUTH_SECRET", raising=False)
    reload_settings()

    assert not config.apple_pass_updates_enabled
    assert client.get(list_url()).status_code == 404


def test_a_members_pass_is_unchanged_while_off(clerk_client: TestClient, db_session, signing_certs):
    make_profile(db_session, clerk_user_id=CLERK_SUB)

    response = clerk_client.post("/wallet/apple-pass", json={})

    assert_2xx(response)
    issued = pass_json(response.content)
    assert "webServiceURL" not in issued and "authenticationToken" not in issued
    assert [field["key"] for field in issued["eventTicket"]["backFields"]] == ["email", "major", "level", "club_name"]


# ====================== the pass carries the web service ======================


def test_a_members_pass_carries_the_web_service_and_the_placeholder(clerk_client: TestClient, db_session, web_service):
    profile = make_profile(db_session, clerk_user_id=CLERK_SUB)

    issued = pass_json(clerk_client.post("/wallet/apple-pass", json={}).content)

    assert issued["webServiceURL"] == WEB_SERVICE_URL, "the trailing slash is dropped: Wallet appends /v1/..."
    token = issued["authenticationToken"]
    assert token == wallet_updates.authentication_token(profile.uuid)
    assert len(token) >= 16
    assert issued["serialNumber"] == f"GDGQ-{profile.uuid[:8].upper()}"
    message = issued["eventTicket"]["backFields"][0]
    assert message["key"] == "club_message" and message["label"] == "رسالة النادي"
    assert message["value"] == wallet_updates.PLACEHOLDER
    assert message["changeMessage"] == "%@"


def test_a_guest_pass_never_carries_the_web_service(client: TestClient, web_service):
    issued = pass_json(client.post("/wallet/apple-pass", json={"fullName": "زائر"}).content)

    assert "webServiceURL" not in issued and "authenticationToken" not in issued
    assert all(field["key"] != "club_message" for field in issued["eventTicket"]["backFields"])


def test_each_member_gets_a_token_of_their_own(db_session, pass_updates_on, monkeypatch):
    first, second = make_profile(db_session), make_profile(db_session)
    token = wallet_updates.authentication_token(first.uuid)

    assert token == wallet_updates.authentication_token(first.uuid)
    assert token != wallet_updates.authentication_token(second.uuid)
    monkeypatch.setenv("APPLE_PASS_AUTH_SECRET", "another-secret-entirely-0123456789")
    reload_settings()
    assert wallet_updates.authentication_token(first.uuid) != token


# ====================== registering ======================


def test_registering_a_pass_stores_the_device(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)

    response = register(client, profile, push=PUSH.upper())

    assert response.status_code == 201
    [row] = registrations(db_session)
    assert row.member_id == profile.member_id
    assert row.device_library_id == DEVICE
    assert row.serial_number == pass_serial_number(profile.uuid)
    assert row.push_token == PUSH, "stored lowercase"


def test_registering_again_is_a_200_and_refreshes_the_push_token(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    assert register(client, profile).status_code == 201

    response = register(client, profile, push=PUSH_2)

    assert response.status_code == 200
    [row] = registrations(db_session)
    assert row.push_token == PUSH_2
    assert len(web_service.requests) == 1, "the welcome was pushed once"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "ApplePass not-the-token-of-this-pass"},
        {"Authorization": "Bearer something"},
        {"Authorization": "ApplePass"},
    ],
    ids=["none", "wrong", "bearer", "empty"],
)
def test_registering_without_the_pass_token_is_refused(client: TestClient, db_session, web_service, headers):
    profile = make_profile(db_session)

    response = register(client, profile, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApplePass"
    assert registrations(db_session) == []
    assert fresh(db_session, profile).pass_welcomed_at is None


def test_another_members_token_does_not_open_this_pass(client: TestClient, db_session, web_service):
    profile, other = make_profile(db_session), make_profile(db_session)

    assert register(client, profile, headers=auth(other)).status_code == 401


def test_an_unknown_serial_is_refused(client: TestClient, db_session, web_service):
    profile = make_profile(db_session, uuid="1234abcd" + "0" * 24)
    url = registration_url(profile).replace("GDGQ-1234ABCD", "GDGQ-FFFFFFFF")

    assert client.post(url, json={"pushToken": PUSH}, headers=auth(profile)).status_code == 401
    assert client.post(url.replace("GDGQ-FFFFFFFF", "GDGQ-1234AB%25"), json={"pushToken": PUSH}).status_code == 401


def test_another_pass_type_is_refused(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    url = registration_url(profile, pass_type_id="pass.com.example.other")

    assert client.post(url, json={"pushToken": PUSH}, headers=auth(profile)).status_code == 401


def test_a_registration_without_a_push_token_is_refused(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)

    assert client.post(registration_url(profile), json={}, headers=auth(profile)).status_code == 422
    assert client.post(registration_url(profile), json={"pushToken": "zz"}, headers=auth(profile)).status_code == 422
    assert registrations(db_session) == []


# ====================== the welcome ======================


def test_the_first_registration_welcomes_the_member_with_a_push_to_that_device(
    client: TestClient, db_session, web_service
):
    profile = make_profile(db_session)
    edited_at = fresh(db_session, profile).updated_at

    assert register(client, profile).status_code == 201

    welcomed = fresh(db_session, profile)
    assert welcomed.pass_welcomed_at == NOW
    assert welcomed.pass_updated_at == NOW
    assert welcomed.updated_at == edited_at, "the welcome is not a card edit"
    [request] = web_service.requests
    assert request.url == httpx.URL(f"https://api.push.apple.com/3/device/{PUSH}")
    assert request.headers["apns-topic"] == config.APPLE_PASS_TYPE_ID
    assert request.content == b"{}"
    assert "apns-push-type" not in request.headers
    assert "authorization" not in request.headers, "certificate auth: no provider token"
    [row] = registrations(db_session)
    assert row.push_due_at is None and row.push_attempts == 0


def test_the_welcome_goes_out_at_night_too(client: TestClient, db_session, web_service, frozen):
    frozen.set(datetime(2026, 7, 20, 21, 30))  # 00:30 in Riyadh, inside quiet hours
    profile = make_profile(db_session)

    register(client, profile)

    assert len(web_service.requests) == 1


def test_the_pass_fetched_after_the_push_carries_the_welcome(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)

    response = client.get(pass_url(profile), headers=auth(profile))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.apple.pkpass"
    assert response.headers["last-modified"] == format_datetime(NOW.replace(tzinfo=UTC), usegmt=True)
    message = pass_json(response.content)["eventTicket"]["backFields"][0]
    assert message == {
        "key": "club_message",
        "label": "رسالة النادي",
        "value": wallet_updates.WELCOME,
        "changeMessage": "%@",
    }
    assert "⁨GDG Qassim⁩" in wallet_updates.WELCOME, "the Latin name is isolated, as in messages.py"
    assert wallet_updates.WELCOME.startswith("أهلًا بك في")
    assert wallet_updates.WELCOME.endswith("متحمسين نشوفك قريب في فعالياتنا")


def test_a_second_device_is_not_welcomed_again(client: TestClient, db_session, web_service, frozen):
    profile = make_profile(db_session)
    register(client, profile)
    frozen.advance(hours=3)

    assert register(client, profile, device=DEVICE_2, push=PUSH_2).status_code == 201

    assert len(web_service.requests) == 1
    assert fresh(db_session, profile).pass_welcomed_at == NOW
    assert len(registrations(db_session, profile)) == 2


def test_a_re_added_card_is_not_welcomed_again(client: TestClient, db_session, web_service, frozen):
    profile = make_profile(db_session)
    register(client, profile)
    frozen.advance(days=2)
    assert client.delete(registration_url(profile), headers=auth(profile)).status_code == 200

    assert register(client, profile).status_code == 201

    assert len(web_service.requests) == 1
    assert fresh(db_session, profile).pass_updated_at == NOW


def test_a_pass_issued_after_the_welcome_already_carries_it(clerk_client: TestClient, db_session, web_service):
    profile = make_profile(db_session, clerk_user_id=CLERK_SUB)
    register(clerk_client, profile)

    issued = pass_json(clerk_client.post("/wallet/apple-pass", json={}).content)

    assert issued["eventTicket"]["backFields"][0]["value"] == wallet_updates.WELCOME


def test_every_member_is_welcomed_once_on_their_own_device(client: TestClient, db_session, web_service):
    first, second = make_profile(db_session), make_profile(db_session)

    register(client, first)
    register(client, second, device=DEVICE_2, push=PUSH_2)

    assert web_service.tokens() == [PUSH, PUSH_2]
    assert fresh(db_session, first).pass_welcomed_at is not None
    assert fresh(db_session, second).pass_welcomed_at is not None


def test_the_welcome_is_marked_once_however_many_ask(db_session, frozen):
    profile = make_profile(db_session)

    assert pass_queries.mark_welcomed(db_session, profile.id, NOW) is True
    assert pass_queries.mark_welcomed(db_session, profile.id, NOW + timedelta(seconds=1)) is False
    assert fresh(db_session, profile).pass_welcomed_at == NOW


# ====================== a push that fails ======================


@pytest.mark.parametrize(
    "answer", [httpx.ConnectError("APNs is down"), (500, {"reason": "InternalServerError"})], ids=["down", "500"]
)
def test_a_failed_push_never_breaks_the_registration(client: TestClient, db_session, web_service, answer):
    profile = make_profile(db_session)
    web_service.answer(PUSH, answer)

    assert register(client, profile).status_code == 201

    assert fresh(db_session, profile).pass_welcomed_at == NOW
    [row] = registrations(db_session)
    assert row.push_attempts == 1
    assert row.push_due_at == NOW + timedelta(minutes=2), "retried after the next sweep"


def test_a_dropped_connection_is_tried_again_at_once(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    web_service.answer(PUSH, httpx.ReadError("connection reset"), (200, None))

    register(client, profile)

    assert len(web_service.requests) == 2
    [row] = registrations(db_session)
    assert row.push_due_at is None


def test_a_failed_push_is_retried_later_and_then_given_up(client: TestClient, db_session, web_service, frozen):
    profile = make_profile(db_session)
    web_service.answer(PUSH, (500, None))
    register(client, profile)

    for _ in range(wallet_push.MAX_ATTEMPTS - 1):
        frozen.advance(hours=1)
        deliver()

    [row] = registrations(db_session)
    assert row.push_attempts == wallet_push.MAX_ATTEMPTS
    assert row.push_due_at is None, "given up"
    frozen.advance(hours=1)
    assert deliver().claimed == 0


def test_a_retry_that_gets_through_clears_the_debt(client: TestClient, db_session, web_service, frozen):
    profile = make_profile(db_session)
    web_service.answer(PUSH, (503, None), (200, None))
    register(client, profile)
    assert deliver().claimed == 0, "not due yet"

    frozen.advance(minutes=3)
    result = deliver()

    assert result.sent == 1
    [row] = registrations(db_session)
    assert row.push_due_at is None and row.push_attempts == 0


@pytest.mark.parametrize(
    "answer",
    [
        (410, {"reason": "Unregistered", "timestamp": clock.epoch(NOW + timedelta(minutes=1)) * 1000}),
        (400, {"reason": "BadDeviceToken"}),
    ],
    ids=["410", "bad-token"],
)
def test_a_dead_push_token_deletes_its_registration(client: TestClient, db_session, web_service, answer):
    profile = make_profile(db_session)
    web_service.answer(PUSH, answer)

    assert register(client, profile).status_code == 201

    assert registrations(db_session) == []


def test_a_missing_certificate_keeps_the_push_owed(client: TestClient, db_session, web_service, monkeypatch):
    profile = make_profile(db_session)
    wallet_push.set_client(None)  # built from the p12 on first use
    monkeypatch.delenv("APPLE_P12_BASE64")
    monkeypatch.setenv("APPLE_P12_PATH", "/nonexistent/Certificates.p12")
    reload_settings()

    assert register(client, profile).status_code == 201

    [row] = registrations(db_session)
    assert row.push_attempts == 1 and row.push_due_at is not None


def test_an_unexpected_delivery_error_never_breaks_the_registration(
    client: TestClient, db_session, web_service, monkeypatch
):
    async def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(wallet_push, "deliver_due", explode)
    profile = make_profile(db_session)

    assert register(client, profile).status_code == 201
    assert fresh(db_session, profile).pass_welcomed_at == NOW


def test_nothing_is_sent_while_off(db_session, pass_apns, frozen):
    profile = make_profile(db_session)
    registration, _ = pass_queries.register(
        db_session, profile.member_id, device_library_id=DEVICE, serial_number="GDGQ-00000000", push_token=PUSH, now=NOW
    )
    pass_queries.owe_push(db_session, registration.id, NOW)
    db_session.commit()

    assert deliver().claimed == 0
    assert pass_apns.requests == []


# ====================== unregistering ======================


def test_unregistering_deletes_the_devices_registration(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)
    register(client, profile, device=DEVICE_2, push=PUSH_2)

    assert client.delete(registration_url(profile), headers=auth(profile)).status_code == 200

    assert [row.device_library_id for row in registrations(db_session)] == [DEVICE_2]
    assert client.delete(registration_url(profile), headers=auth(profile)).status_code == 200, "already gone"


def test_unregistering_needs_the_pass_token(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)

    assert (
        client.delete(registration_url(profile), headers={"Authorization": "ApplePass nope-nope-nope"}).status_code
        == 401
    )
    assert len(registrations(db_session)) == 1


# ====================== which passes changed ======================


def test_a_device_with_no_registration_has_nothing_to_update(client: TestClient, web_service):
    assert client.get(list_url()).status_code == 204


def test_without_a_tag_every_registered_pass_is_listed(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)

    response = client.get(list_url())

    assert response.status_code == 200
    assert response.json() == {
        "serialNumbers": [pass_serial_number(profile.uuid)],
        "lastUpdated": str(clock.epoch(NOW)),
    }


def test_the_tag_limits_the_list_to_what_changed_since(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)
    tag = clock.epoch(NOW)

    assert client.get(list_url(), params={"passesUpdatedSince": str(tag)}).status_code == 204
    older = client.get(list_url(), params={"passesUpdatedSince": str(tag - 1)})
    assert older.status_code == 200 and older.json()["serialNumbers"] == [pass_serial_number(profile.uuid)]
    assert client.get(list_url(), params={"passesUpdatedSince": "not-ours"}).status_code == 200, "a tag not ours: all"


def test_another_pass_type_or_device_lists_nothing(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)

    assert client.get(list_url(pass_type_id="pass.com.example.other")).status_code == 204
    assert client.get(list_url(device=DEVICE_2)).status_code == 204


# ====================== the latest pass ======================


def test_the_latest_pass_before_any_welcome_carries_the_placeholder(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)

    response = client.get(pass_url(profile), headers=auth(profile))

    assert response.status_code == 200
    assert "last-modified" not in response.headers
    assert pass_json(response.content)["eventTicket"]["backFields"][0]["value"] == wallet_updates.PLACEHOLDER


def test_the_latest_pass_is_304_when_wallet_already_has_it(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)
    last_modified = client.get(pass_url(profile), headers=auth(profile)).headers["last-modified"]

    same = client.get(pass_url(profile), headers={**auth(profile), "If-Modified-Since": last_modified})
    earlier = format_datetime((NOW - timedelta(seconds=1)).replace(tzinfo=UTC), usegmt=True)
    older = client.get(pass_url(profile), headers={**auth(profile), "If-Modified-Since": earlier})
    garbage = client.get(pass_url(profile), headers={**auth(profile), "If-Modified-Since": "yesterday-ish"})

    assert same.status_code == 304 and same.content == b""
    assert same.headers["last-modified"] == last_modified
    assert older.status_code == 200
    assert garbage.status_code == 200


def test_the_latest_pass_needs_the_pass_token(client: TestClient, db_session, web_service):
    profile = make_profile(db_session, uuid="1234abcd" + "0" * 24)

    assert client.get(pass_url(profile)).status_code == 401
    assert client.get(pass_url(profile), headers={"Authorization": "ApplePass nope-nope-nope"}).status_code == 401
    unknown = pass_url(profile).replace("GDGQ-1234ABCD", "GDGQ-FFFFFFFF")
    assert client.get(unknown, headers=auth(profile)).status_code == 401


def test_colliding_serials_are_told_apart_by_the_token(client: TestClient, db_session, web_service):
    """Two cards whose uuids share their first 8 characters have one serial; each token opens only its own."""
    first = make_profile(db_session, uuid="abcd1234" + "1" * 24)
    second = make_profile(db_session, uuid="ABCD1234" + "2" * 24)
    assert pass_serial_number(first.uuid) == pass_serial_number(second.uuid)

    names = [
        pass_json(client.get(pass_url(profile), headers=auth(profile)).content)["eventTicket"]["primaryFields"][0][
            "value"
        ]
        for profile in (first, second)
    ]
    register(client, first)
    register(client, second, device=DEVICE_2, push=PUSH_2)

    assert names == [first.member.name, second.member.name]
    assert {row.member_id for row in registrations(db_session)} == {first.member_id, second.member_id}
    assert web_service.tokens() == [PUSH, PUSH_2], "each member welcomed once"


# ====================== the log, and what stays out of logs ======================


def test_wallets_log_is_kept_without_the_device_id(client: TestClient, web_service, caplog):
    line = f"Web service error for pass: /wallet/apple/v1/devices/{DEVICE}/registrations/x returned 500"

    with caplog.at_level(logging.INFO):
        response = client.post("/wallet/apple/v1/log", json={"logs": [line] * 20})

    assert response.status_code == 200
    reported = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Apple Wallet reported")]
    assert len(reported) == 10
    assert all("/v1/devices/[device]/" in message for message in reported)
    assert DEVICE not in caplog.text


def test_request_logs_never_show_the_device_id_or_tokens(client: TestClient, db_session, web_service, caplog):
    profile = make_profile(db_session)
    token = wallet_updates.authentication_token(profile.uuid)
    assert token is not None

    with caplog.at_level(logging.INFO):
        register(client, profile)
        client.get(list_url())
        client.get(pass_url(profile), headers=auth(profile))

    assert "/wallet/apple/v1/devices/[device]/registrations/" in caplog.text
    for secret in (DEVICE, PUSH, token):
        assert secret not in caplog.text


# ====================== account deletion ======================


def test_deleting_the_account_deletes_the_registrations(client: TestClient, db_session, web_service):
    profile = make_profile(db_session)
    register(client, profile)
    register(client, profile, device=DEVICE_2, push=PUSH_2)
    member = db_session.get_one(Members, profile.member_id)

    member_queries.anonymize_member(db_session, member)
    db_session.commit()

    assert registrations(db_session) == []
    assert client.get(pass_url(profile), headers=auth(profile)).status_code == 401
