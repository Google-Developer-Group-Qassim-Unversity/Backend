"""The Google Forms sync, driven through its seam.

Before `app/services/form_responses.py` existed, the Google client was built
inline in the router and nothing could stand in for it, so the only thing these
tests could reach was `extract_email_answer`. The matching, the update and the
job accounting - where the bugs actually are - went unexercised.

`RecordedFormResponses` is the second adapter, so the tests below drive the
whole path: a webhook arrives, the sync runs, submissions change, and the job
row says what happened.
"""

import httplib2
import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from app.DB import form_sync_jobs as job_queries
from app.DB.schema import Events, EventsLocationType, EventsStatus, Forms, FormType, FormSyncJobsStatus
from app.DB.schema import Members, MembersGender, Submissions, SubmissionsSubmissionType
from app.exceptions import BadGateway, GoogleFormAuthExpired, GoogleFormNotLinked, NotFound, ServiceUnavailable
from app.services.form_responses import FormAccess, RecordedFormResponses, _reraise_mapped, get_form_responses
from app.services.form_sync import extract_email_answer, sync_form_submissions, sync_manual_form_submissions
from tests.utils import assert_2xx

GOOGLE_FORM_ID = "google-form-abc"


# ====================== extract_email_answer ======================

# Real captured response payload (form 156, prod) - the email lives under
# questionId "128490c7" here, but under a completely different ID on other
# forms, and that same ID ("196b7896") holds a multiple-choice answer on
# this form instead of an email. Question IDs and titles are not stable
# across forms, so extract_email_answer must find the email by shape, not
# by a fixed ID or title.
REAL_FORM_156_RESPONSE_ANSWERS = {
    "196b7896": {"questionId": "196b7896", "textAnswers": {"answers": [{"value": "Option 1"}]}},
    "32cf98a8": {"questionId": "32cf98a8", "textAnswers": {"answers": [{"value": "طلاب"}]}},
    "2d0d7dad": {"questionId": "2d0d7dad", "textAnswers": {"answers": [{"value": "asdf"}]}},
    "36c5263a": {"questionId": "36c5263a", "textAnswers": {"answers": [{"value": "Option 1"}]}},
    "3b0efcf2": {"questionId": "3b0efcf2", "textAnswers": {"answers": [{"value": "Abdlelah Albrrak"}]}},
    "128490c7": {"questionId": "128490c7", "textAnswers": {"answers": [{"value": "albrrak337@gmail.com"}]}},
}


def test_extract_email_answer_finds_email_regardless_of_question_id():
    assert extract_email_answer(REAL_FORM_156_RESPONSE_ANSWERS) == "albrrak337@gmail.com"


def test_extract_email_answer_normalizes_case():
    answers = {"q1": {"textAnswers": {"answers": [{"value": "Someone@Example.COM"}]}}}
    assert extract_email_answer(answers) == "someone@example.com"


def test_extract_email_answer_returns_none_when_no_email_shaped_value():
    answers = {
        "q1": {"textAnswers": {"answers": [{"value": "Option 1"}]}},
        "q2": {"textAnswers": {"answers": [{"value": "Abdlelah Albrrak"}]}},
    }
    assert extract_email_answer(answers) is None


def test_extract_email_answer_handles_empty_answers():
    assert extract_email_answer({}) is None


# ====================== fixtures ======================


def make_response(response_id: str, email: str) -> dict:
    """One Google Forms response, shaped the way the API returns them."""
    return {
        "responseId": response_id,
        "answers": {
            "3b0efcf2": {"questionId": "3b0efcf2", "textAnswers": {"answers": [{"value": "Some Name"}]}},
            "128490c7": {"questionId": "128490c7", "textAnswers": {"answers": [{"value": email}]}},
        },
    }


@pytest.fixture
def linked_form(db_session):
    """An event with a Google-linked form, ready to sync."""
    event = Events(
        name="form sync event",
        location_type=EventsLocationType.ONLINE,
        location="space",
        start_datetime="2026-06-29 00:00:00",
        end_datetime="2026-06-29 00:00:00",
        status=EventsStatus.OPEN,
    )
    db_session.add(event)
    db_session.flush()

    form = Forms(
        event_id=event.id,
        form_type=FormType.GOOGLE,
        google_form_id=GOOGLE_FORM_ID,
        google_refresh_token="refresh-token-xyz",
    )
    db_session.add(form)
    db_session.flush()
    db_session.commit()
    return form


def add_partial_submission(session, form, email: str, uni_id: str):
    """A member who started the form but whose Google response has not landed yet."""
    member = Members(
        name=f"Member {uni_id}",
        email=email,
        phone_number="0500000000",
        uni_id=uni_id,
        gender=MembersGender.MALE,
        uni_level=4,
        uni_college="Engineering",
    )
    session.add(member)
    session.flush()

    submission = Submissions(
        form_id=form.id, member_id=member.id, is_accepted=0, submission_type=SubmissionsSubmissionType.PARTIAL
    )
    session.add(submission)
    session.flush()
    session.commit()
    return member, submission


# ====================== sync_form_submissions ======================


def test_sync_matches_a_response_to_its_partial_submission(db_session, linked_form):
    _member, submission = add_partial_submission(db_session, linked_form, "matched@example.com", "300000001")
    recorded = RecordedFormResponses(responses={GOOGLE_FORM_ID: [make_response("resp-1", "matched@example.com")]})

    job = job_queries.create_job(db_session, GOOGLE_FORM_ID)
    sync_form_submissions(GOOGLE_FORM_ID, job.id, recorded)

    db_session.expire_all()
    synced = db_session.get(Submissions, submission.id)
    assert synced is not None
    assert synced.submission_type == SubmissionsSubmissionType.GOOGLE
    assert synced.google_submission_id == "resp-1"
    assert synced.google_submission_value is not None

    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.SUCCEEDED
    assert (finished.total, finished.succeeded, finished.failed) == (1, 1, 0)


def test_sync_matches_on_email_regardless_of_case(db_session, linked_form):
    _member, submission = add_partial_submission(db_session, linked_form, "mixed@example.com", "300000002")
    recorded = RecordedFormResponses(responses={GOOGLE_FORM_ID: [make_response("resp-1", "Mixed@Example.COM")]})

    sync_form_submissions(GOOGLE_FORM_ID, None, recorded)

    db_session.expire_all()
    synced = db_session.get(Submissions, submission.id)
    assert synced is not None
    assert synced.submission_type == SubmissionsSubmissionType.GOOGLE


def test_sync_leaves_submissions_alone_when_no_response_matches(db_session, linked_form):
    _member, submission = add_partial_submission(db_session, linked_form, "waiting@example.com", "300000003")
    recorded = RecordedFormResponses(responses={GOOGLE_FORM_ID: [make_response("resp-1", "somebody-else@example.com")]})

    job = job_queries.create_job(db_session, GOOGLE_FORM_ID)
    sync_form_submissions(GOOGLE_FORM_ID, job.id, recorded)

    db_session.expire_all()
    untouched = db_session.get(Submissions, submission.id)
    assert untouched is not None
    assert untouched.submission_type == SubmissionsSubmissionType.PARTIAL

    # A response nobody started a submission for is not a failure - it is what
    # the manual backfill exists to pick up - so the job still succeeds.
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.SUCCEEDED
    assert (finished.total, finished.succeeded) == (0, 0)


def test_sync_counts_every_match_against_the_job(db_session, linked_form):
    add_partial_submission(db_session, linked_form, "one@example.com", "300000004")
    add_partial_submission(db_session, linked_form, "two@example.com", "300000005")
    recorded = RecordedFormResponses(
        responses={
            GOOGLE_FORM_ID: [
                make_response("resp-1", "one@example.com"),
                make_response("resp-2", "two@example.com"),
                make_response("resp-3", "stranger@example.com"),
            ]
        }
    )

    job = job_queries.create_job(db_session, GOOGLE_FORM_ID)
    sync_form_submissions(GOOGLE_FORM_ID, job.id, recorded)

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert (finished.total, finished.succeeded, finished.failed) == (2, 2, 0)


def test_sync_fails_the_job_when_the_form_is_not_linked(db_session, linked_form):
    linked_form.google_refresh_token = None
    db_session.commit()

    job = job_queries.create_job(db_session, GOOGLE_FORM_ID)
    with pytest.raises(GoogleFormNotLinked):
        sync_form_submissions(GOOGLE_FORM_ID, job.id, RecordedFormResponses())

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.FAILED
    # The cause survives into the job record, which a generic RuntimeError
    # could not carry.
    assert finished.error is not None
    assert "GoogleFormNotLinked" in finished.error


def test_sync_fails_the_job_when_the_form_is_unknown(db_session):
    job = job_queries.create_job(db_session, "form-nobody-has")
    with pytest.raises(NotFound):
        sync_form_submissions("form-nobody-has", job.id, RecordedFormResponses())

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.FAILED


def test_sync_succeeds_with_nothing_to_do(db_session, linked_form):
    recorded = RecordedFormResponses(responses={GOOGLE_FORM_ID: []})

    job = job_queries.create_job(db_session, GOOGLE_FORM_ID)
    sync_form_submissions(GOOGLE_FORM_ID, job.id, recorded)

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.SUCCEEDED


def test_sync_reads_the_form_with_its_own_refresh_token(db_session, linked_form):
    """The token travels with the form id, so the adapter never touches the database."""
    seen: list[FormAccess] = []

    class CapturingResponses(RecordedFormResponses):
        def list_responses(self, access: FormAccess) -> list[dict]:
            seen.append(access)
            return []

    sync_form_submissions(GOOGLE_FORM_ID, None, CapturingResponses())

    assert len(seen) == 1
    assert seen[0].google_form_id == GOOGLE_FORM_ID
    assert seen[0].refresh_token == "refresh-token-xyz"


def test_form_access_repr_hides_the_refresh_token():
    access = FormAccess(google_form_id="f", refresh_token="super-secret")
    assert "super-secret" not in repr(access)


# ====================== the webhook, end to end ======================


def test_webhook_syncs_the_form_it_was_notified_about(client: TestClient, db_session, linked_form):
    """Pub/Sub push -> job row -> background sync, against the substituted adapter.

    This is what the seam bought: the route resolves the adapter through
    `get_form_responses` and hands it to the background task, so overriding
    that one dependency puts a recorded adapter in the real one's place along
    the whole path. Nothing here monkeypatches a function.

    What this test deliberately does *not* assert is the resulting submission
    row. Background tasks open their own session through `db_session()`, and
    under the `db_bind` fixture every session shares one connection with
    `join_transaction_mode="create_savepoint"` - a write made from inside a
    request by a second session does not survive the request (checked: the job
    row stays QUEUED even though the sync ran). That is an artifact of the
    single-connection test transaction, not of the sync: in production the two
    sessions are on separate connections. The rows the sync writes are asserted
    by the `sync_form_submissions` tests above, which call it directly.
    """
    from app.main import app

    add_partial_submission(db_session, linked_form, "webhook@example.com", "300000006")
    recorded = RecordedFormResponses(responses={GOOGLE_FORM_ID: [make_response("resp-webhook", "webhook@example.com")]})
    app.dependency_overrides[get_form_responses] = lambda: recorded

    try:
        response = client.post(
            "/submissions/google/webhook",
            json={
                "message": {"attributes": {"formId": GOOGLE_FORM_ID, "eventType": "RESPONSES"}, "messageId": "msg-1"},
                "subscription": "projects/x/subscriptions/y",
            },
        )
    finally:
        app.dependency_overrides.pop(get_form_responses, None)

    assert_2xx(response)
    body = response.json()
    assert body["status"] == "received"
    assert body["form_id"] == GOOGLE_FORM_ID
    assert body["job_id"] is not None

    # TestClient runs background tasks before returning, so the sync has run -
    # against the recorded adapter, for the form the notification named.
    assert recorded.calls == [GOOGLE_FORM_ID]


def test_webhook_without_a_form_id_queues_nothing(client: TestClient, linked_form):
    from app.main import app

    recorded = RecordedFormResponses()
    app.dependency_overrides[get_form_responses] = lambda: recorded

    try:
        response = client.post(
            "/submissions/google/webhook",
            json={"message": {"attributes": {"eventType": "RESPONSES"}, "messageId": "msg-1"}},
        )
    finally:
        app.dependency_overrides.pop(get_form_responses, None)

    assert_2xx(response)
    body = response.json()
    assert (body["status"], body["reason"], body["job_id"]) == ("ignored", "missing_form_id", None)
    assert recorded.calls == []


# ====================== the manual backfill ======================


def test_manual_sync_creates_submissions_for_members_without_one(db_session, linked_form):
    member = Members(
        name="No Submission Yet",
        email="backfill@example.com",
        phone_number="0500000000",
        uni_id="300000007",
        gender=MembersGender.MALE,
        uni_level=4,
        uni_college="Engineering",
    )
    db_session.add(member)
    db_session.commit()

    recorded = RecordedFormResponses(
        responses={
            GOOGLE_FORM_ID: [
                make_response("resp-1", "backfill@example.com"),
                make_response("resp-2", "nobody-in-the-db@example.com"),
                {"responseId": "resp-3", "answers": {}},
            ]
        }
    )

    result = sync_manual_form_submissions(GOOGLE_FORM_ID, limit=50, responses_client=recorded)

    assert result["created"] == 1
    assert result["skipped_no_member"] == 1
    assert result["skipped_missing_email"] == 1
    assert result["total_fetched"] == 3
    assert result["form_id"] == linked_form.id


def test_manual_sync_raises_instead_of_reporting_zeros_when_the_form_is_unknown(db_session):
    """A fetch failure used to come back as a row of zeros, which reads exactly
    like a form that simply had no new responses."""
    with pytest.raises(NotFound):
        sync_manual_form_submissions("form-nobody-has", limit=50, responses_client=RecordedFormResponses())


def test_manual_sync_honours_its_limit(db_session, linked_form):
    for index in range(3):
        member = Members(
            name=f"Member {index}",
            email=f"limit{index}@example.com",
            phone_number="0500000000",
            uni_id=f"30000001{index}",
            gender=MembersGender.MALE,
            uni_level=4,
            uni_college="Engineering",
        )
        db_session.add(member)
    db_session.commit()

    recorded = RecordedFormResponses(
        responses={GOOGLE_FORM_ID: [make_response(f"resp-{index}", f"limit{index}@example.com") for index in range(3)]}
    )

    result = sync_manual_form_submissions(GOOGLE_FORM_ID, limit=2, responses_client=recorded)

    assert result["processed"] == 2
    assert result["created"] == 2
    assert result["total_fetched"] == 3


# ====================== error mapping ======================
#
# The half of the `FormResponses` interface that is not a return value. A
# caller has to be able to tell "nobody linked this form" from "Google is
# down" from "the link expired", which the old `return None` could not.


def make_http_error(status_code: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status_code}), b"{}", uri="https://forms.googleapis.com")


def test_expired_credentials_map_to_auth_expired():
    with pytest.raises(GoogleFormAuthExpired) as raised:
        _reraise_mapped(RefreshError("token revoked"), GOOGLE_FORM_ID)
    assert GOOGLE_FORM_ID in raised.value.detail
    assert raised.value.status_code == 502


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_requests_map_to_auth_expired(status_code):
    with pytest.raises(GoogleFormAuthExpired):
        _reraise_mapped(make_http_error(status_code), GOOGLE_FORM_ID)


def test_unknown_form_maps_to_not_found():
    with pytest.raises(NotFound) as raised:
        _reraise_mapped(make_http_error(404), GOOGLE_FORM_ID)
    assert raised.value.status_code == 404


def test_other_google_errors_map_to_bad_gateway():
    with pytest.raises(BadGateway) as raised:
        _reraise_mapped(make_http_error(500), GOOGLE_FORM_ID)
    assert "500" in raised.value.detail


def test_transport_failures_map_to_service_unavailable():
    with pytest.raises(ServiceUnavailable):
        _reraise_mapped(TransportError("connection reset"), GOOGLE_FORM_ID)


def test_unrecognised_errors_are_re_raised_untouched():
    """A bug in this module must not come back looking like Google having a bad day."""
    original = ValueError("something else entirely")
    with pytest.raises(ValueError) as raised:
        _reraise_mapped(original, GOOGLE_FORM_ID)
    assert raised.value is original


def test_recorded_adapter_refuses_forms_it_was_not_given():
    """So a test cannot pass against a form it never set up."""
    access = FormAccess(google_form_id="never-recorded", refresh_token="t")
    with pytest.raises(NotFound):
        RecordedFormResponses().list_responses(access)
    with pytest.raises(NotFound):
        RecordedFormResponses().get_schema(access)


def test_recorded_adapter_returns_a_recorded_schema():
    access = FormAccess(google_form_id=GOOGLE_FORM_ID, refresh_token="t")
    recorded = RecordedFormResponses(schemas={GOOGLE_FORM_ID: {"formId": GOOGLE_FORM_ID}})
    assert recorded.get_schema(access) == {"formId": GOOGLE_FORM_ID}
