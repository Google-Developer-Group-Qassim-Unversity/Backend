import logging
import json
import re
from time import perf_counter
from typing import Literal, Annotated
from fastapi import APIRouter, Depends, Request, status, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from app.DB.main import db_session
from app.DB import submissions as submission_queries, forms as form_queries
from app.DB import form_sync_jobs as job_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import authenticated_guard, CurrentMember, admin_guard, resolve_member
from app.config import config
from app.exceptions import NotFound
from app.routers.models import submission_exists_model, submission_accept_model
from app.services.job_tracker import FORM_SYNC_JOB_QUERIES, job_boundary
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
from app.dependencies import DB

from app.routers.responses import FormSyncJobModel, StatusResponse, SubmissionResponse, WebhookAckResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/submissions", tags=["Submissions"])


@router.post("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=SubmissionResponse)
def create_submission(
    form_id: int,
    submission_type: Literal["none", "partial"],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(authenticated_guard)],
    session: DB,
):
    member_id = resolve_member(session, credentials).id
    new_submission = submission_queries.create_submission(session, form_id, submission_type, member_id)
    if not new_submission:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submission already exists")
    session.commit()
    return new_submission


@router.get("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=submission_exists_model)
def check_submission_exists(form_id: int, member: CurrentMember, session: DB):
    try:
        logger.info(f"Querying DB for form_id [{form_id}]")
        start = perf_counter()
        member_id = member.id
        session.commit()
        submission = submission_queries.get_submission_by_form_and_member(session, form_id, member_id)
        end = perf_counter()
        logger.info(
            f"got member [{member_id}], found submission [{submission}]  DB took [{(end - start) * 1000:.2f}]ms to execute"
        )
        if submission is None:
            return {"submission_status": False}
        submission_type = submission.submission_type
        if submission_type == "partial":
            return {"submission_status": "partial", "submission_timestamp": submission.submitted_at}
        return {"submission_status": True, "submission_timestamp": submission.submitted_at}
    except Exception:
        raise


@router.put(
    "/accept", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)], response_model=StatusResponse
)
def accept_submission(submissions: list[submission_accept_model], session: DB):
    try:
        for submission in submissions:
            submission = submission_queries.update_is_accepted(
                session, submission.submission_id, submission.is_accepted
            )
            if submission is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submission not found")
        session.commit()
        return {"status": "success"}
    except Exception:
        raise


# ====================== Google Forms API ======================

# ==============================================================


def get_google_credentials(refresh_token: str):
    credentials = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
    )

    # Refresh the token if needed
    if not credentials.valid:
        credentials.refresh(GoogleRequest())

    return credentials


def fetch_schema(google_form_id: str):
    """Fetch the form schema from Google Forms API"""
    with db_session() as session:
        form = form_queries.get_form_by_google_form_id(session, google_form_id)

        if not form:
            raise ValueError(f"Form not found in database for google_form_id: {google_form_id}")

        if not form.google_refresh_token:
            raise ValueError("Form does not have a refresh token")

        # Get Google credentials
        credentials = get_google_credentials(form.google_refresh_token)

        # Build the Forms API service
        service = build("forms", "v1", credentials=credentials)

        # Fetch the form schema from Google
        schema = service.forms().get(formId=google_form_id).execute()

        return schema


# Question IDs are NOT stable across forms - each form clone gets its own
# internal IDs, and admins phrase the "personal email" question differently
# across templates - so identifying the email question by a fixed ID or
# title ahead of time is unreliable (confirmed empirically: the same ID
# means a different question, or nothing, on different forms). Scanning
# every text answer for the one value that's actually shaped like an email
# is robust regardless of which question held it or how it's labeled.
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


def fetch_form_responses(google_form_id: str):
    """Fetch all responses from a Google Form and return them"""
    try:
        logger.info(f"Fetching responses for form: {google_form_id}")

        # Get form details from database to retrieve refresh token
        with db_session() as session:
            form = form_queries.get_form_by_google_form_id(session, google_form_id)

            if not form:
                logger.info(f"ERROR: Form not found in database for google_form_id: {google_form_id}")
                return None

            if not form.google_refresh_token:
                logger.info(f"ERROR: No refresh token available for form: {google_form_id}")
                return None

            logger.info(f"Found form in database with ID: {form.id}")
            form_id = form.id

            # Get Google credentials
            credentials = get_google_credentials(form.google_refresh_token)
            logger.info("Successfully authenticated with Google")

            # Build the Forms API service
            service = build("forms", "v1", credentials=credentials)

            # Fetch the form responses
            result = service.forms().responses().list(formId=google_form_id).execute()

            responses = result.get("responses", [])
            logger.info(f"\nTotal responses found: {len(responses)}")

            # Log all responses
            for idx, response in enumerate(responses, 1):
                logger.info(f"\n--- Response #{idx} ---")
                logger.debug("request body: %s", response)

            logger.info("\n=== Finished fetching responses ===")

            return {"form_id": form_id, "google_form_id": google_form_id, "responses": responses}

    except Exception as e:
        logger.exception(e)
        return None


def sync_form_submissions(google_form_id: str, job_id: int | None = None):
    with job_boundary(job_id, FORM_SYNC_JOB_QUERIES) as (_tracker, session):
        logger.info(f"Running scheduled job: sync for google_form_id: {google_form_id}")

        # Fetch Google Form responses
        fetch_result = fetch_form_responses(google_form_id)

        if fetch_result is None:
            # fetch_form_responses already logged the underlying exception (or lack
            # of a form/refresh token); this is what makes the job itself FAILED
            # rather than silently returning as if nothing was wrong.
            raise RuntimeError(f"Failed to fetch form responses for google_form_id={google_form_id}")

        form_id = fetch_result["form_id"]
        google_responses = fetch_result["responses"]

        logger.info(f"Form ID: {form_id}")
        logger.info(f"Google responses count: {len(google_responses)}")

        partial_submissions = submission_queries.get_partial_submissions_by_form_id(session, form_id)
        logger.info(f"Partial submissions count: {len(partial_submissions)}")

        if not partial_submissions:
            logger.info("No partial submissions to sync")
            return

        # Create a mapping of email (normalized) to partial submissions
        partial_by_email = {}
        for submission in partial_submissions:
            email = (submission.email or "").strip().lower()
            if not email:
                logger.info(f"Partial submission: ID={submission.submission_id} has no email on file, skipping")
                continue
            partial_by_email[email] = submission
            logger.info(f"Partial submission: ID={submission.submission_id}, email={email}")

        # Match Google responses to partial submissions
        matched_count = 0
        unmatched_responses = []

        for response in google_responses:
            response_id = response.get("responseId")
            answers = response.get("answers", {})

            email = extract_email_answer(answers)
            if not email:
                logger.info(f"Response {response_id}: No email-shaped answer found")
                unmatched_responses.append(response_id)
                continue

            logger.info(f"Response {response_id}: email={email}")

            # Check if this email has a partial submission
            if email in partial_by_email:
                partial_submission = partial_by_email[email]

                # Update submission with Google response data
                updated = submission_queries.update_google_submission(
                    session,
                    partial_submission.submission_id,
                    submission_type="google",
                    google_submission_id=response_id,
                    google_submission_value=answers,
                )

                if updated:
                    matched_count += 1
                    logger.info(f"✓ Matched and updated submission ID {partial_submission.id} for email {email}")
                    logger.info(f"  - Google response ID: {response_id}")
                else:
                    logger.info(f"✗ Failed to update submission ID {partial_submission.id}")
            else:
                logger.info(f"Response {response_id}: No matching partial submission for email {email}")
                unmatched_responses.append(response_id)

        # Commit all updates
        session.commit()

        # Summary
        logger.info("\n=== Sync Summary ===")
        logger.info(f"Total Google responses: {len(google_responses)}")
        logger.info(f"Total partial submissions: {len(partial_submissions)}")
        logger.info(f"Successfully matched: {matched_count}")
        logger.info(f"Unmatched responses: {len(unmatched_responses)}")

        if unmatched_responses:
            logger.info(f"Unmatched response IDs: {unmatched_responses}")

        logger.info("\n=== Sync Complete ===")


@router.get("/test-google-forms/{google_form_id}", status_code=status.HTTP_200_OK, response_model=dict)
def test_fetch_form_responses(google_form_id: str):
    responses = fetch_form_responses(google_form_id)
    schema = fetch_schema(google_form_id)
    logger.debug("responses: %s", json.dumps(responses, ensure_ascii=False))
    logger.debug("schema: %s", json.dumps(schema, ensure_ascii=False))

    return schema


@router.get(
    "/sync-jobs/{job_id:int}",
    status_code=status.HTTP_200_OK,
    response_model=FormSyncJobModel,
    dependencies=[Depends(admin_guard)],
    responses={404: {"description": "Job not found"}},
)
def get_form_sync_job(job_id: int, session: DB):
    job = job_queries.get_job(session, job_id)
    if job is None:
        raise NotFound("Form sync job", job_id)
    return job


@router.post("/google/webhook", status_code=status.HTTP_200_OK, response_model=WebhookAckResponse)
async def google_forms_webhook(request: Request, background_tasks: BackgroundTasks, session: DB):
    try:
        logger.info("⚓ Google Forms Webhook Notification ⚓")

        body = await request.json()

        # Validate it's a Pub/Sub message
        if "message" not in body:
            logger.debug("request body: %s", {"status": "ignored", "reason": "not_pubsub_message", "body": body})
            return {"status": "ignored", "reason": "not_pubsub_message"}
        if "attributes" not in body["message"]:
            logger.debug("request body: %s", {"status": "ignored", "reason": "missing_attributes"})
            return {"status": "ignored", "reason": "missing_attributes"}

        logger.info(f"Received Pub/Sub message: {body}")
        message = body["message"]
        attributes = message["attributes"]

        # Extract form information from attributes
        form_id = attributes.get("formId")
        watch_id = attributes.get("watchId")
        event_type = attributes.get("eventType")
        message_id = message.get("messageId") or message.get("message_id")
        publish_time = message.get("publishTime") or message.get("publish_time")
        subscription = body.get("subscription")

        if not form_id:
            logger.debug("request body: %s", {"status": "ignored", "reason": "missing_form_id"})
            return {"status": "ignored", "reason": "missing_form_id"}

        # Log the notification
        logger.debug(
            "request body: %s",
            {
                "status": "received",
                "form_id": form_id,
                "watch_id": watch_id,
                "event_type": event_type,
                "message_id": message_id,
                "publish_time": publish_time,
                "subscription": subscription,
            },
        )

        # Sync form submissions in the background
        job = await run_in_threadpool(job_queries.create_job, session, form_id)
        background_tasks.add_task(sync_form_submissions, form_id, job.id)
        logger.info(f"Background task scheduled to sync submissions for form: {form_id} (job {job.id})")

        return {
            "status": "received",
            "form_id": form_id,
            "event_type": event_type,
            "message_id": message_id,
            "job_id": job.id,
        }

    except json.JSONDecodeError as e:
        logger.exception(e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON: {str(e)}")
    except KeyError as e:
        logger.exception(e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing required field: {str(e)}")
