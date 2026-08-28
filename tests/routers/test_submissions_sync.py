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
    from app.routers.submissions import extract_email_answer

    assert extract_email_answer(REAL_FORM_156_RESPONSE_ANSWERS) == "albrrak337@gmail.com"


def test_extract_email_answer_normalizes_case():
    from app.routers.submissions import extract_email_answer

    answers = {"q1": {"textAnswers": {"answers": [{"value": "Someone@Example.COM"}]}}}
    assert extract_email_answer(answers) == "someone@example.com"


def test_extract_email_answer_returns_none_when_no_email_shaped_value():
    from app.routers.submissions import extract_email_answer

    answers = {
        "q1": {"textAnswers": {"answers": [{"value": "Option 1"}]}},
        "q2": {"textAnswers": {"answers": [{"value": "Abdlelah Albrrak"}]}},
    }
    assert extract_email_answer(answers) is None


def test_extract_email_answer_handles_empty_answers():
    from app.routers.submissions import extract_email_answer

    assert extract_email_answer({}) is None


# ====================== sync_form_submissions job status ======================
#
# sync_form_submissions used to only `logger.exception` a failed fetch and
# return - the same "invisible failure" shape email_jobs.py was rebuilt to
# fix. These pin the job_boundary wiring: a fetch failure must fail the job,
# and a clean run (even one with nothing to do) must succeed it.


def test_sync_form_submissions_marks_job_failed_when_fetch_fails(db_session, monkeypatch):
    import pytest

    from app.DB import form_sync_jobs as job_queries
    from app.DB.schema import FormSyncJobsStatus
    from app.routers import submissions

    monkeypatch.setattr(submissions, "fetch_form_responses", lambda google_form_id: None)

    job = job_queries.create_job(db_session, "form-abc")

    with pytest.raises(RuntimeError):
        submissions.sync_form_submissions("form-abc", job.id)

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.FAILED


def test_sync_form_submissions_marks_job_succeeded_when_nothing_to_sync(db_session, monkeypatch):
    from app.DB import form_sync_jobs as job_queries
    from app.DB import submissions as submission_queries
    from app.DB.schema import FormSyncJobsStatus
    from app.routers import submissions

    monkeypatch.setattr(
        submissions,
        "fetch_form_responses",
        lambda google_form_id: {"form_id": 1, "google_form_id": google_form_id, "responses": []},
    )
    monkeypatch.setattr(submission_queries, "get_partial_submissions_by_form_id", lambda session, form_id: [])

    job = job_queries.create_job(db_session, "form-abc")

    submissions.sync_form_submissions("form-abc", job.id)

    db_session.expire_all()
    finished = job_queries.get_job(db_session, job.id)
    assert finished is not None
    assert finished.status == FormSyncJobsStatus.SUCCEEDED
