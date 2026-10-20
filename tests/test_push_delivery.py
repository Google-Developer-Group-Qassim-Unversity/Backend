"""Delivering the outbox against a fake APNs (``httpx.MockTransport``): outcomes, retries, re-checks and claiming."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.DB.ids import new_id
from app.DB.schema import (
    MemberDevices,
    Members,
    MembersGender,
    PushEnvironment,
    PushKind,
    PushLocale,
    PushNotifications,
    PushStatus,
)
from app.services.push import delivery, messages, outbox
from app.services.push.outbox import Push
from tests.push_support import add_device, deliver, make_member, set_prefs

TEXT = messages.broadcast("عنوان", "نص", "Title", "Text")


def queue(
    session, member, *, key="k1", kind=PushKind.BROADCAST, expires=None, switch="news", **kwargs
) -> PushNotifications:
    added = outbox.enqueue(
        session,
        kind,
        [Push(member_id=member.id, dedupe_key=key)],
        text=TEXT,
        url="gdgq://notifications",
        thread_id="news",
        switch=switch,
        expires_at=expires or datetime(2026, 7, 22),
        **kwargs,
    )
    assert added == 1
    session.commit()
    return session.scalars(select(PushNotifications).where(PushNotifications.dedupe_key == key)).one()


def reload(session, row):
    session.expire_all()
    return session.get(PushNotifications, row.id)


@pytest.fixture
def reader(db_session, frozen):
    """A member with club news on. The clock is frozen at 12:00 Riyadh, outside quiet hours."""
    member = make_member(db_session)
    set_prefs(db_session, member, news=True)
    return member


def test_each_phone_gets_it_in_its_own_language(db_session, reader, push_on, apns_mock, frozen):
    ar = add_device(db_session, reader, locale=PushLocale.AR)
    en = add_device(db_session, reader, locale=PushLocale.EN)
    row = queue(db_session, reader)

    result = deliver()

    assert (result.claimed, result.sent) == (1, 1)
    by_token = dict(zip(apns_mock.tokens(), apns_mock.payloads()))
    assert by_token[ar.token]["aps"]["alert"] == {"title": "عنوان", "body": "نص"}
    assert by_token[en.token]["aps"]["alert"] == {"title": "Title", "body": "Text"}
    assert by_token[en.token]["body"] == {"url": "gdgq://notifications", "kind": "broadcast", "id": row.id}
    request = apns_mock.requests[0]
    assert request.headers["apns-priority"] == "5"  # broadcasts and new events are not urgent
    assert request.headers["apns-collapse-id"] == delivery.collapse_id("k1")
    assert request.headers["apns-expiration"] == str(
        int((datetime(2026, 7, 22) - datetime(1970, 1, 1)).total_seconds())
    )
    row = reload(db_session, row)
    assert (row.status, row.sent_at, row.attempts) == (PushStatus.SENT, frozen.now, 1)


def test_development_phones_never_get_production_pushes(db_session, reader, push_on, apns_mock):
    add_device(db_session, reader, environment=PushEnvironment.DEVELOPMENT)
    production = add_device(db_session, reader)
    queue(db_session, reader)
    deliver()
    assert apns_mock.tokens() == [production.token]


def test_410_deletes_a_phone_only_if_it_did_not_register_again_after_apples_timestamp(
    db_session, reader, push_on, apns_mock
):
    gone = add_device(db_session, reader, seen=datetime(2026, 7, 1))
    back = add_device(db_session, reader, seen=datetime(2026, 7, 15))
    died = int((datetime(2026, 7, 10) - datetime(1970, 1, 1)).total_seconds() * 1000)
    for device in (gone, back):
        apns_mock.answer(device.token, (410, {"reason": "Unregistered", "timestamp": died}))
    row = queue(db_session, reader)

    gone_id, back_id = gone.id, back.id
    result = deliver()

    db_session.expire_all()
    assert db_session.get(MemberDevices, gone_id) is None
    assert db_session.get(MemberDevices, back_id) is not None
    assert result.devices_removed == 1
    assert (reload(db_session, row).status, reload(db_session, row).last_error) == (PushStatus.SKIPPED, "no_device")


def test_a_bad_device_token_is_forgotten(db_session, reader, push_on, apns_mock):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (400, {"reason": "BadDeviceToken"}))
    device_id = device.id
    queue(db_session, reader)
    deliver()
    db_session.expire_all()
    assert db_session.get(MemberDevices, device_id) is None


def test_one_phone_taking_it_is_enough(db_session, reader, push_on, apns_mock):
    dead, alive = add_device(db_session, reader), add_device(db_session, reader)
    apns_mock.answer(dead.token, (400, {"reason": "BadDeviceToken"}))
    row = queue(db_session, reader)
    deliver()
    assert reload(db_session, row).status == PushStatus.SENT
    db_session.expire_all()
    assert [d.id for d in db_session.scalars(select(MemberDevices).where(MemberDevices.member_id == reader.id))] == [
        alive.id
    ]


def test_a_bad_key_keeps_the_push_queued_and_logs_one_error(db_session, reader, push_on, apns_mock, frozen, caplog):
    for _ in range(2):
        device = add_device(db_session, reader)
        apns_mock.answer(device.token, (403, {"reason": "InvalidProviderToken"}))
    row = queue(db_session, reader)

    with caplog.at_level(logging.INFO):
        deliver()

    row = reload(db_session, row)
    assert (row.status, row.attempts, row.not_before) == (PushStatus.PENDING, 0, frozen.now + timedelta(minutes=5))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "InvalidProviderToken" in errors[0].getMessage()


def test_too_many_provider_token_updates_keeps_the_token(db_session, reader, push_on, apns_mock, frozen):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (429, {"reason": "TooManyProviderTokenUpdates"}), (200, None))
    row = queue(db_session, reader)
    deliver()
    assert reload(db_session, row).not_before == frozen.now + timedelta(minutes=5)
    frozen.advance(minutes=5)
    deliver()
    assert reload(db_session, row).status == PushStatus.SENT
    first, second = (r.headers["authorization"] for r in apns_mock.requests)
    assert first == second


def test_too_many_requests_retries_after_a_minute(db_session, reader, push_on, apns_mock, frozen):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (429, {"reason": "TooManyRequests"}))
    row = queue(db_session, reader)
    deliver()
    row = reload(db_session, row)
    assert (row.status, row.attempts, row.not_before) == (PushStatus.PENDING, 0, frozen.now + timedelta(minutes=1))


def test_server_errors_back_off_then_fail_after_five_attempts(db_session, reader, push_on, apns_mock, frozen):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (503, {"reason": "ServiceUnavailable"}))
    row = queue(db_session, reader)

    deliver()
    row = reload(db_session, row)
    assert (row.status, row.attempts, row.not_before) == (PushStatus.PENDING, 1, frozen.now + timedelta(minutes=2))
    for attempt in range(2, 6):
        frozen.set(row.not_before)
        deliver()
        row = reload(db_session, row)
        assert row.attempts == attempt
    assert (row.status, row.last_error) == (PushStatus.FAILED, "ServiceUnavailable")


def test_a_timeout_is_retried(db_session, reader, push_on, apns_mock, frozen):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, httpx.ReadTimeout("slow"))
    row = queue(db_session, reader)
    deliver()
    row = reload(db_session, row)
    assert (row.status, row.last_error) == (PushStatus.PENDING, "ReadTimeout")


def test_one_request_failing_unexpectedly_never_loses_the_batchs_other_answers(
    db_session, reader, push_on, apns_mock, frozen, caplog
):
    # Before: the exception escaped asyncio.gather, nothing was recorded, and the rows that did
    # reach Apple stayed leased and went out a second time five minutes later.
    other = make_member(db_session)
    set_prefs(db_session, other, news=True)
    broken, fine = add_device(db_session, reader), add_device(db_session, other)
    apns_mock.answer(broken.token, RuntimeError(f"connection state for {broken.token}"))
    apns_mock.answer(fine.token, (200, None))
    failing, sent = queue(db_session, reader, key="k-broken"), queue(db_session, other, key="k-fine")

    with caplog.at_level(logging.DEBUG):
        result = deliver()

    assert (result.sent, result.retried) == (1, 1)
    assert reload(db_session, sent).status == PushStatus.SENT
    failing = reload(db_session, failing)
    assert (failing.status, failing.attempts, failing.last_error) == (PushStatus.PENDING, 1, "RuntimeError")
    assert failing.not_before == frozen.now + timedelta(minutes=2)
    assert broken.token not in caplog.text


def test_a_device_never_shows_its_token_in_a_repr():
    device = delivery.Device("dev-1", "ab" * 32, "ar", datetime(2026, 7, 1))
    assert "ab" * 32 not in repr(device)
    job = delivery.Job("row-1", 1, PushKind.BROADCAST, "k", {}, datetime(2026, 7, 2), 1, [device])
    assert "ab" * 32 not in repr(job)


def test_a_payload_apple_refuses_fails_at_once(db_session, reader, push_on, apns_mock):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (400, {"reason": "BadExpirationDate"}))
    row = queue(db_session, reader)
    deliver()
    row = reload(db_session, row)
    assert (row.status, row.last_error) == (PushStatus.FAILED, "BadExpirationDate")


def test_an_expired_push_is_skipped_unsent(db_session, reader, push_on, apns_mock, frozen):
    add_device(db_session, reader)
    row = queue(db_session, reader, expires=frozen.now + timedelta(minutes=1))
    frozen.advance(minutes=2)
    deliver()
    assert (reload(db_session, row).status, reload(db_session, row).last_error) == (PushStatus.SKIPPED, "expired")
    assert apns_mock.requests == []


def test_turning_the_switch_off_stops_what_is_still_waiting(db_session, push_on, apns_mock, frozen):
    member = make_member(db_session)
    add_device(db_session, member)
    prefs = set_prefs(db_session, member, news=True)
    row = queue(db_session, member)
    prefs.news = 0
    db_session.commit()
    deliver()
    assert reload(db_session, row).last_error == "switch_off"
    assert apns_mock.requests == []


def test_a_member_whose_phones_are_gone_is_skipped(db_session, reader, push_on, apns_mock):
    device = add_device(db_session, reader)
    row = queue(db_session, reader)
    db_session.delete(device)
    db_session.commit()
    deliver()
    assert reload(db_session, row).last_error == "no_device"


def test_nothing_is_sent_while_push_is_off(db_session, reader, apns_mock):
    assert deliver().claimed == 0
    assert apns_mock.requests == []


def test_a_claimed_row_is_leased_until_the_lease_runs_out(db_session, reader, push_on, frozen):
    add_device(db_session, reader)
    row = queue(db_session, reader)

    jobs, _ = delivery.claim(frozen.now)
    assert [job.id for job in jobs] == [row.id]
    # A worker that died mid-send: nobody else takes the row during the lease ...
    assert delivery.claim(frozen.now + timedelta(minutes=4))[0] == []
    # ... and it comes back when the lease ends.
    jobs, _ = delivery.claim(frozen.now + timedelta(minutes=5))
    assert [(job.id, job.attempts) for job in jobs] == [(row.id, 2)]


def test_logs_never_carry_a_device_token(db_session, reader, push_on, apns_mock, caplog):
    device = add_device(db_session, reader)
    apns_mock.answer(device.token, (410, {"reason": "Unregistered"}))
    queue(db_session, reader)
    with caplog.at_level(logging.DEBUG):
        deliver()
    assert device.token not in caplog.text
    assert "/3/device/[token]" in caplog.text  # httpx's request line, rewritten


# ---------------------------------------------------------------- two workers, real connections


@pytest.fixture
def committed_outbox(engine):
    """Rows committed for real, so two connections can compete for them; removed afterwards."""
    suffix = uuid4().hex[:10]
    now = datetime(2026, 7, 20, 9, 0)
    with Session(engine) as session:
        member = Members(
            name=f"Claim {suffix}", email=f"claim-{suffix}@example.com", uni_id=suffix, gender=MembersGender.MALE
        )
        session.add(member)
        session.flush()
        ids = []
        for n in range(6):
            row = PushNotifications(
                id=new_id(),
                member_id=member.id,
                kind=PushKind.BROADCAST,
                dedupe_key=f"claim:{suffix}:{n}",
                payload={},
                status=PushStatus.PENDING,
                not_before=now - timedelta(minutes=n),
                expires_at=now + timedelta(days=1),
                created_at=now,
            )
            session.add(row)
            ids.append(row.id)
        session.commit()
        refs = SimpleNamespace(member_id=member.id, ids=set(ids), now=now)
    try:
        yield refs
    finally:
        with Session(engine) as session:
            session.execute(delete(PushNotifications).where(PushNotifications.member_id == refs.member_id))
            session.execute(delete(Members).where(Members.id == refs.member_id))
            session.commit()


def test_two_workers_never_claim_the_same_row(engine, committed_outbox):
    barrier = Barrier(2)

    def take(_):
        with Session(engine) as session:
            session.execute(text("SET SESSION innodb_lock_wait_timeout = 5"))
            barrier.wait(timeout=10)
            rows = delivery.lock_due(session, committed_outbox.now, 3)
            ids = {row.id for row in rows if row.member_id == committed_outbox.member_id}
            barrier.wait(timeout=10)  # both hold their locks at the same time
            session.rollback()
            return ids

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(take, range(2))
    assert first and second
    assert not first & second
    assert first | second <= committed_outbox.ids


# ---------------------------------------------------------------- the test-push command


def test_the_test_command_sends_to_each_phone_and_prints_only_apples_answer(
    db_session, reader, push_on, apns_mock, capsys
):
    from app.services.push import cli

    ok, dead = add_device(db_session, reader), add_device(db_session, reader, locale=PushLocale.EN)
    apns_mock.answer(dead.token, (410, {"reason": "Unregistered"}))
    db_session.commit()

    assert cli.main(["test", "--member-id", str(reader.id)]) == 1
    out = capsys.readouterr().out
    assert f"device {ok.id}: 200 OK" in out and f"device {dead.id}: 410 Unregistered" in out
    assert ok.token not in out and dead.token not in out
    assert {p["aps"]["alert"]["title"] for p in apns_mock.payloads()} == {"إشعار تجريبي", "Test notification"}


def test_the_test_command_says_when_there_is_nothing_to_send_to(db_session, reader, push_on, capsys):
    from app.services.push import cli

    db_session.commit()
    assert cli.main(["test", "--member-id", str(reader.id)]) == 1
    assert "no phone registered in the production environment" in capsys.readouterr().out


def test_the_test_command_refuses_while_push_is_off(capsys):
    from app.services.push import cli

    assert cli.main(["test", "--member-id", "1"]) == 2
    assert "push is off" in capsys.readouterr().out
