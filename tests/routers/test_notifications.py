"""The member app's notification routes: phones, switches, the feed and announcements (docs/PUSH.md)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.DB.schema import (
    MemberDevices,
    MemberNotificationPrefs,
    PushBroadcasts,
    PushKind,
    PushNotifications,
    PushStatus,
)
from app.services.push import outbox
from app.services.push.outbox import Push
from tests.push_support import add_device, make_event, make_member, make_token, register, set_prefs

TOKEN = "AB" * 32


def device_body(**overrides):
    body = {
        "token": TOKEN,
        "platform": "ios",
        "environment": "production",
        "app_version": "1.0.2",
        "app_build": "2",
        "locale": "ar",
    }
    body.update(overrides)
    return body


@pytest.fixture
def member(club):
    person = club.person()
    club.sign_in(person)
    return person


def devices(session, **where):
    session.expire_all()
    statement = select(MemberDevices)
    for key, value in where.items():
        statement = statement.where(getattr(MemberDevices, key) == value)
    return session.scalars(statement).all()


# ---------------------------------------------------------------- POST /notifications/devices


def test_registering_a_phone_stores_its_token_lowercase(club, member, db_session, frozen):
    response = club.client.post("/notifications/devices", json=device_body())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["registered"] is True
    [row] = devices(db_session, member_id=member.id)
    assert row.id == body["id"]
    assert row.token == TOKEN.lower()
    assert (row.environment.value, row.locale.value, row.app_version, row.app_build) == (
        "production",
        "ar",
        "1.0.2",
        "2",
    )
    assert row.last_seen_at == frozen.now


def test_registering_again_refreshes_the_same_row(club, member, db_session, frozen):
    first = club.client.post("/notifications/devices", json=device_body()).json()
    frozen.advance(days=1)
    second = club.client.post("/notifications/devices", json=device_body(locale="en", app_build="3")).json()
    assert first["id"] == second["id"]
    [row] = devices(db_session, member_id=member.id)
    assert (row.locale.value, row.app_build, row.last_seen_at) == ("en", "3", frozen.now)


def test_a_phone_signed_in_as_someone_else_moves_to_them(club, member, db_session):
    club.client.post("/notifications/devices", json=device_body())
    other = club.person()
    club.sign_in(other)
    response = club.client.post("/notifications/devices", json=device_body())
    assert response.status_code == 200
    assert [d.member_id for d in devices(db_session, token=TOKEN.lower())] == [other.id]


def test_a_sixth_phone_drops_the_one_seen_longest_ago(club, member, db_session, frozen):
    for n in range(6):
        frozen.advance(minutes=1)
        club.client.post("/notifications/devices", json=device_body(token=make_token(n + 1)))
    tokens = {d.token for d in devices(db_session, member_id=member.id)}
    assert len(tokens) == 5 and make_token(1) not in tokens


def _welcomes(session):
    session.expire_all()
    return session.scalars(select(PushNotifications).where(PushNotifications.kind == PushKind.WELCOME)).all()


def test_a_new_members_first_phone_gets_the_welcome(club, member, db_session, push_on, apns_mock, frozen):
    member.created_at = frozen.now - timedelta(days=2)
    db_session.commit()
    assert club.client.post("/notifications/devices", json=device_body()).status_code == 200
    [row] = _welcomes(db_session)
    assert (row.member_id, row.dedupe_key) == (member.id, f"welcome:{member.id}")
    assert row.payload["url"] == "gdgq://notifications"
    assert row.payload["en"]["title"] == "Welcome to GDG Qassim"
    assert row.status == PushStatus.SENT and len(apns_mock.requests) == 1
    # The same phone again, a second phone, or the first one signed out and back in: no second welcome.
    club.client.post("/notifications/devices", json=device_body())
    club.client.post("/notifications/devices", json=device_body(token="CD" * 32))
    for row in devices(db_session, member_id=member.id):
        db_session.delete(row)
    db_session.commit()
    club.client.post("/notifications/devices", json=device_body())
    assert len(_welcomes(db_session)) == 1


def test_no_welcome_for_a_member_who_joined_over_a_week_ago(club, member, db_session, push_on, apns_mock, frozen):
    member.created_at = frozen.now - timedelta(days=8)
    db_session.commit()
    assert club.client.post("/notifications/devices", json=device_body()).status_code == 200
    assert _welcomes(db_session) == []


def test_no_welcome_for_a_member_who_already_had_a_phone(club, member, db_session, push_on, apns_mock, frozen):
    member.created_at = frozen.now - timedelta(days=1)
    add_device(db_session, member, token=make_token())
    db_session.commit()
    club.client.post("/notifications/devices", json=device_body())
    assert _welcomes(db_session) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"token": "ab" * 31},  # 62 characters
        {"token": "zz" * 32},
        {"token": "ab" * 101},
        {"platform": "android"},
        {"environment": "sandbox"},
        {"locale": "fr"},
        {"app_version": ""},
        {"member_id": 1},
    ],
)
def test_a_bad_registration_is_a_422(club, member, overrides):
    assert club.client.post("/notifications/devices", json=device_body(**overrides)).status_code == 422


def test_registering_needs_a_member_record(club):
    club.sign_in(None)  # a Clerk user who never onboarded
    assert club.client.post("/notifications/devices", json=device_body()).status_code == 404


def test_registering_needs_a_session(client):
    assert client.post("/notifications/devices", json=device_body()).status_code == 403


# ---------------------------------------------------------------- DELETE /notifications/devices/{id}


def test_a_member_removes_their_own_phone_once(club, member, db_session):
    device_id = club.client.post("/notifications/devices", json=device_body()).json()["id"]
    assert club.client.delete(f"/notifications/devices/{device_id}").json() == {"deleted": True}
    assert club.client.delete(f"/notifications/devices/{device_id}").json() == {"deleted": False}
    assert devices(db_session, member_id=member.id) == []


def test_nobody_removes_someone_elses_phone(club, member, db_session):
    device = add_device(db_session, club.person())
    assert club.client.delete(f"/notifications/devices/{device.id}").json() == {"deleted": False}
    assert devices(db_session, id=device.id)


# ---------------------------------------------------------------- preferences


def test_preferences_start_with_every_switch_on(club, member):
    assert club.client.get("/notifications/preferences").json() == {
        "remind": True,
        "accept": True,
        "points": True,
        "news": True,
        "updated_at": None,
    }


def test_patching_one_switch_keeps_the_others(club, member, frozen):
    response = club.client.patch("/notifications/preferences", json={"news": False, "points": False})
    assert response.status_code == 200
    body = response.json()
    assert (body["remind"], body["accept"], body["points"], body["news"]) == (True, True, False, False)
    assert body["updated_at"] == "2026-07-20T09:00:00Z"
    again = club.client.patch("/notifications/preferences", json={"points": True}).json()
    assert (again["points"], again["news"]) == (True, False)
    assert club.client.get("/notifications/preferences").json() == again


def test_a_first_save_of_another_switch_leaves_club_news_on(club, member, db_session):
    body = club.client.patch("/notifications/preferences", json={"remind": False}).json()
    assert (body["remind"], body["news"]) == (False, True)
    db_session.expire_all()
    [row] = db_session.scalars(select(MemberNotificationPrefs).where(MemberNotificationPrefs.member_id == member.id))
    assert (row.remind, row.accept, row.points, row.news) == (0, 1, 1, 1)


def test_members_without_a_saved_row_hear_club_news(db_session, push_on):
    never_saved, opted_out = make_member(db_session), make_member(db_session)
    for person in (never_saved, opted_out):
        add_device(db_session, person)
    set_prefs(db_session, opted_out, news=False)
    assert outbox.reachable(db_session, "news", {never_saved.id, opted_out.id}) == {never_saved.id}


@pytest.mark.parametrize("body", [{"sound": True}, {"news": "maybe"}, {"member_id": 3, "news": True}])
def test_a_bad_preferences_patch_is_a_422(club, member, body):
    assert club.client.patch("/notifications/preferences", json=body).status_code == 422


def test_an_empty_patch_changes_nothing(club, member):
    assert club.client.patch("/notifications/preferences", json={}).json()["updated_at"] is None


# ---------------------------------------------------------------- GET /notifications/me


def _sent(session, member, kind, *, key, sent_at, text=None, event_id=None, status=PushStatus.SENT):
    row = PushNotifications(
        member_id=member.id,
        kind=kind,
        dedupe_key=key,
        event_id=event_id,
        payload={
            "ar": (text or {}).get("ar", {"title": "عنوان", "body": "نص"}),
            "en": (text or {}).get("en", {"title": "Title", "body": "Text"}),
            "url": "gdgq://notifications",
            "thread_id": "news",
        },
        status=status,
        attempts=1,
        not_before=sent_at,
        expires_at=sent_at + timedelta(days=2),
        created_at=sent_at,
        sent_at=sent_at if status == PushStatus.SENT else None,
    )
    session.add(row)
    session.flush()
    return row


def test_the_feed_lists_what_was_sent_newest_first_in_the_asked_language(club, member, db_session, frozen):
    older = _sent(db_session, member, PushKind.ACCEPTED, key="a1", sent_at=frozen.now - timedelta(days=3))
    newer = _sent(db_session, member, PushKind.BROADCAST, key="b1", sent_at=frozen.now - timedelta(hours=1))
    _sent(db_session, member, PushKind.POINTS, key="p1", sent_at=frozen.now, status=PushStatus.PENDING)
    _sent(db_session, member, PushKind.BROADCAST, key="old", sent_at=frozen.now - timedelta(days=61))
    _sent(db_session, club.person(), PushKind.BROADCAST, key="other", sent_at=frozen.now)

    items = club.client.get("/notifications/me", params={"lang": "en"}).json()
    assert [item["id"] for item in items] == [newer.id, older.id]
    assert items[0] == {
        "id": newer.id,
        "kind": "broadcast",
        "title": "Title",
        "body": "Text",
        "url": "gdgq://notifications",
        "event_id": None,
        "created_at": "2026-07-20T08:00:00Z",
    }
    assert club.client.get("/notifications/me").json()[0]["title"] == "عنوان"
    assert len(club.client.get("/notifications/me", params={"limit": 1}).json()) == 1


def test_the_feed_lists_the_1_0_3_kinds(club, member, db_session, frozen):
    for n, kind in enumerate([PushKind.CERTIFICATE, PushKind.WEEKLY_SUMMARY, PushKind.EVENT_CANCELLED]):
        _sent(db_session, member, kind, key=f"k{n}", sent_at=frozen.now - timedelta(hours=n))
    kinds = [item["kind"] for item in club.client.get("/notifications/me").json()]
    assert kinds == ["certificate", "weekly_summary", "event_cancelled"]


@pytest.mark.parametrize("params", [{"lang": "fr"}, {"limit": 0}, {"limit": 101}])
def test_bad_feed_parameters_are_a_422(club, member, params):
    assert club.client.get("/notifications/me", params=params).status_code == 422


# ---------------------------------------------------------------- POST /notifications/broadcast


@pytest.fixture
def media_leader(club):
    media = club.department("Media")
    leader = club.join(club.person(), media, "leader")
    club.sign_in(leader)
    return leader


def broadcast_body(**overrides):
    body = {"title_ar": "إعلان", "body_ar": "لقاء النادي الليلة", "audience": {"type": "all"}}
    body.update(overrides)
    return body


def _reader(session, *, news=True, environment=None):
    from tests.push_support import make_member

    person = make_member(session, "Reader")
    set_prefs(session, person, news=news)
    if environment is None:
        add_device(session, person)
    else:
        add_device(session, person, environment=environment)
    return person


def test_a_plain_member_cannot_broadcast(club, member, push_on):
    response = club.client.post("/notifications/broadcast", json=broadcast_body())
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_medias_leaders_broadcast_to_members_with_club_news_on(
    club, media_leader, db_session, push_on, apns_mock, frozen
):
    from app.DB.schema import PushEnvironment

    readers = [_reader(db_session), _reader(db_session)]
    _reader(db_session, news=False)
    _reader(db_session, environment=PushEnvironment.DEVELOPMENT)
    db_session.commit()

    response = club.client.post("/notifications/broadcast", json=broadcast_body(url="gdgq://events/12"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipients"] == 2

    db_session.expire_all()
    broadcast = db_session.get(PushBroadcasts, body["broadcast_id"])
    assert broadcast is not None and broadcast.sent_by == media_leader.id and broadcast.recipient_count == 2
    rows = db_session.scalars(select(PushNotifications).where(PushNotifications.broadcast_id == broadcast.id)).all()
    assert sorted(r.member_id for r in rows) == sorted(r.id for r in readers)
    assert {r.dedupe_key for r in rows} == {f"broadcast:{broadcast.id}:{r.id}" for r in readers}
    # Sent right after the response, through BackgroundTasks.
    assert len(apns_mock.requests) == 2
    assert all(p["url"] == "gdgq://events/12" and p["kind"] == "broadcast" for p in apns_mock.payloads())
    assert {r.status for r in rows} == {PushStatus.SENT}


def test_a_dry_run_counts_and_sends_nothing(club, media_leader, db_session, apns_mock):
    _reader(db_session)
    db_session.commit()
    # Works with push off too: it only counts.
    response = club.client.post("/notifications/broadcast", json=broadcast_body(dry_run=True))
    assert response.json() == {"broadcast_id": None, "recipients": 1}
    assert db_session.scalars(select(PushBroadcasts)).all() == []
    assert apns_mock.requests == []


def test_an_event_broadcast_reaches_its_accepted_registrants_with_reminders_on(
    club, media_leader, db_session, push_on, apns_mock
):
    from tests.push_support import make_member

    event = make_event(db_session, start=datetime(2026, 7, 25, 16, 0))
    accepted, pending, muted = (make_member(db_session) for _ in range(3))
    for person in (accepted, pending, muted):
        add_device(db_session, person)
    register(db_session, event, accepted)
    register(db_session, event, pending, accepted=False)
    register(db_session, event, muted)
    set_prefs(db_session, muted, remind=False)
    _reader(db_session)  # news on, but not registered
    db_session.commit()

    body = broadcast_body(audience={"type": "event", "event_id": event.id}, dry_run=True)
    assert club.client.post("/notifications/broadcast", json=body).json()["recipients"] == 1
    body["dry_run"] = False
    response = club.client.post("/notifications/broadcast", json=body).json()
    db_session.expire_all()
    [row] = db_session.scalars(
        select(PushNotifications).where(PushNotifications.broadcast_id == response["broadcast_id"])
    ).all()
    assert (row.member_id, row.event_id, row.payload["switch"]) == (accepted.id, event.id, "remind")
    assert row.payload["thread_id"] == f"event-{event.id}"


def test_an_event_broadcast_for_a_missing_event_is_a_404(club, media_leader, push_on):
    body = broadcast_body(audience={"type": "event", "event_id": 987654})
    assert club.client.post("/notifications/broadcast", json=body).status_code == 404


def test_only_the_apps_links_are_allowed(club, media_leader, push_on):
    response = club.client.post("/notifications/broadcast", json=broadcast_body(url="https://evil.example"))
    assert response.status_code == 422
    assert response.json()["code"] == "url_not_allowed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title_ar": "ع" * 61},
        {"body_ar": "ن" * 181},
        {"title_ar": "   "},
        {"body_ar": "a\x07b"},
        {"title_en": "Only a title"},
        {"audience": {"type": "everyone"}},
        {"audience": {"type": "event"}},
        {"sender": "me"},
    ],
)
def test_a_bad_broadcast_is_a_422(club, media_leader, push_on, overrides):
    assert club.client.post("/notifications/broadcast", json=broadcast_body(**overrides)).status_code == 422


def test_three_broadcasts_a_riyadh_day_then_429(club, media_leader, db_session, push_on, apns_mock, frozen):
    for _ in range(3):
        assert club.client.post("/notifications/broadcast", json=broadcast_body()).status_code == 200
    response = club.client.post("/notifications/broadcast", json=broadcast_body())
    assert response.status_code == 429 and response.json()["code"] == "broadcast_limit"
    # A dry run still answers.
    assert club.client.post("/notifications/broadcast", json=broadcast_body(dry_run=True)).status_code == 200
    # The next Riyadh day starts at 21:00 UTC.
    frozen.set(datetime(2026, 7, 20, 21, 0))
    assert club.client.post("/notifications/broadcast", json=broadcast_body()).status_code == 200


def test_broadcasting_with_push_off_is_a_503(club, media_leader):
    response = club.client.post("/notifications/broadcast", json=broadcast_body())
    assert response.status_code == 503 and response.json()["code"] == "push_not_configured"


def test_a_broadcast_at_night_waits_for_8am(club, media_leader, db_session, push_on, apns_mock, frozen):
    _reader(db_session)
    db_session.commit()
    frozen.set(datetime(2026, 7, 20, 20, 0))  # 23:00 Riyadh
    broadcast_id = club.client.post("/notifications/broadcast", json=broadcast_body()).json()["broadcast_id"]
    db_session.expire_all()
    [row] = db_session.scalars(select(PushNotifications).where(PushNotifications.broadcast_id == broadcast_id)).all()
    assert row.not_before == datetime(2026, 7, 21, 5, 0) and row.status == PushStatus.PENDING
    assert apns_mock.requests == []


def test_enqueue_writes_nothing_while_push_is_off(db_session):
    from tests.push_support import make_member

    person = make_member(db_session)
    add_device(db_session, person)
    added = outbox.enqueue(
        db_session,
        PushKind.POINTS,
        [Push(member_id=person.id, dedupe_key="points:x")],
        text={"ar": {"title": "a", "body": "b"}, "en": {"title": "a", "body": "b"}},
        url="gdgq://points/history",
        thread_id="points",
        expires_at=datetime(2030, 1, 1),
    )
    assert added == 0
