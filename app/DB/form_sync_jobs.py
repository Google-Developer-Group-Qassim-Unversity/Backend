"""Reads and writes for the form_sync_jobs table.

Mirrors app/DB/email_jobs.py's shape: each mutation commits on its own, so a
background job's progress survives whatever happens to the sync that follows
it.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.DB.schema import FormSyncJobs, FormSyncJobsStatus

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000


def create_job(session: Session, google_form_id: str) -> FormSyncJobs:
    job = FormSyncJobs(google_form_id=google_form_id, status=FormSyncJobsStatus.QUEUED)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def mark_running(session: Session, job_id: int) -> None:
    job = session.get(FormSyncJobs, job_id)
    if job is None:
        return
    job.status = FormSyncJobsStatus.RUNNING
    job.started_at = datetime.now()
    session.commit()


def record_success(session: Session, job_id: int, count: int = 1) -> None:
    job = session.get(FormSyncJobs, job_id)
    if job is None:
        return
    job.succeeded += count
    session.commit()


def record_failure(session: Session, job_id: int, error: str) -> None:
    job = session.get(FormSyncJobs, job_id)
    if job is None:
        return
    job.failed += 1
    job.error = error[:MAX_ERROR_LENGTH]
    session.commit()


def finish(session: Session, job_id: int, error: str | None = None) -> None:
    """Close the job out, deriving the final status from what actually happened.

    `error` is for a failure that killed the whole run rather than one item.
    """
    job = session.get(FormSyncJobs, job_id)
    if job is None:
        return

    if error is not None:
        job.status = FormSyncJobsStatus.FAILED
        job.error = error[:MAX_ERROR_LENGTH]
    elif job.failed and job.succeeded:
        job.status = FormSyncJobsStatus.PARTIAL
    elif job.failed:
        job.status = FormSyncJobsStatus.FAILED
    else:
        job.status = FormSyncJobsStatus.SUCCEEDED

    job.finished_at = datetime.now()
    session.commit()
    logger.info(
        "form sync job %s finished: %s (%s/%s matched, %s failed)",
        job_id,
        job.status.value,
        job.succeeded,
        job.total,
        job.failed,
    )


def get_job(session: Session, job_id: int) -> FormSyncJobs | None:
    return session.get(FormSyncJobs, job_id)
