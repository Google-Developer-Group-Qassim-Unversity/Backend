"""Reads and writes for the email_jobs table.

Each mutation commits on its own. A background job's progress has to survive
whatever happens to the send that follows it, so it cannot ride along in the
same transaction as the email log rows.
"""

import logging
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.DB.schema import EmailJobs, EmailJobsStatus, EmailJobsType

logger = logging.getLogger(__name__)

# an `error` column is TEXT, but there is no reason to store a whole traceback
MAX_ERROR_LENGTH = 2000


def create_job(
    session: Session, job_type: EmailJobsType, created_by: int, total: int, event_id: int | None = None
) -> EmailJobs:
    job = EmailJobs(
        job_type=job_type, status=EmailJobsStatus.QUEUED, created_by=created_by, event_id=event_id, total=total
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def mark_running(session: Session, job_id: int) -> None:
    job = session.get(EmailJobs, job_id)
    if job is None:
        return
    job.status = EmailJobsStatus.RUNNING
    job.started_at = datetime.now()
    session.commit()


def record_success(session: Session, job_id: int, count: int = 1) -> None:
    job = session.get(EmailJobs, job_id)
    if job is None:
        return
    job.succeeded += count
    session.commit()


def record_failure(session: Session, job_id: int, error: str) -> None:
    job = session.get(EmailJobs, job_id)
    if job is None:
        return
    job.failed += 1
    job.error = error[:MAX_ERROR_LENGTH]
    session.commit()


def finish(session: Session, job_id: int, error: str | None = None) -> None:
    """Close the job out, deriving the final status from what actually happened.

    `error` is for a failure that killed the whole run rather than one recipient.
    """
    job = session.get(EmailJobs, job_id)
    if job is None:
        return

    if error is not None:
        job.status = EmailJobsStatus.FAILED
        job.error = error[:MAX_ERROR_LENGTH]
    elif job.failed and job.succeeded:
        job.status = EmailJobsStatus.PARTIAL
    elif job.failed:
        job.status = EmailJobsStatus.FAILED
    else:
        job.status = EmailJobsStatus.SUCCEEDED

    job.finished_at = datetime.now()
    session.commit()
    logger.info(
        "email job %s finished: %s (%s/%s sent, %s failed)",
        job_id,
        job.status.value,
        job.succeeded,
        job.total,
        job.failed,
    )


def get_jobs(session: Session, limit: int = 50, status: EmailJobsStatus | None = None) -> list[EmailJobs]:
    stmt = select(EmailJobs).order_by(desc(EmailJobs.created_at), desc(EmailJobs.id)).limit(limit)
    if status is not None:
        stmt = stmt.where(EmailJobs.status == status)
    return list(session.scalars(stmt).all())


def get_job(session: Session, job_id: int) -> EmailJobs | None:
    return session.get(EmailJobs, job_id)


def get_unfinished(session: Session) -> list[EmailJobs]:
    """Jobs still marked queued or running.

    A worker restart leaves these stranded - nothing resumes a BackgroundTask -
    so they are worth surfacing rather than letting them sit as "running" forever.
    """
    stmt = select(EmailJobs).where(EmailJobs.status.in_([EmailJobsStatus.QUEUED, EmailJobsStatus.RUNNING]))
    return list(session.scalars(stmt).all())
