from fastapi import APIRouter, Query, status

from app.DB.main import db_session
from app.DB import submissions as submission_queries, members as member_queries
from app.routers.logging import (
    LogFile,
    write_log_to,
    write_log_traceback_to,
    write_log_exception_to,
    write_log_title_to,
)

# Reuse Google Forms helpers from the existing submissions router
from app.routers.submissions import fetch_form_responses, extract_email_answer, sync_form_submissions
from typing import Annotated

router = APIRouter()


def sync_manual_form_submissions(google_form_id: str, limit: int, log_file):
    """
    differs from the scheduled job in that it creates new submissions instead of updating partial ones
    """
    try:
        write_log_title_to(log_file, f"Manual sync submissions for google_form_id: {google_form_id} (limit={limit})")

        fetch_result = fetch_form_responses(google_form_id, log_file)
        if fetch_result is None:
            write_log_to(log_file, "ERROR: Failed to fetch form responses")
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

        write_log_to(log_file, "=== Manual Sync Summary ===")
        write_log_to(log_file, f"google_form_id: {google_form_id}")
        write_log_to(log_file, f"form_id: {form_id}")
        write_log_to(log_file, f"total_fetched: {len(google_responses)}")
        write_log_to(log_file, f"processed: {processed}")
        write_log_to(log_file, f"created: {created}")
        write_log_to(log_file, f"skipped_existing: {skipped_existing}")
        write_log_to(log_file, f"skipped_no_member: {skipped_no_member}")
        write_log_to(log_file, f"skipped_missing_email: {skipped_missing_email}")

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
        write_log_exception_to(log_file, e)
        write_log_traceback_to(log_file)
        raise


@router.post("/google/{google_form_id}", status_code=status.HTTP_200_OK)
def manual_create_google_submissions(google_form_id: str, limit: Annotated[int, Query(ge=1, le=2000)] = 50):
    """
    Public (no-auth) endpoint to manually sync Google Form responses into DB submissions.
    Processes only the first `limit` responses as returned by the Google API.
    """
    with LogFile("manual google submissions sync") as log:
        return sync_manual_form_submissions(google_form_id, limit, log.file)


@router.post("/google/run/{google_form_id}", status_code=status.HTTP_200_OK)
def manual_run_google_form_submissions(google_form_id: str):
    with LogFile("manual google submissions sync") as log:
        return sync_form_submissions(google_form_id, log.file)
