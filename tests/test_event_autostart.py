"""GDG-24: the sweep moves an open event to active once it starts; closing stays manual.

The clock (``frozen``) is naive UTC 2026-07-20 09:00, 12:00 in Riyadh; event
times are Riyadh wall clock.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.DB.schema import EventHistory, EventHistoryAction, EventsStatus, FormType
from app.services import event_status
from app.services.pipeline_sweep import run_sweep
from tests.push_support import form_of, make_event

STARTED = datetime(2026, 7, 20, 11, 0)  # an hour ago in Riyadh
LATER = datetime(2026, 7, 20, 13, 0)  # 10:00 UTC, an hour from now


def status_changes(session, event) -> list[EventHistory]:
    return list(
        session.scalars(
            select(EventHistory).where(
                EventHistory.event_id == event.id, EventHistory.action == EventHistoryAction.STATUS_CHANGED
            )
        )
    )


def test_an_open_event_that_started_becomes_active(db_session, frozen):
    event = make_event(db_session, start=STARTED)

    result = run_sweep(db_session)

    assert event.status == EventsStatus.ACTIVE
    assert result.events_started == 1
    [change] = status_changes(db_session, event)
    assert change.actor_id is None
    assert change.details == {"status": ["open", "active"]}


def test_it_starts_on_the_riyadh_clock_not_utc(db_session, frozen):
    # 10:00 Riyadh was two hours ago; read as UTC it would still be an hour away.
    event = make_event(db_session, start=datetime(2026, 7, 20, 10, 0))
    upcoming = make_event(db_session, start=LATER, name="Later talk")

    run_sweep(db_session)

    assert event.status == EventsStatus.ACTIVE
    assert upcoming.status == EventsStatus.OPEN


@pytest.mark.parametrize("status", [EventsStatus.DRAFT, EventsStatus.ACTIVE, EventsStatus.CLOSED])
def test_other_statuses_are_left_alone(db_session, frozen, status):
    event = make_event(db_session, start=STARTED, status=status)

    result = run_sweep(db_session)

    assert event.status == status
    assert result.events_started == 0
    assert status_changes(db_session, event) == []


def test_a_second_sweep_changes_nothing(db_session, frozen):
    event = make_event(db_session, start=STARTED)
    run_sweep(db_session)

    result = run_sweep(db_session)

    assert result.events_started == 0
    assert len(status_changes(db_session, event)) == 1


def test_starting_unpublishes_its_google_form(db_session, frozen, monkeypatch):
    calls = []
    monkeypatch.setattr(
        event_status, "set_form_publish_state", lambda form_id, is_published: calls.append((form_id, is_published))
    )
    event = make_event(db_session, start=STARTED, form_type=FormType.GOOGLE)
    form_of(db_session, event).google_form_id = "gform-1"

    run_sweep(db_session)

    assert calls == [("gform-1", False)]
