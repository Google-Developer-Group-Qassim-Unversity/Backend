"""Tests for the Event Lifecycle & Registration data model.

Covers:
- Pure derivation rules in ``app.services.event_lifecycle``
- Status & registration endpoints (``PUT /events/{id}/status``, ``PUT /events/{id}/registration``)
- Paginated filtering (``status``, ``phase``, ``registration_status``, ``exclude_custom``)
- Submission gating (``POST``/``DELETE /submissions/{form_id}``) and ``GET /submissions/me``
- Attendance gating for non-published events (400 ``event_not_published``)
- Points & member history exclusion of cancelled events
- Registration-closing push skipped when registration is closed
- Alembic migration ``e5f6a7b8c9d0`` backfill & round-trip downgrade
"""

from datetime import datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from app.DB import club_structure as club_queries
from app.DB import logs as log_queries
from app.DB.schema import (
    DepartmentsLogs,
    EventHistoryAction,
    Events,
    EventsLocationType,
    EventsStatus,
    FormRegistrationStatus,
    Forms,
    FormType,
    Logs,
    MemberProfiles,
    Members,
    MembersGender,
    MembersLogs,
    PushKind,
    PushNotifications,
    PushStatus,
)
from app.services import event_lifecycle
from app.services.pipeline_sweep import run_sweep
from tests.factories import make_create_event_payload, make_event
from tests.push_support import add_device, deliver, make_event as make_push_event, make_member
from tests.utils import assert_2xx, assert_bad_request, assert_conflict, semester_id_on


# ---------------------------------------------------------------- pure helper unit tests


def _stub_event(
    *,
    status: EventsStatus = EventsStatus.PUBLISHED,
    start: datetime = datetime(2026, 7, 25, 16, 0),
    end: datetime = datetime(2026, 7, 25, 18, 0),
) -> Events:
    return Events(
        id=1,
        name="Workshop",
        location_type=EventsLocationType.ON_SITE,
        location="Hall 1",
        start_datetime=start,
        end_datetime=end,
        status=status,
        semester_id="00000000-0000-0000-0000-000000000000",
    )


def _stub_form(
    *,
    form_type: FormType = FormType.REGISTRATION,
    registration_status: FormRegistrationStatus = FormRegistrationStatus.OPEN,
    google_form_id: str | None = None,
) -> Forms:
    return Forms(
        id=10, event_id=1, form_type=form_type, registration_status=registration_status, google_form_id=google_form_id
    )


def test_compute_phase_boundaries():
    start = datetime(2026, 7, 25, 16, 0)
    end = datetime(2026, 7, 25, 18, 0)
    assert event_lifecycle.compute_phase(start, end, datetime(2026, 7, 25, 15, 59, 59)) == "upcoming"
    assert event_lifecycle.compute_phase(start, end, start) == "live"
    assert event_lifecycle.compute_phase(start, end, datetime(2026, 7, 25, 17, 0)) == "live"
    assert event_lifecycle.compute_phase(start, end, end) == "live"
    assert event_lifecycle.compute_phase(start, end, datetime(2026, 7, 25, 18, 0, 1)) == "ended"


def test_effective_registration_status_and_is_open_matrix():
    now = datetime(2026, 7, 20, 12, 0)
    event = _stub_event()

    # No form or form_type == none -> none
    assert event_lifecycle.effective_registration_status(event, None, now) == "none"
    assert event_lifecycle.is_registration_open(event, None, now) is False
    none_form = _stub_form(form_type=FormType.NONE, registration_status=FormRegistrationStatus.OPEN)
    assert event_lifecycle.effective_registration_status(event, none_form, now) == "none"
    assert event_lifecycle.is_registration_open(event, none_form, now) is False

    # Published + open -> open
    open_form = _stub_form()
    assert event_lifecycle.effective_registration_status(event, open_form, now) == "open"
    assert event_lifecycle.is_registration_open(event, open_form, now) is True

    # Published + not_open -> not_open
    not_open_form = _stub_form(registration_status=FormRegistrationStatus.NOT_OPEN)
    assert event_lifecycle.effective_registration_status(event, not_open_form, now) == "not_open"
    assert event_lifecycle.is_registration_open(event, not_open_form, now) is False

    # Published + closed -> closed
    closed_pub_form = _stub_form(registration_status=FormRegistrationStatus.CLOSED)
    assert event_lifecycle.effective_registration_status(event, closed_pub_form, now) == "closed"
    assert event_lifecycle.is_registration_open(event, closed_pub_form, now) is False

    # Past event end -> closed
    ended_event = _stub_event(start=now - timedelta(hours=3), end=now - timedelta(minutes=1))
    assert event_lifecycle.effective_registration_status(ended_event, open_form, now) == "closed"
    assert event_lifecycle.is_registration_open(ended_event, open_form, now) is False

    # Cancelled event -> closed even if form says open or not_open
    cancelled_event = _stub_event(status=EventsStatus.CANCELLED)
    assert event_lifecycle.effective_registration_status(cancelled_event, open_form, now) == "closed"
    assert event_lifecycle.is_registration_open(cancelled_event, open_form, now) is False

    # Draft event -> always not_open (even if its dates are in the past or form was closed)
    draft_event = _stub_event(status=EventsStatus.DRAFT)
    assert event_lifecycle.effective_registration_status(draft_event, open_form, now) == "not_open"
    assert event_lifecycle.is_registration_open(draft_event, open_form, now) is False
    closed_form = _stub_form(registration_status=FormRegistrationStatus.CLOSED)
    assert event_lifecycle.effective_registration_status(draft_event, closed_form, now) == "not_open"
    past_draft = _stub_event(status=EventsStatus.DRAFT, start=now - timedelta(hours=3), end=now - timedelta(minutes=1))
    assert event_lifecycle.effective_registration_status(past_draft, open_form, now) == "not_open"


def test_should_google_form_be_published():
    published = _stub_event(status=EventsStatus.PUBLISHED)
    draft = _stub_event(status=EventsStatus.DRAFT)
    cancelled = _stub_event(status=EventsStatus.CANCELLED)
    gform_open = _stub_form(
        form_type=FormType.GOOGLE, registration_status=FormRegistrationStatus.OPEN, google_form_id="gf-1"
    )
    gform_closed = _stub_form(
        form_type=FormType.GOOGLE, registration_status=FormRegistrationStatus.CLOSED, google_form_id="gf-1"
    )
    gform_unattached = _stub_form(
        form_type=FormType.GOOGLE, registration_status=FormRegistrationStatus.OPEN, google_form_id=None
    )
    reg_open = _stub_form(form_type=FormType.REGISTRATION, registration_status=FormRegistrationStatus.OPEN)

    assert event_lifecycle.should_google_form_be_published(published, gform_open) is True
    assert event_lifecycle.should_google_form_be_published(draft, gform_open) is False
    assert event_lifecycle.should_google_form_be_published(cancelled, gform_open) is False
    assert event_lifecycle.should_google_form_be_published(published, gform_closed) is False
    assert event_lifecycle.should_google_form_be_published(published, gform_unattached) is False
    assert event_lifecycle.should_google_form_be_published(published, reg_open) is False


# ---------------------------------------------------------------- status & registration routes


def test_status_and_registration_lifecycle_endpoints(admin_client: TestClient, frozen, monkeypatch):
    from app.routers import events as events_router

    frozen.set(datetime(2026, 6, 20, 9, 0))  # 12:00 Riyadh
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        events_router,
        "set_form_publish_state",
        lambda google_form_id, is_published: calls.append((google_form_id, is_published)),
    )

    # Create draft event with google form -> registration starts not_open
    created = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(start_datetime="2026-06-29T16:00:00", end_datetime="2026-06-29T18:00:00", status="draft"),
            form_type="google",
        ),
    )
    assert_2xx(created)
    event = created.json()
    assert event["status"] == "draft"
    assert event["published_at"] is None
    assert event["cancelled_at"] is None
    assert event["phase"] == "upcoming"

    details = admin_client.get(f"/events/{event['id']}/details").json()
    assert details["form"] is not None
    assert details["form"]["registration_status"] == "not_open"
    assert details["form"]["effective_registration_status"] == "not_open"
    assert details["form"]["is_registration_open"] is False

    # Attach google_form_id
    form_id = details["form"]["id"]
    admin_client.put(
        f"/forms/{form_id}", json={"event_id": event["id"], "form_type": "google", "google_form_id": "gf-life"}
    )

    # Publish without open_registration -> registration stays not_open, Google Form not yet published
    pub = admin_client.put(f"/events/{event['id']}/status", json={"status": "published"})
    assert_2xx(pub)
    assert pub.json()["published_at"] == "2026-06-20T09:00:00"
    assert calls == []

    # Open registration via PUT /events/{id}/registration -> publishes Google Form & records history
    reg = admin_client.put(f"/events/{event['id']}/registration", json={"registration_status": "open"})
    assert_2xx(reg)
    reg_body = reg.json()
    assert reg_body["registration_status"] == "open"
    assert reg_body["effective_registration_status"] == "open"
    assert reg_body["is_registration_open"] is True
    assert calls == [("gf-life", True)]

    # Cancel event -> sets cancelled_at, auto-closes registration, unpublishes Google Form
    frozen.advance(hours=2)
    cancelled = admin_client.put(f"/events/{event['id']}/status", json={"status": "cancelled"})
    assert_2xx(cancelled)
    assert cancelled.json()["cancelled_at"] == "2026-06-20T11:00:00"
    assert cancelled.json()["published_at"] == "2026-06-20T09:00:00"
    assert calls == [("gf-life", True), ("gf-life", False)]

    form_after_cancel = admin_client.get(f"/events/{event['id']}/form").json()
    assert form_after_cancel["registration_status"] == "closed"
    assert form_after_cancel["effective_registration_status"] == "closed"
    assert form_after_cancel["is_registration_open"] is False

    # Re-publish clears cancelled_at and keeps original published_at
    repub = admin_client.put(f"/events/{event['id']}/status", json={"status": "published"})
    assert_2xx(repub)
    assert repub.json()["cancelled_at"] is None
    assert repub.json()["published_at"] == "2026-06-20T09:00:00"

    # History contains registration_changed
    actions = [item["action"] for item in admin_client.get(f"/events/{event['id']}/history").json()["items"]]
    assert EventHistoryAction.REGISTRATION_CHANGED.value in actions


def test_registration_endpoint_rejects_form_type_none(admin_client: TestClient):
    none_event = admin_client.post("/events/", json=make_create_event_payload(form_type="none")).json()
    refused = admin_client.put(f"/events/{none_event['id']}/registration", json={"registration_status": "open"})
    assert_conflict(refused)
    assert refused.json()["code"] == "registration_not_supported"


# ---------------------------------------------------------------- paginated filtering


def test_paginated_filters_phase_registration_status_and_exclude_custom(admin_client: TestClient, frozen):
    frozen.set(datetime(2026, 6, 25, 9, 0))  # 12:00 Riyadh

    upcoming_open = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(
                name="upcoming-open",
                start_datetime="2026-06-28T10:00:00",
                end_datetime="2026-06-28T12:00:00",
                status="published",
            ),
            form_type="registration",
        ),
    ).json()
    live_not_open = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(
                name="live-not-open",
                start_datetime="2026-06-25T10:00:00",
                end_datetime="2026-06-25T14:00:00",
                status="published",
            ),
            form_type="registration",
            registration_status="not_open",
        ),
    ).json()
    ended_closed = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(
                name="ended-closed",
                start_datetime="2026-06-20T10:00:00",
                end_datetime="2026-06-20T12:00:00",
                status="published",
            ),
            form_type="registration",
            registration_status="open",
        ),
    ).json()
    walkin_no_form = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(
                name="walkin-event",
                location_type="online",
                start_datetime="2026-06-28T10:00:00",
                end_datetime="2026-06-28T12:00:00",
                status="published",
            ),
            form_type="none",
        ),
    ).json()

    # phase filters
    res_upcoming = admin_client.get("/events/paginated", params={"phase": "upcoming"}).json()
    assert {e["id"] for e in res_upcoming["items"]} == {upcoming_open["id"], walkin_no_form["id"]}
    assert res_upcoming["total"] == 2

    res_live = admin_client.get("/events/paginated", params={"phase": "live"}).json()
    assert [e["id"] for e in res_live["items"]] == [live_not_open["id"]]
    assert res_live["total"] == 1

    res_ended = admin_client.get("/events/paginated", params={"phase": "ended"}).json()
    assert [e["id"] for e in res_ended["items"]] == [ended_closed["id"]]
    assert res_ended["total"] == 1

    # exclude_custom stays accepted as a backward-compatible parameter (all events are real now)
    res_no_custom = admin_client.get("/events/paginated", params={"phase": "upcoming", "exclude_custom": True}).json()
    assert {e["id"] for e in res_no_custom["items"]} == {upcoming_open["id"], walkin_no_form["id"]}
    assert res_no_custom["total"] == 2

    # registration_status filters match effective status
    assert [
        e["id"] for e in admin_client.get("/events/paginated", params={"registration_status": "open"}).json()["items"]
    ] == [upcoming_open["id"]]
    assert [
        e["id"]
        for e in admin_client.get("/events/paginated", params={"registration_status": "not_open"}).json()["items"]
    ] == [live_not_open["id"]]
    assert [
        e["id"] for e in admin_client.get("/events/paginated", params={"registration_status": "closed"}).json()["items"]
    ] == [ended_closed["id"]]
    assert [
        e["id"] for e in admin_client.get("/events/paginated", params={"registration_status": "none"}).json()["items"]
    ] == [walkin_no_form["id"]]


# ---------------------------------------------------------------- submissions gating & GET /submissions/me


def test_post_submission_enforces_effective_registration_open_and_get_my_submissions(
    admin_client: TestClient, clerk_client: TestClient, db_session, frozen
):
    frozen.set(datetime(2026, 6, 20, 9, 0))  # 12:00 Riyadh
    member = Members(
        name="Test Member",
        email="test@example.com",
        phone_number="0501234567",
        uni_id="123456789",
        gender=MembersGender.MALE,
    )
    db_session.add(member)
    db_session.commit()

    # Registration not_open -> blocked with 409 registration_closed
    event = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            event=make_event(
                start_datetime="2026-06-29T16:00:00", end_datetime="2026-06-29T18:00:00", status="published"
            ),
            form_type="registration",
            registration_status="not_open",
        ),
    ).json()
    form_id = admin_client.get(f"/events/{event['id']}/form").json()["id"]

    blocked = clerk_client.post(f"/submissions/{form_id}", params={"submission_type": "none"})
    assert_conflict(blocked)
    assert blocked.json()["code"] == "registration_closed"

    # Open registration manually -> allowed
    assert_2xx(admin_client.put(f"/events/{event['id']}/registration", json={"registration_status": "open"}))
    created = clerk_client.post(f"/submissions/{form_id}", params={"submission_type": "none"})
    assert_2xx(created)

    # GET /submissions/me returns it with event_id
    mine = clerk_client.get("/submissions/me")
    assert_2xx(mine)
    rows = mine.json()
    assert len(rows) == 1
    assert rows[0]["form_id"] == form_id
    assert rows[0]["event_id"] == event["id"]
    assert rows[0]["submission_type"] == "none"
    assert rows[0]["is_accepted"] is False
    assert rows[0]["is_invited"] is False

    # Cancel -> excluded from GET /submissions/me
    assert_2xx(clerk_client.delete(f"/submissions/{form_id}"))
    assert clerk_client.get("/submissions/me").json() == []


# ---------------------------------------------------------------- attendance & points gating


@pytest.mark.parametrize("bad_status", ["draft", "cancelled"])
def test_attendance_rejects_non_published_events(
    admin_client: TestClient, clerk_client: TestClient, db_session, seed_refs, monkeypatch, bad_status
):
    from app.DB.schema import Actions, ActionsActionType
    import jwt

    monkeypatch.setattr(
        log_queries,
        "get_attendable_logs",
        lambda session, event_id: session.scalar(
            select(Logs)
            .join(Actions, Actions.id == Logs.action_id)
            .where(Logs.event_id == event_id, Actions.action_type == ActionsActionType.MEMBER)
        ),
    )

    event = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            seed_refs=seed_refs,
            event=make_event(
                start_datetime="2026-06-29T10:00:00", end_datetime="2026-06-29T12:00:00", status=bad_status
            ),
        ),
    ).json()
    event_id = event["id"]

    # Manual
    res_manual = admin_client.post(
        f"/attendance/{event_id}/manual", json={"member_ids": [seed_refs.ahmed.id], "day": 1}
    )
    assert_bad_request(res_manual)
    assert res_manual.json()["code"] == "event_not_published"

    # Backfill
    res_backfill = admin_client.post(
        f"/attendance/{event_id}/backfill",
        json={
            "day": 1,
            "members": [
                {
                    "name": seed_refs.ahmed.name,
                    "email": seed_refs.ahmed.email,
                    "phone_number": seed_refs.ahmed.phone_number,
                    "uni_id": seed_refs.ahmed.uni_id,
                    "gender": "Male",
                }
            ],
        },
    )
    assert_bad_request(res_backfill)
    assert res_backfill.json()["code"] == "event_not_published"

    # Scan
    profile = MemberProfiles(member_id=seed_refs.ahmed.id, uuid="test-scan-uuid-1234")
    db_session.add(profile)
    db_session.commit()
    res_scan = admin_client.post(f"/attendance/{event_id}/scan", json={"uuid": profile.uuid, "day": 1})
    assert_bad_request(res_scan)
    assert res_scan.json()["code"] == "event_not_published"

    # Member QR check-in
    member = Members(
        name="Test Member",
        email="test@example.com",
        phone_number="0501234567",
        uni_id="123456789",
        gender=MembersGender.MALE,
    )
    db_session.add(member)
    db_session.commit()
    token = jwt.encode(
        {"eventId": event_id, "exp": datetime(2030, 1, 1), "requireAttendanceTimeWindow": False},
        "test_jwt_secret_for_testing_only",
        algorithm="HS256",
    )
    res_qr = clerk_client.post(f"/attendance/{event_id}", params={"token": token})
    assert_bad_request(res_qr)
    assert res_qr.json()["code"] == "event_not_published"


def test_cancelled_event_excluded_from_points_member_events_and_club_structure(
    admin_client: TestClient, db_session, seed_refs
):
    event = Events(
        name="Cancelled Workshop",
        location_type=EventsLocationType.ON_SITE,
        location="Hall 1",
        start_datetime=datetime(2026, 7, 1, 10, 0),
        end_datetime=datetime(2026, 7, 1, 12, 0),
        status=EventsStatus.CANCELLED,
        semester_id=semester_id_on(db_session, "2026-07-01"),
    )
    db_session.add(event)
    db_session.flush()
    dept_log = Logs(action_id=seed_refs.dept_action.id, event_id=event.id)
    mem_log = Logs(action_id=seed_refs.member_action.id, event_id=event.id)
    db_session.add_all([dept_log, mem_log])
    db_session.flush()
    db_session.add(DepartmentsLogs(department_id=seed_refs.dept_business.id, log_id=dept_log.id))
    db_session.add(MembersLogs(member_id=seed_refs.ahmed.id, log_id=mem_log.id, date=datetime(2026, 7, 1, 11, 0)))
    db_session.commit()

    assert (
        admin_client.get(f"/points/departments/{seed_refs.dept_business.id}").json()["department"]["total_points"] == 0
    )
    assert admin_client.get(f"/points/members/{seed_refs.ahmed.id}").json()["member"]["total_points"] == 0
    assert club_queries.department_has_points(db_session, event.semester_id, seed_refs.dept_business.id) is False


# ---------------------------------------------------------------- push registration_closing skipped when closed


def test_registration_closing_push_skips_when_registration_closed(db_session, push_on, apns_mock, frozen):
    # Frozen clock is 2026-07-20 09:00 UTC = 12:00 Riyadh.
    # Event starts tomorrow at 12:00 Riyadh (24h from now).
    event = make_push_event(
        db_session, start=datetime(2026, 7, 21, 12, 0), registration_status=FormRegistrationStatus.OPEN
    )
    member = make_member(db_session)
    add_device(db_session, member)
    db_session.commit()

    result = run_sweep(db_session, frozen.now)
    db_session.commit()
    assert result.push_registration_closing == 1

    # Close registration before delivery -> skipped as event_closed
    form = db_session.scalars(select(Forms).where(Forms.event_id == event.id)).one()
    form.registration_status = FormRegistrationStatus.CLOSED
    db_session.commit()
    deliver()

    row = db_session.scalars(
        select(PushNotifications).where(PushNotifications.kind == PushKind.REGISTRATION_CLOSING)
    ).one()
    assert (row.status, row.last_error) == (PushStatus.SKIPPED, "event_closed")
    assert apns_mock.requests == []


# ---------------------------------------------------------------- migration backfill & round-trip


def test_migration_e5f6a7b8c9d0_backfills_and_round_trips(database_url, monkeypatch):
    from sqlalchemy.engine import make_url

    from app.DB.main import _build_connect_args
    from tests.conftest import _drill_server_root

    if "server_url" not in _drill_server_root:
        pytest.skip("migration backfill test needs a testcontainer MySQL server of its own")

    scratch_db = "test_lifecycle_backfill_drill"
    root_server_url = _drill_server_root["server_url"]
    drill_url = make_url(root_server_url).set(database=scratch_db).render_as_string(hide_password=False)

    server = create_engine(root_server_url, connect_args=_build_connect_args(root_server_url))
    drill_engine = None
    try:
        with server.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{scratch_db}`")
            conn.exec_driver_sql(f"CREATE DATABASE `{scratch_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")

        monkeypatch.setenv("DATABASE_URL", drill_url)
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", drill_url.replace("%", "%%"))

        # Upgrade only up to the revision immediately before our migration.
        command.upgrade(cfg, "d4e5f6a7b8c9")

        drill_engine = create_engine(drill_url, connect_args=_build_connect_args(drill_url))
        with drill_engine.begin() as conn:
            sem_id = conn.execute(text("SELECT id FROM semesters LIMIT 1")).scalar_one()
            rows = [
                ("draft-reg", "draft", "registration"),
                ("open-reg", "open", "registration"),
                ("open-none", "open", "none"),
                ("active-google", "active", "google"),
                ("closed-reg", "closed", "registration"),
            ]
            for name, status, form_type in rows:
                res = conn.execute(
                    text(
                        "INSERT INTO events (name, location_type, location, start_datetime, end_datetime, status, semester_id) "
                        "VALUES (:name, 'online', 'Online', '2027-01-10 10:00:00', '2027-01-10 12:00:00', :status, :sem)"
                    ),
                    {"name": name, "status": status, "sem": sem_id},
                )
                ev_id = int(res.lastrowid)
                conn.execute(
                    text("INSERT INTO forms (event_id, form_type) VALUES (:ev_id, :ft)"),
                    {"ev_id": ev_id, "ft": form_type},
                )

        # Upgrade to e5f6a7b8c9d0 and verify backfill + open_events view.
        command.upgrade(cfg, "e5f6a7b8c9d0")
        with drill_engine.connect() as conn:
            migrated = {
                row.name: (row.status, row.published_at is not None, row.registration_status)
                for row in conn.execute(
                    text(
                        "SELECT e.name, e.status, e.published_at, f.registration_status "
                        "FROM events e JOIN forms f ON f.event_id = e.id"
                    )
                )
            }
            assert migrated == {
                "draft-reg": ("draft", False, "not_open"),
                "open-reg": ("published", True, "open"),
                "open-none": ("published", True, "not_open"),
                "active-google": ("published", True, "closed"),
                "closed-reg": ("published", True, "closed"),
            }
            view_names = set(conn.execute(text("SELECT name FROM open_events")).scalars().all())
            assert view_names == {"open-reg", "open-none", "active-google", "closed-reg"}

        # Downgrade back to d4e5f6a7b8c9, verify restoration, then re-upgrade and check parity.
        command.downgrade(cfg, "d4e5f6a7b8c9")
        with drill_engine.connect() as conn:
            restored = dict(conn.execute(text("SELECT name, status FROM events")).tuples().all())
            assert restored["draft-reg"] == "draft"
            assert restored["open-reg"] == "open"
            assert restored["active-google"] == "active"

        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        if drill_engine is not None:
            drill_engine.dispose()
        with server.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{scratch_db}`")
        server.dispose()


# ---------------------------------------------------------------------------
# Meeting URL visibility policy (public | accepted | hidden)
# ---------------------------------------------------------------------------


def test_visible_meeting_url_policy_matrix():
    url = "https://meet.google.com/abc-defg-hij"
    assert event_lifecycle.visible_meeting_url(None, "public", is_staff=True, is_accepted=True) is None
    assert event_lifecycle.visible_meeting_url(url, "public") == url
    assert event_lifecycle.visible_meeting_url(url, "accepted") is None
    assert event_lifecycle.visible_meeting_url(url, "accepted", is_accepted=True) == url
    assert event_lifecycle.visible_meeting_url(url, "accepted", is_staff=True) == url
    assert event_lifecycle.visible_meeting_url(url, "hidden") is None
    assert event_lifecycle.visible_meeting_url(url, "hidden", is_accepted=True) is None
    assert event_lifecycle.visible_meeting_url(url, "hidden", is_staff=True) == url


def test_meeting_url_visibility_enforced_across_event_and_submission_routes(
    admin_client: TestClient, db_session, seed_refs, frozen
):
    from contextlib import contextmanager
    from app.DB.schema import Submissions
    from app.helpers import authenticated_guard, get_current_member, optional_clerk_guard
    from app.main import app
    from app.services.permissions.access import Access
    from app.services.permissions.dependencies import get_access
    from tests.conftest import FAKE_CLERK_CREDENTIALS

    @contextmanager
    def as_guest():
        saved = dict(app.dependency_overrides)
        try:
            app.dependency_overrides[optional_clerk_guard] = lambda: None
            app.dependency_overrides[get_access] = lambda: Access()
            app.dependency_overrides.pop(get_current_member, None)
            yield admin_client
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(saved)

    @contextmanager
    def as_member():
        saved = dict(app.dependency_overrides)
        try:
            app.dependency_overrides[authenticated_guard] = lambda: FAKE_CLERK_CREDENTIALS
            app.dependency_overrides[optional_clerk_guard] = lambda: FAKE_CLERK_CREDENTIALS
            app.dependency_overrides[get_access] = lambda: Access()
            app.dependency_overrides.pop(get_current_member, None)
            yield admin_client
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(saved)

    frozen.set(datetime(2026, 6, 20, 9, 0))  # 12:00 Riyadh

    ev = admin_client.post(
        "/events/",
        json=make_create_event_payload(
            seed_refs=seed_refs,
            event=make_event(
                name="Private Bootcamp",
                status="published",
                location_type="online",
                location="Google Meet",
                start_datetime="2026-06-28T10:00:00",
                end_datetime="2026-06-28T12:00:00",
            ),
            form_type="registration",
            registration_status="open",
        ),
    ).json()
    ev_id = ev["id"]
    form_id = db_session.scalar(select(Forms.id).where(Forms.event_id == ev_id))
    assert form_id is not None

    # Smart default on registration event is 'accepted'.
    ev_admin = admin_client.get(f"/events/{ev_id}").json()
    assert ev_admin["meeting_url_visibility"] == "accepted"

    # Set meeting URL while keeping 'accepted' visibility.
    set_resp = admin_client.put(
        f"/events/{ev_id}/meeting-url", json={"meeting_url": "abc-defg-hij", "meeting_url_visibility": "accepted"}
    )
    assert_2xx(set_resp)
    assert set_resp.json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert set_resp.json()["meeting_url_visibility"] == "accepted"

    # Staff sees it on GET /events/{id}; guest and public list do NOT.
    assert admin_client.get(f"/events/{ev_id}").json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    with as_guest() as guest:
        assert guest.get(f"/events/{ev_id}").json()["meeting_url"] is None
        public_list = {e["id"]: e for e in guest.get("/events/").json()}
        assert public_list[ev_id]["meeting_url"] is None
        open_list = {e["id"]: e for e in guest.get("/events/open").json()}
        assert open_list[ev_id]["meeting_url"] is None

    # Regular member registers (not yet accepted) -> still cannot see meeting URL.
    m = Members(
        name="Student",
        email="student222@qu.edu.sa",
        phone_number="0501234567",
        uni_id="123456789",
        gender=MembersGender.MALE,
    )
    db_session.add(m)
    db_session.commit()
    with as_member() as mc:
        assert_2xx(mc.post(f"/submissions/{form_id}?submission_type=none"))
        assert mc.get(f"/events/{ev_id}").json()["meeting_url"] is None
        sub_check = mc.get(f"/submissions/{form_id}").json()
        assert sub_check["submission_status"] is True
        assert sub_check["is_accepted"] is False
        assert sub_check["meeting_url"] is None

    # Accept the member -> now sees meeting URL on both GET /events/{id} and submissions endpoints.
    sub_id = db_session.scalar(
        select(Submissions.id).where(Submissions.form_id == form_id, Submissions.member_id == m.id)
    )
    assert_2xx(admin_client.put("/submissions/accept", json=[{"submission_id": sub_id, "is_accepted": True}]))
    with as_member() as mc:
        assert mc.get(f"/events/{ev_id}").json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"
        sub_check_accepted = mc.get(f"/submissions/{form_id}").json()
        assert sub_check_accepted["is_accepted"] is True
        assert sub_check_accepted["meeting_url"] == "https://meet.google.com/abc-defg-hij"
        my_subs = mc.get("/submissions/me").json()
        assert my_subs[0]["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    # Flip visibility to 'hidden' without re-sending URL -> URL kept, hidden from accepted member, visible to staff.
    hide_resp = admin_client.put(f"/events/{ev_id}/meeting-url", json={"meeting_url_visibility": "hidden"})
    assert_2xx(hide_resp)
    assert hide_resp.json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"
    assert hide_resp.json()["meeting_url_visibility"] == "hidden"
    with as_member() as mc:
        assert mc.get(f"/events/{ev_id}").json()["meeting_url"] is None
        assert mc.get(f"/submissions/{form_id}").json()["meeting_url"] is None
    assert admin_client.get(f"/events/{ev_id}").json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    # Flip visibility to 'public' -> guest and public list now see it.
    pub_resp = admin_client.put(f"/events/{ev_id}/meeting-url", json={"meeting_url_visibility": "public"})
    assert_2xx(pub_resp)
    with as_guest() as guest:
        assert guest.get(f"/events/{ev_id}").json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    # Switch back to 'accepted', then toggle form_type to 'none' -> fail-safe switches to 'hidden' (never leaks).
    assert_2xx(admin_client.put(f"/events/{ev_id}/meeting-url", json={"meeting_url_visibility": "accepted"}))
    assert_2xx(admin_client.put(f"/forms/{form_id}", json={"event_id": ev_id, "form_type": "none"}))
    after_none = admin_client.get(f"/events/{ev_id}").json()
    assert after_none["meeting_url_visibility"] == "hidden"
    # Setting 'accepted' on an open-attendance event is rejected.
    bad_vis = admin_client.put(f"/events/{ev_id}/meeting-url", json={"meeting_url_visibility": "accepted"})
    assert_bad_request(bad_vis)
