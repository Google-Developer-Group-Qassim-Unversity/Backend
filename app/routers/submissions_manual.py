import logging
from fastapi import Depends, APIRouter, Query, status

from app.DB.main import db_session
from app.helpers import admin_guard
from app.DB import submissions as submission_queries, members as member_queries

# Reuse Google Forms helpers from the existing submissions router
from app.routers.submissions import fetch_form_responses, extract_email_answer, sync_form_submissions
from typing import Annotated

from app.routers.responses import ManualSyncResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/submissions_manual", tags=["Submissions Manual"])


def sync_manual_form_submissions(google_form_id: str, limit: int):
    """
    differs from the scheduled job in that it creates new submissions instead of updating partial ones
    """
    try:
        logger.info(f"Manual sync submissions for google_form_id: {google_form_id} (limit={limit})")

        fetch_result = fetch_form_responses(google_form_id)
        if fetch_result is None:
            logger.info("ERROR: Failed to fetch form responses")
            return {
                "created": 0,
                "skipped_existing": 0,
                "skipped_no_member": 0,
                "skipped_missing_email": 0,
                "processed": 0,
                "total_fetched": 0,
            }

        form_id = fetch_result["form_id"]
        google_responses = fetch_result["responses"] or []

        created = 0
        skipped_existing = 0
        skipped_no_member = 0
        skipped_missing_email = 0
        processed = 0

        with db_session() as session:
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

                existing = submission_queries.get_submission_by_form_and_member(session, form_id, member.id)
                if existing:
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

        logger.info("=== Manual Sync Summary ===")
        logger.info(f"google_form_id: {google_form_id}")
        logger.info(f"form_id: {form_id}")
        logger.info(f"total_fetched: {len(google_responses)}")
        logger.info(f"processed: {processed}")
        logger.info(f"created: {created}")
        logger.info(f"skipped_existing: {skipped_existing}")
        logger.info(f"skipped_no_member: {skipped_no_member}")
        logger.info(f"skipped_missing_email: {skipped_missing_email}")

        return {
            "created": created,
            "skipped_existing": skipped_existing,
            "skipped_no_member": skipped_no_member,
            "skipped_missing_email": skipped_missing_email,
            "processed": processed,
            "total_fetched": len(google_responses),
            "form_id": form_id,
        }

    except Exception as e:
        logger.exception(e)
        raise


@router.post(
    "/google/{google_form_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(admin_guard)],
    response_model=ManualSyncResponse,
)
def manual_create_google_submissions(google_form_id: str, limit: Annotated[int, Query(ge=1, le=2000)] = 50):
    """
    Public (no-auth) endpoint to manually sync Google Form responses into DB submissions.
    Processes only the first `limit` responses as returned by the Google API.
    """
    return sync_manual_form_submissions(google_form_id, limit)


@router.post("/google/run/{google_form_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(admin_guard)])
def manual_run_google_form_submissions(google_form_id: str):
    return sync_form_submissions(google_form_id)
