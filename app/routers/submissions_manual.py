from fastapi import APIRouter, HTTPException, Query, status

from app.DB.main import SessionLocal
from app.DB import submissions as submission_queries, members as member_queries
from app.routers.logging import (
    create_log_file,
    write_log,
    write_log_exception,
    write_log_title,
    write_log_traceback,
)

# Reuse Google Forms helpers from the existing submissions router
from app.routers.submissions import fetch_form_responses, get_uni_id_question_id

router = APIRouter()


def sync_manual_form_submissions(google_form_id: str, limit: int, log_file):
    try:
        write_log_title(log_file, f"Manual sync submissions for google_form_id: {google_form_id} (limit={limit})")

        fetch_result = fetch_form_responses(google_form_id, log_file)
        if not fetch_result:
            write_log(log_file, "ERROR: Failed to fetch form responses")
            return {
                "created": 0,
                "skipped_existing": 0,
                "skipped_no_member": 0,
                "skipped_missing_uni_id": 0,
                "processed": 0,
                "total_fetched": 0,
            }

        form_id = fetch_result["form_id"]
        google_responses = fetch_result["responses"] or []

        try:
            uni_id_question_id = get_uni_id_question_id(form_id)
            write_log(log_file, f"Found uni_id question ID: {uni_id_question_id}")
        except Exception as e:
            write_log(log_file, f"ERROR: Failed to get uni_id question ID: {str(e)}")
            write_log_exception(log_file, e)
            return {
                "created": 0,
                "skipped_existing": 0,
                "skipped_no_member": 0,
                "skipped_missing_uni_id": 0,
                "processed": 0,
                "total_fetched": len(google_responses),
            }

        created = 0
        skipped_existing = 0
        skipped_no_member = 0
        skipped_missing_uni_id = 0
        processed = 0

        with SessionLocal() as session:
            for response in google_responses[:limit]:
                processed += 1

                response_id = response.get("responseId")
                answers = response.get("answers", {}) or {}

                uni_id_answer = answers.get(uni_id_question_id)
                if not uni_id_answer:
                    skipped_missing_uni_id += 1
                    continue

                text_answers = uni_id_answer.get("textAnswers", {}) or {}
                answers_list = text_answers.get("answers", []) or []
                if not answers_list:
                    skipped_missing_uni_id += 1
                    continue

                uni_id = (answers_list[0].get("value", "") or "").strip()
                if not uni_id:
                    skipped_missing_uni_id += 1
                    continue

                member = member_queries.get_member_by_uni_id(session, uni_id)
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

        write_log(log_file, "=== Manual Sync Summary ===")
        write_log(log_file, f"google_form_id: {google_form_id}")
        write_log(log_file, f"form_id: {form_id}")
        write_log(log_file, f"total_fetched: {len(google_responses)}")
        write_log(log_file, f"processed: {processed}")
        write_log(log_file, f"created: {created}")
        write_log(log_file, f"skipped_existing: {skipped_existing}")
        write_log(log_file, f"skipped_no_member: {skipped_no_member}")
        write_log(log_file, f"skipped_missing_uni_id: {skipped_missing_uni_id}")

        return {
            "created": created,
            "skipped_existing": skipped_existing,
            "skipped_no_member": skipped_no_member,
            "skipped_missing_uni_id": skipped_missing_uni_id,
            "processed": processed,
            "total_fetched": len(google_responses),
            "form_id": form_id,
        }

    except Exception as e:
        write_log_exception(log_file, e)
        write_log_traceback(log_file)
        raise


@router.post("/google/{google_form_id}", status_code=status.HTTP_200_OK)
def manual_sync_google_form_submissions(
    google_form_id: str,
    limit: int = Query(default=50, ge=1, le=2000),
):
    """
    Public (no-auth) endpoint to manually sync Google Form responses into DB submissions.
    Processes only the first `limit` responses as returned by the Google API.
    """
    log_file = create_log_file("manual google submissions sync")
    try:
        return sync_manual_form_submissions(google_form_id, limit, log_file)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while syncing submissions",
        )

