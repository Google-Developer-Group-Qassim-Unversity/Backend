import logging
import json
from time import perf_counter
from typing import Literal, Annotated
from fastapi import APIRouter, Depends, Request, status, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from app.DB import submissions as submission_queries
from app.DB import form_sync_jobs as job_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import authenticated_guard, CurrentMember, admin_guard, resolve_member
from app.exceptions import NotFound
from app.routers.models import submission_exists_model, submission_accept_model
from app.services.form_responses import FormResponsesClient
from app.services.form_sync import resolve_form_access, sync_form_submissions
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


@router.get("/test-google-forms/{google_form_id}", status_code=status.HTTP_200_OK, response_model=dict)
def test_fetch_form_responses(google_form_id: str, session: DB, responses_client: FormResponsesClient):
    """Debug endpoint: what Google currently holds for this form."""
    _form_id, access = resolve_form_access(session, google_form_id)

    responses = responses_client.list_responses(access)
    schema = responses_client.get_schema(access)
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
async def google_forms_webhook(
    request: Request, background_tasks: BackgroundTasks, session: DB, responses_client: FormResponsesClient
):
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
        background_tasks.add_task(sync_form_submissions, form_id, job.id, responses_client)
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
