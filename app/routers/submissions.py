import logging
import json
from time import perf_counter
from typing import Literal, Annotated
from fastapi import APIRouter, Depends, Request, status, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from app.DB import submissions as submission_queries
from app.DB import form_sync_jobs as job_queries
from fastapi_clerk_auth import HTTPAuthorizationCredentials
from app.helpers import authenticated_guard, CurrentMember, resolve_member
from sqlalchemy import select

from app.DB.schema import EventHistoryAction, EventsStatus, Forms
from app.services import event_history
from app.exceptions import NotFound, RegistrationClosed, SubmissionNotFound
from app.routers.models import submission_exists_model, submission_accept_model
from app.services.form_responses import FormResponsesClient
from app.services.form_sync import resolve_form_access, sync_form_submissions
from app.dependencies import DB

from app.routers.responses import FormSyncJobModel, StatusResponse, SubmissionResponse, WebhookAckResponse
from app.services.permissions.catalogue import Perm
from app.services.permissions.dependencies import CurrentAccess
from app.services.permissions.departments import form_departments
from app.services.permissions.guards import Require, Staff


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


@router.delete("/{form_id:int}", status_code=status.HTTP_200_OK, response_model=StatusResponse)
def cancel_submission(form_id: int, member: CurrentMember, session: DB):
    """Cancel the caller's own registration, while the event is still taking registrations.

    Accepted registrations can be cancelled too - that frees the seat. Once the
    event leaves `open` (it is running or over) the registration is part of the
    record and stays.

    A soft delete: the row is kept with `cancelled_at` set. Registering again
    brings it back (see `create_submission`).
    """
    submission = submission_queries.get_submission_by_form_and_member(session, form_id, member.id)
    if submission is None:
        raise SubmissionNotFound(form_id)
    if submission.form.event.status != EventsStatus.OPEN:
        raise RegistrationClosed(form_id)
    submission_queries.cancel_submission(session, submission)
    session.commit()
    logger.info(f"member [{member.id}] cancelled submission [{submission.id}] for form [{form_id}]")
    return {"status": "success"}


@router.put(
    "/accept",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(Require(Perm.SUBMISSIONS_REVIEW))],
    response_model=StatusResponse,
)
def accept_submission(
    submissions: list[submission_accept_model], session: DB, access: CurrentAccess, actor: CurrentMember
):
    # Every submission's event must belong to a department the caller reviews registrations for.
    for form_id in submission_queries.get_form_ids(session, [s.submission_id for s in submissions]):
        access.require_any(Perm.SUBMISSIONS_REVIEW, form_departments(session, {"form_id": form_id}))
    try:
        # Who accepted or rejected whom, one history row per event.
        reviewed: dict[int, list[dict]] = {}
        for submission in submissions:
            row = submission_queries.update_is_accepted(session, submission.submission_id, submission.is_accepted)
            if row is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submission not found")
            event_id = session.scalar(select(Forms.event_id).where(Forms.id == row.form_id))
            assert event_id is not None, f"Form [{row.form_id}] has no event"
            reviewed.setdefault(event_id, []).append(
                {"submission_id": row.id, "member_id": row.member_id, "is_accepted": bool(row.is_accepted)}
            )
        for event_id, rows in reviewed.items():
            event_history.record(
                session, event_id, EventHistoryAction.SUBMISSIONS_REVIEWED, actor, {"submissions": rows}
            )
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
    dependencies=[Depends(Staff)],
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
