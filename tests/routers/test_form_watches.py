"""Watch renewal, and the credential health check that would have caught the outage.

Both of these exist because of the same September 2026 incident: the club
refresh token expired (the OAuth client is in Testing publishing status, where
Google caps refresh tokens at seven days), every Google Forms sync started
failing, and the only visible symptom was a Starlette
`RuntimeError: Caught handled exception, but response already started.` on the
webhook path - with Sentry over quota, so nothing reported it at all.

Watches have the identical seven-day clock and no renewal existed, which is
the quieter half of the same problem: an expired watch stops notifying with no
error anywhere.
"""

import pytest
from google.auth.exceptions import RefreshError, TransportError

from app.DB import forms as form_queries
from app.DB.schema import Events, EventsLocationType, EventsStatus, Forms, FormType
from app.exceptions import BadGateway, GoogleFormAuthExpired
from app.services.form_watches import RecordedFormWatches, renew_form_watches
from tests.utils import assert_2xx


def make_linked_form(db_session, name: str, google_form_id: str, watch_id: str | None):
    event = Events(
        name=name,
        location_type=EventsLocationType.ONLINE,
        location="space",
        start_datetime="2026-06-29 00:00:00",
        end_datetime="2026-06-29 00:00:00",
        status=EventsStatus.OPEN,
    )
    db_session.add(event)
    db_session.flush()
    form = Forms(event_id=event.id, form_type=FormType.GOOGLE, google_form_id=google_form_id, google_watch_id=watch_id)
    db_session.add(form)
    db_session.flush()
    db_session.commit()
    return form


@pytest.fixture
def watched_forms(db_session):
    return [
        make_linked_form(db_session, "watched one", "form-1", "watch-1"),
        make_linked_form(db_session, "watched two", "form-2", "watch-2"),
    ]


# ====================== the ordinary sweep ======================


def test_renews_every_registered_watch(db_session, watched_forms):
    watches = RecordedFormWatches()

    summary = renew_form_watches(db_session, watches)

    assert summary["renewed"] == 2
    assert summary["recreated"] == 0
    assert summary["failed"] == 0
    assert summary["aborted_reason"] is None
    assert watches.renewed == [("form-1", "watch-1"), ("form-2", "watch-2")]


def test_skips_forms_with_no_watch_registered(db_session, watched_forms):
    """A form that was never attached to Google has nothing to renew."""
    make_linked_form(db_session, "no watch", "form-3", None)
    make_linked_form(db_session, "not google", None, None)
    watches = RecordedFormWatches()

    summary = renew_form_watches(db_session, watches)

    assert summary["renewed"] == 2
    assert [form_id for form_id, _ in watches.renewed] == ["form-1", "form-2"]


# ====================== the expired-watch path ======================


def test_expired_watch_is_recreated_and_the_new_id_persisted(db_session, watched_forms):
    """Google answers NOT_FOUND once the seven days are up; renewing is no longer possible."""
    watches = RecordedFormWatches(expired={"watch-1"})

    summary = renew_form_watches(db_session, watches)

    assert summary["recreated"] == 1
    assert summary["renewed"] == 1
    assert watches.created == ["form-1"]

    db_session.expire_all()
    refreshed = form_queries.get_form_by_google_form_id(db_session, "form-1")
    assert refreshed.google_watch_id == "new-watch-1"
    assert refreshed.google_watch_id != "watch-1"


def test_recreated_watch_id_is_committed_not_left_dangling(db_session, watched_forms):
    """A watch live at Google but unsaved here would be recreated again next run, leaking the old one."""
    watches = RecordedFormWatches(expired={"watch-1", "watch-2"})

    renew_form_watches(db_session, watches)

    db_session.rollback()
    ids = {f.google_form_id: f.google_watch_id for f in form_queries.get_forms(db_session) if f.google_form_id}
    assert ids["form-1"] == "new-watch-1"
    assert ids["form-2"] == "new-watch-2"


# ====================== failure policy ======================


def test_dead_credential_aborts_the_sweep_instead_of_failing_every_form(db_session, watched_forms):
    """One club-wide token: form 2 would fail identically, so there is no point asking."""
    watches = RecordedFormWatches(fail_renew_with=GoogleFormAuthExpired("form-1"))

    summary = renew_form_watches(db_session, watches)

    assert summary["aborted_reason"] is not None
    assert summary["renewed"] == 0
    assert summary["failed"] == 0
    assert len(summary["results"]) == 1
    assert summary["results"][0]["outcome"] == "aborted"


def test_one_forms_failure_does_not_stop_the_others(db_session, watched_forms):
    """Unlike a dead credential, a per-form error says nothing about the next form."""

    class FlakyWatches(RecordedFormWatches):
        def renew(self, google_form_id: str, watch_id: str) -> None:
            if google_form_id == "form-1":
                raise BadGateway(detail="Google Forms API returned error: 500")
            super().renew(google_form_id, watch_id)

    summary = renew_form_watches(db_session, FlakyWatches())

    assert summary["failed"] == 1
    assert summary["renewed"] == 1
    assert summary["aborted_reason"] is None
    failures = [r for r in summary["results"] if r["outcome"] == "failed"]
    assert "BadGateway" in failures[0]["error"]


def test_sweep_over_no_watched_forms_is_a_no_op(db_session):
    summary = renew_form_watches(db_session, RecordedFormWatches())

    assert summary == {"renewed": 0, "recreated": 0, "failed": 0, "aborted_reason": None, "results": []}


# ====================== the endpoint ======================


def test_renew_endpoint_requires_admin(client, watched_forms):
    assert client.post("/forms/watches/renew").status_code in (401, 403)


def test_renew_endpoint_reports_the_sweep(admin_client, watched_forms, monkeypatch):
    watches = RecordedFormWatches(expired={"watch-2"})
    monkeypatch.setattr("app.services.form_watches.get_form_watches", lambda: watches)

    response = admin_client.post("/forms/watches/renew")

    assert_2xx(response)
    body = response.json()
    assert body["renewed"] == 1
    assert body["recreated"] == 1
    assert body["failed"] == 0
    assert body["aborted_reason"] is None


# ====================== /health/google ======================


@pytest.fixture(autouse=True)
def _reset_credential_cache():
    """The success TTL is process-wide state; a stale entry would mask the next test."""
    from app.services import google_health

    google_health._health = google_health._CredentialHealth()
    yield


def test_health_google_reports_a_working_token(client, monkeypatch):
    monkeypatch.setattr("app.services.google_health.get_google_credentials", lambda: object())

    body = client.get("/health/google").json()

    assert body["valid"] is True
    assert body["reason"] is None
    assert body["checked_at"]


def test_health_google_names_an_expired_or_revoked_token(client, monkeypatch):
    """This is the exact failure that went unnoticed for seven hours on 2026-09-20."""

    def dead():
        raise RefreshError("('invalid_grant: Token has been expired or revoked.', {...})")

    monkeypatch.setattr("app.services.google_health.get_google_credentials", dead)

    body = client.get("/health/google").json()

    assert body["valid"] is False
    assert body["reason"] == "invalid_grant"
    assert "expired or revoked" in body["detail"]


def test_health_google_distinguishes_unreachable_from_invalid(client, monkeypatch):
    """Google being down says nothing about whether the token is good."""

    def unreachable():
        raise TransportError("connection reset")

    monkeypatch.setattr("app.services.google_health.get_google_credentials", unreachable)

    body = client.get("/health/google").json()

    assert body["valid"] is False
    assert body["reason"] == "unreachable"


def test_health_google_never_caches_a_failure(client, monkeypatch):
    """A cached false would be harmless; a cached true after revocation would not be."""
    calls = []

    def dead():
        calls.append(1)
        raise RefreshError("invalid_grant")

    monkeypatch.setattr("app.services.google_health.get_google_credentials", dead)

    client.get("/health/google")
    client.get("/health/google")

    assert len(calls) == 2


def test_health_google_caches_success_so_polling_is_cheap(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.google_health.get_google_credentials", lambda: calls.append(1))

    first = client.get("/health/google").json()
    second = client.get("/health/google").json()

    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
