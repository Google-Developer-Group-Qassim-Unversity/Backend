"""Shared set-up for the push notification tests: push switched on with a throwaway key, a fake APNs, a frozen clock.

No test talks to Apple: ``apns_mock`` puts an ``httpx.MockTransport`` client in
``app.services.push.apns``, and the key is generated per test run.
"""

import asyncio
import itertools
import json
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import reload_settings
from app.DB.schema import (
    Actions,
    ActionsActionType,
    Events,
    EventsLocationType,
    EventsStatus,
    Forms,
    FormType,
    Logs,
    MemberDevices,
    MemberNotificationPrefs,
    Members,
    MembersGender,
    PushEnvironment,
    PushLocale,
    PushPlatform,
    Submissions,
    SubmissionsSubmissionType,
)
from app.services import event_pipeline_clock
from app.services.push import apns, delivery
from tests.utils import semester_id_on

KEY_ID = "TESTKEY001"


def make_p8() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


TEST_KEY = make_p8()


@pytest.fixture
def push_on(monkeypatch):
    """Push configured: a key id and a freshly generated P-256 key, production environment."""
    monkeypatch.setenv("APNS_KEY_ID", KEY_ID)
    monkeypatch.setenv("APNS_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("APNS_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("APNS_ENVIRONMENT", raising=False)
    reload_settings()
    apns.reset_provider_token()
    yield
    apns.reset_provider_token()


class FakeApns:
    """Answers like APNs. ``answer(token, (status, json), ...)``: one answer per call, the last one repeats."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, list] = {}

    def answer(self, token: str, *answers) -> None:
        self.answers[token] = list(answers)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.url.path.rsplit("/", 1)[-1]
        queue = self.answers.get(token)
        answer = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else (200, None)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    def tokens(self) -> list[str]:
        return [request.url.path.rsplit("/", 1)[-1] for request in self.requests]

    def payloads(self) -> list[dict]:
        return [json.loads(request.content) for request in self.requests]


@pytest.fixture
def apns_mock():
    fake = FakeApns()
    apns.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    yield fake
    apns.set_client(None)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def set(self, when: datetime) -> None:
        self.now = when

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


@pytest.fixture
def frozen(monkeypatch):
    """Naive UTC 2026-07-20 09:00 (12:00 in Riyadh), for everything that reads the pipeline's clock."""
    clock = Clock(datetime(2026, 7, 20, 9, 0))
    monkeypatch.setattr(event_pipeline_clock, "now", lambda: clock.now)
    return clock


def deliver(batch_size: int = delivery.BATCH_SIZE) -> delivery.DeliveryResult:
    return asyncio.run(delivery.deliver_pending(batch_size))


# ---------------------------------------------------------------- data

_numbers = itertools.count(1)


def make_member(session, name: str = "Push Member") -> Members:
    n = next(_numbers)
    member = Members(
        name=f"{name} {n}",
        email=f"push{n}@example.com",
        uni_id=f"55{n:07d}",
        clerk_user_id=f"clerk_push_{n}",
        gender=MembersGender.MALE,
    )
    session.add(member)
    session.flush()
    return member


def make_token(n: int | None = None) -> str:
    return f"{n if n is not None else next(_numbers):064x}"


def add_device(
    session,
    member: Members,
    *,
    token: str | None = None,
    environment: PushEnvironment = PushEnvironment.PRODUCTION,
    locale: PushLocale = PushLocale.AR,
    seen: datetime = datetime(2026, 7, 1, 9, 0),
) -> MemberDevices:
    device = MemberDevices(
        member_id=member.id,
        token=token or make_token(),
        platform=PushPlatform.IOS,
        environment=environment,
        app_version="1.0.2",
        app_build="2",
        locale=locale,
        created_at=seen,
        last_seen_at=seen,
    )
    session.add(device)
    session.flush()
    return device


def set_prefs(session, member: Members, **switches) -> MemberNotificationPrefs:
    values = {"remind": 1, "accept": 1, "points": 1, "news": 1, **{k: int(v) for k, v in switches.items()}}
    prefs = MemberNotificationPrefs(member_id=member.id, updated_at=datetime(2026, 7, 1), **values)
    session.add(prefs)
    session.flush()
    return prefs


def make_event(
    session,
    *,
    start: datetime,
    end: datetime | None = None,
    status: EventsStatus = EventsStatus.OPEN,
    location_type: EventsLocationType = EventsLocationType.ON_SITE,
    form_type: FormType = FormType.REGISTRATION,
    name: str = "Intro to ML",
    location: str = "Hall 60",
    created_at: datetime = datetime(2026, 7, 1, 9, 0),
) -> Events:
    """Times are Riyadh wall clock, as the events table stores them."""
    end = end or start + timedelta(hours=2)
    event = Events(
        name=name,
        location_type=location_type,
        location=location,
        start_datetime=start,
        end_datetime=end,
        status=status,
        semester_id=semester_id_on(session, end.date().isoformat()),
        created_at=created_at,
    )
    session.add(event)
    session.flush()
    session.add(Forms(event_id=event.id, form_type=form_type))
    session.flush()
    return event


def form_of(session, event: Events) -> Forms:
    from sqlalchemy import select

    return session.scalars(select(Forms).where(Forms.event_id == event.id)).one()


def register(session, event: Events, member: Members, *, accepted: bool = True) -> Submissions:
    submission = Submissions(
        form_id=form_of(session, event).id,
        member_id=member.id,
        is_accepted=int(accepted),
        is_invited=0,
        submission_type=SubmissionsSubmissionType.REGISTRATION,
        submitted_at=datetime(2026, 7, 2, 10, 0),
    )
    session.add(submission)
    session.flush()
    return submission


def attendance_log(session, event: Events, points: int = 5) -> Logs:
    action = Actions(
        action_name=f"push test attendance {next(_numbers)}",
        points=points,
        action_type=ActionsActionType.MEMBER,
        ar_action_name="حضور",
    )
    session.add(action)
    session.flush()
    log = Logs(action_id=action.id, event_id=event.id)
    session.add(log)
    session.flush()
    return log
