"""Regression coverage for the email job boundary.

`send_direct_email_job` and `send_blast_job` used to swallow their top-level
exception (rollback + log, no re-raise). `track()`'s contract (see
job_tracker.py) is that an exception escaping the job body marks the whole
job FAILED and re-raises; a per-recipient exception inside
`tracker.recipient(...)` is instead recorded and swallowed so the loop can
continue. Anything that raises *outside* a `tracker.recipient()` scope -
before the loop starts, or (for blast's SES path) the single gateway call
that isn't wrapped per-recipient at all - has to hit the outer boundary.

`send_blast_job`'s SES branch calls `call_blast_api` directly, with no
`tracker.recipient()` around it, so a gateway failure there used to be
swallowed with the job left recorded SUCCEEDED (0/0). These tests pin the
fix: both job functions must re-raise and end up FAILED.
"""

import asyncio

import pytest

from app.DB import email_jobs as job_queries
from app.DB.schema import (
    EmailJobsStatus,
    EmailJobsType,
    EmailLogsFromAddress,
    EmailProvider,
    Events,
    EventsLocationType,
    EventsStatus,
    Forms,
    FormsSubmissionsFormType,
    Submissions,
    SubmissionsSubmissionType,
)
from app.exceptions import BadGateway
from app.routers.email_models import BlastGuaranteedRecipient, BlastSendRequest, DirectEmailRequest, SimpleEvent
from app.services import email_jobs
from app.services.email_recipients import resolve_ad_hoc_recipients
from app.services.job_tracker import EMAIL_JOB_QUERIES, job_boundary


def _boom(*args, **kwargs):
    raise BadGateway(detail="gateway is down")


def _boom_plain(*args, **kwargs):
    """A non-HTTPException failure, to exercise the generic `except Exception`
    branch rather than the `except HTTPException` branch that already re-raises."""
    raise RuntimeError("something unrelated to HTTP broke")


def test_send_blast_job_ses_path_marks_job_failed_when_gateway_errors(db_session, seed_refs, monkeypatch):
    """The SES branch calls the gateway outside any tracker.recipient() scope."""
    monkeypatch.setattr(email_jobs, "call_blast_api", _boom)

    job = job_queries.create_job(db_session, EmailJobsType.BLAST, seed_refs.ahmed.id, total=1)
    request = BlastSendRequest(
        subject="Hi", html_content="<p>hi</p>", count=1, order_by="activity", provider=EmailProvider.SES
    )
    recipients = [{"name": "Sara", "email": seed_refs.sara.email}]

    with pytest.raises(BadGateway):
        asyncio.run(email_jobs.send_blast_job(recipients, [], 1, seed_refs.ahmed.id, request, job.id))

    # the job body runs on its own session (see job_tracker.track / db_session);
    # this fixture's session cached the pre-run row, so it needs a refresh.
    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.FAILED
    assert finished.succeeded == 0
    assert finished.failed == 0  # nothing ever entered tracker.recipient() to record a per-recipient failure


def test_send_direct_email_job_marks_job_failed_when_body_raises_before_the_loop(db_session, seed_refs, monkeypatch):
    """Anything that raises before the recipient loop starts must still fail the job.

    Uses a plain (non-HTTPException) failure: `send_direct_email_job` has a
    separate `except HTTPException: raise` branch that already re-raises
    correctly on its own, so an HTTPException-based failure wouldn't exercise
    the `except Exception` branch this test targets.
    """
    monkeypatch.setattr(email_jobs.logger, "info", _boom_plain)

    job = job_queries.create_job(db_session, EmailJobsType.DIRECT_EMAIL, seed_refs.ahmed.id, total=1)
    request = DirectEmailRequest(
        subject="Hi",
        html_content="<p>hi</p>",
        recipients=[BlastGuaranteedRecipient(email=seed_refs.sara.email, name="Sara")],
    )
    recipients = [{"name": "Sara", "email": seed_refs.sara.email, "member_id": seed_refs.sara.id}]

    with pytest.raises(RuntimeError):
        asyncio.run(
            email_jobs.send_direct_email_job(recipients, seed_refs.ahmed.id, request.provider, None, request, job.id)
        )

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.FAILED
    assert finished.succeeded == 0


def test_job_boundary_marks_succeeded_when_nothing_fails(db_session, seed_refs):
    job = job_queries.create_job(db_session, EmailJobsType.BLAST, seed_refs.ahmed.id, total=1)

    with job_boundary(job.id, EMAIL_JOB_QUERIES) as (tracker, _session):
        with tracker.recipient("someone@example.com"):
            pass

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.SUCCEEDED
    assert finished.succeeded == 1
    assert finished.failed == 0


def test_job_boundary_marks_partial_when_some_recipients_fail(db_session, seed_refs):
    """A per-recipient failure inside tracker.recipient() is recorded, not raised - the
    loop continues and the job ends PARTIAL, distinct from the job_boundary-level
    failure the tests above cover."""
    job = job_queries.create_job(db_session, EmailJobsType.BLAST, seed_refs.ahmed.id, total=2)

    with job_boundary(job.id, EMAIL_JOB_QUERIES) as (tracker, _session):
        with tracker.recipient("ok@example.com"):
            pass
        with tracker.recipient("bad@example.com"):
            raise RuntimeError("that address bounced")

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.PARTIAL
    assert finished.succeeded == 1
    assert finished.failed == 1


def test_resolve_ad_hoc_recipients_dedups_by_lowercased_email(db_session, seed_refs):
    """A member picked by member_id and again by a manually-typed, differently-cased
    address for the same email collapses to one entry."""
    items = [
        BlastGuaranteedRecipient(member_id=seed_refs.ahmed.id),
        BlastGuaranteedRecipient(email=seed_refs.ahmed.email.upper(), name="Duplicate Ahmed"),
    ]

    recipients, resolved_ids = resolve_ad_hoc_recipients(db_session, items, include_member_id=True)

    assert len(recipients) == 1
    assert resolved_ids == {seed_refs.ahmed.id}


def test_resolve_ad_hoc_recipients_include_member_id_flag(db_session, seed_refs):
    items = [BlastGuaranteedRecipient(member_id=seed_refs.ahmed.id)]

    with_id, _ = resolve_ad_hoc_recipients(db_session, items, include_member_id=True)
    assert with_id[0]["member_id"] == seed_refs.ahmed.id

    without_id, _ = resolve_ad_hoc_recipients(db_session, items, include_member_id=False)
    assert "member_id" not in without_id[0]


def test_resolve_ad_hoc_recipients_skips_unresolvable_member_id(db_session, seed_refs):
    items = [BlastGuaranteedRecipient(member_id=999_999), BlastGuaranteedRecipient(email=seed_refs.sara.email)]

    recipients, resolved_ids = resolve_ad_hoc_recipients(db_session, items, include_member_id=True)

    assert len(recipients) == 1
    assert recipients[0]["email"] == seed_refs.sara.email
    assert resolved_ids == set()  # 999_999 never resolved to a real member


# ====================== acceptance ======================


@pytest.fixture
def invited_submission(db_session, seed_refs):
    """One accepted submission, already claimed as invited by the route.

    `send_acceptance_blasts` marks its recipients invited *before* queueing the
    job, so that a second click cannot queue the same blast again while the
    first is still sending. The job below is what has to give that claim back
    when the send never lands.
    """
    event = Events(
        name="acceptance event",
        location_type=EventsLocationType.ONLINE,
        location="space",
        start_datetime="2026-06-29 00:00:00",
        end_datetime="2026-06-29 00:00:00",
        status=EventsStatus.OPEN,
    )
    db_session.add(event)
    db_session.flush()

    form = Forms(event_id=event.id, form_type=FormsSubmissionsFormType.NONE)
    db_session.add(form)
    db_session.flush()

    submission = Submissions(
        form_id=form.id,
        member_id=seed_refs.sara.id,
        is_accepted=1,
        is_invited=1,
        submission_type=SubmissionsSubmissionType.NONE,
    )
    db_session.add(submission)
    db_session.flush()
    db_session.commit()
    return event, submission


def _acceptance_args(event, submission, seed_refs, job_id):
    return (
        [{"name": seed_refs.sara.name, "email": seed_refs.sara.email}],
        [submission.id],
        "You're in",
        "<p>You're in.</p>",
        SimpleEvent(name=event.name, date="29 June 2026", official=False),
        event.id,
        EmailLogsFromAddress.GDG_QASSIM,
        seed_refs.ahmed.id,
        job_id,
    )


def test_send_acceptance_job_releases_the_invited_claim_when_the_gateway_fails(
    db_session, seed_refs, invited_submission, monkeypatch
):
    """A failed send must not leave the recipients marked invited.

    `get_accepted_not_invited_by_event` is the only thing that decides who an
    acceptance blast goes to. A submission stuck at is_invited=1 after a send
    that never happened is invisible to it forever: the member never gets the
    email and no retry will ever pick them up again.
    """
    event, submission = invited_submission
    monkeypatch.setattr(email_jobs, "call_acceptance_api", _boom)

    job = job_queries.create_job(db_session, EmailJobsType.ACCEPTANCE, seed_refs.ahmed.id, total=1, event_id=event.id)

    with pytest.raises(BadGateway):
        asyncio.run(email_jobs.send_acceptance_job(*_acceptance_args(event, submission, seed_refs, job.id)))

    db_session.expire_all()
    assert db_session.get(Submissions, submission.id).is_invited == 0

    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.FAILED
    assert finished.succeeded == 0


def test_send_acceptance_job_keeps_the_invited_claim_when_the_send_succeeds(
    db_session, seed_refs, invited_submission, outbound
):
    """The mirror of the test above: a successful send leaves the claim standing,
    which is what stops the same acceptance email going out twice."""
    event, submission = invited_submission

    job = job_queries.create_job(db_session, EmailJobsType.ACCEPTANCE, seed_refs.ahmed.id, total=1, event_id=event.id)

    asyncio.run(email_jobs.send_acceptance_job(*_acceptance_args(event, submission, seed_refs, job.id)))

    db_session.expire_all()
    assert db_session.get(Submissions, submission.id).is_invited == 1

    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == EmailJobsStatus.SUCCEEDED
    assert finished.succeeded == 1

    blast = outbound.one("/blasts")
    assert blast.params.get_list("emails") == [seed_refs.sara.email]
    assert blast.params["from_address"] == EmailLogsFromAddress.GDG_QASSIM.value
