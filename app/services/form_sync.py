"""Matching Google Form responses onto the submissions we already hold.

Both syncs used to live in routers - the scheduled one in
`app/routers/submissions.py`, the manual backfill in
`app/routers/submissions_manual.py`, which imported the first router to get at
its Google helpers. They are the same job with two different endings, so they
live together now and the routers do routing.

They read Google through the `FormResponses` seam
(`app/services/form_responses.py`) rather than building a client, so a test can
drive either one end to end with recorded payloads.

The difference between them, which is the reason there are two:

- `sync_form_submissions` updates `partial` submissions that a member already
  started. Runs after every webhook notification.
- `sync_manual_form_submissions` creates submissions for responses that have no
  partial row at all, matching on the member's email. Admin-triggered backfill.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.DB import forms as form_queries
from app.DB import members as member_queries
from app.DB import submissions as submission_queries
from app.DB import form_sync_jobs as job_queries
from app.DB.main import db_session
from app.exceptions import GoogleFormNotLinked, NotFound
from app.services.form_responses import FormAccess, FormResponses, get_form_responses
from app.services.job_tracker import FORM_SYNC_JOB_QUERIES, job_boundary

logger = logging.getLogger(__name__)


# Question IDs are NOT stable across forms - each form clone gets its own
# internal IDs, and admins phrase the "personal email" question differently
# across templates - so identifying the email question by a fixed ID or
# title ahead of time is unreliable (confirmed empirically: the same ID
# means a different question, or nothing, on different forms). Scanning
# every text answer for the one value that's actually shaped like an email
# is robust regardless of which question held it or how it's labeled.
# See docs/GOOGLE_FORMS_SYNC.md.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def extract_email_answer(answers: dict) -> str | None:
    """Find the answer in a Google Forms response's `answers` dict that looks like an email address."""
    for answer in answers.values():
        answers_list = answer.get("textAnswers", {}).get("answers", [])
        if not answers_list:
            continue
        value = (answers_list[0].get("value") or "").strip()
        if value and EMAIL_PATTERN.match(value):
            return value.lower()
    return None


def resolve_form_access(session: Session, google_form_id: str) -> tuple[int, FormAccess]:
    """The `forms` row for a Google form id, as the id and the token to read it with.

    Raises rather than returning `None`. The two failures here used to be a
    `logger.info` and a `return None` inside the fetch, which is how a form
    nobody had linked came back looking exactly like an empty inbox.
    """
    form = form_queries.get_form_by_google_form_id(session, google_form_id)
    if form is None:
        raise NotFound("Google form", google_form_id)
    if not form.google_refresh_token:
        raise GoogleFormNotLinked(google_form_id)
    return form.id, FormAccess(google_form_id=google_form_id, refresh_token=form.google_refresh_token)


def sync_form_submissions(
    google_form_id: str, job_id: int | None = None, responses_client: FormResponses | None = None
) -> None:
    """Match a form's Google responses onto the `partial` submissions it already has.

    `responses_client` is passed in by the route, which resolves it through the
    `get_form_responses` dependency - a background task runs after the response
    is sent, where injection no longer reaches. It defaults to the production
    adapter so the admin-triggered path can call this directly.
    """
    client = responses_client or get_form_responses()

    with job_boundary(job_id, FORM_SYNC_JOB_QUERIES) as (tracker, session):
        logger.info("syncing google_form_id %s", google_form_id)

        form_id, access = resolve_form_access(session, google_form_id)
        google_responses = client.list_responses(access)
        logger.info("form %s (db id %s): %d responses", google_form_id, form_id, len(google_responses))

        partial_submissions = submission_queries.get_partial_submissions_by_form_id(session, form_id)
        logger.info("%d partial submissions to match against", len(partial_submissions))

        if not partial_submissions:
            logger.info("nothing to sync for form %s", google_form_id)
            return

        partial_by_email = {}
        for submission in partial_submissions:
            email = (submission.email or "").strip().lower()
            if not email:
                logger.info("partial submission %s has no email on file, skipping", submission.submission_id)
                continue
            partial_by_email[email] = submission

        # Only responses that match something can succeed or fail, so they are
        # what the job counts. An unmatched response is neither - it usually
        # belongs to somebody who filled the form without starting a
        # submission, which the manual backfill exists to pick up.
        matchable = []
        unmatched_responses = []
        for response in google_responses:
            email = extract_email_answer(response.get("answers", {}))
            if email and email in partial_by_email:
                matchable.append((response, partial_by_email[email], email))
            else:
                unmatched_responses.append(response.get("responseId"))

        if job_id is not None:
            job_queries.set_total(session, job_id, len(matchable))

        for response, partial_submission, email in matchable:
            response_id = response.get("responseId")
            # One bad response cannot cancel the ones after it; the failure is
            # recorded against the job instead.
            with tracker.recipient(f"response {response_id} ({email})"):
                updated = submission_queries.update_google_submission(
                    session,
                    partial_submission.submission_id,
                    submission_type="google",
                    google_submission_id=response_id,
                    google_submission_value=response.get("answers", {}),
                )
                if updated is None:
                    raise RuntimeError(f"submission {partial_submission.submission_id} disappeared mid-sync")
                session.commit()
                logger.info("matched response %s to submission %s", response_id, partial_submission.submission_id)

        logger.info(
            "form %s sync complete: %d responses, %d matched, %d unmatched",
            google_form_id,
            len(google_responses),
            len(matchable),
            len(unmatched_responses),
        )
        if unmatched_responses:
            logger.info("unmatched response ids: %s", unmatched_responses)


def sync_manual_form_submissions(
    google_form_id: str, limit: int, responses_client: FormResponses | None = None
) -> dict:
    """Create submissions for responses that never had a partial row.

    Differs from the scheduled job in that it creates rather than updates, and
    matches a response's email against the `members` table instead of against
    an existing submission.
    """
    client = responses_client or get_form_responses()

    with db_session() as session:
        form_id, access = resolve_form_access(session, google_form_id)
        google_responses = client.list_responses(access)
        logger.info("manual sync for form %s (db id %s), limit %d", google_form_id, form_id, limit)

        created = 0
        skipped_existing = 0
        skipped_no_member = 0
        skipped_missing_email = 0
        processed = 0

        for response in google_responses[:limit]:
            processed += 1
            response_id = response.get("responseId")
            answers = response.get("answers", {}) or {}

            email = extract_email_answer(answers)
            if not email:
                skipped_missing_email += 1
                continue

            member = member_queries.get_member_by_email_or_none(session, email)
            if not member:
                skipped_no_member += 1
                continue

            if submission_queries.get_submission_by_form_and_member(session, form_id, member.id):
                skipped_existing += 1
                continue

            new_submission = submission_queries.create_google_submission(
                session,
                form_id=form_id,
                member_id=member.id,
                google_submission_id=response_id,
                google_submission_value=answers,
            )
            if new_submission:
                created += 1

        session.commit()

    logger.info(
        "manual sync for form %s: %d fetched, %d processed, %d created, "
        "%d skipped existing, %d skipped no member, %d skipped missing email",
        google_form_id,
        len(google_responses),
        processed,
        created,
        skipped_existing,
        skipped_no_member,
        skipped_missing_email,
    )

    return {
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_no_member": skipped_no_member,
        "skipped_missing_email": skipped_missing_email,
        "processed": processed,
        "total_fetched": len(google_responses),
        "form_id": form_id,
    }
