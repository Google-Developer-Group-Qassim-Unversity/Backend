"""2f8c7b6a901d links old email logs to their jobs before the outbox reads them."""

import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.DB import email_jobs as jobs
from app.DB.semesters import get_semester_by_hijri_code
from app.DB.schema import EmailJobs, EmailJobsStatus, EmailJobsType, EmailLogs, EmailLogsEmailType, Events
from tests.factories import make_event

MIGRATION = Path(__file__).parents[1] / "alembic/versions/2f8c7b6a901d_mail_sends.py"


def _backfill(session: Session) -> None:
    spec = importlib.util.spec_from_file_location("mail_sends_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session.flush()
    module.backfill(session.connection())
    session.expire_all()


def _old_job(
    session: Session, sender: int, job_type: EmailJobsType, created: datetime, started: datetime, finished: datetime
) -> EmailJobs:
    job = jobs.create_job(session, job_type, sender, total=1)
    job.status = EmailJobsStatus.SUCCEEDED
    job.created_at, job.started_at, job.finished_at = created, started, finished
    session.commit()
    return job


def _old_log(
    session: Session, sender: int, email_type: EmailLogsEmailType, sent_at: datetime, **kwargs: Any
) -> EmailLogs:
    log = EmailLogs(
        from_address="sender@example.com",
        sent_at=sent_at,
        sent_by=sender,
        recipient_count=1,
        email_type=email_type,
        **kwargs,
    )
    session.add(log)
    session.commit()
    return log


def test_utc_created_at_is_normalised_before_logs_are_linked(db_session: Session, seed_refs: Any) -> None:
    sender = seed_refs.ahmed.id
    # Stored 3h early, the link window of this campaign also covers `earlier`.
    campaign = _old_job(
        db_session,
        sender,
        EmailJobsType.BLAST,
        datetime(2026, 10, 5, 13, 34, 49),
        datetime(2026, 10, 5, 16, 34, 49),
        datetime(2026, 10, 5, 16, 39, 10),
    )
    own = _old_log(db_session, sender, EmailLogsEmailType.BLAST, datetime(2026, 10, 5, 16, 35, 46))
    earlier = _old_log(db_session, sender, EmailLogsEmailType.BLAST, datetime(2026, 10, 5, 14, 0, 0))
    never_started = jobs.create_job(db_session, EmailJobsType.BLAST, sender, total=1)
    untouched = never_started.created_at

    _backfill(db_session)

    assert campaign.created_at == campaign.started_at
    assert never_started.created_at == untouched
    assert own.job_id == campaign.id
    assert earlier.job_id is not None and earlier.job_id != campaign.id
    history = db_session.get(EmailJobs, earlier.job_id)
    assert history is not None and history.created_at == earlier.sent_at


def test_manual_certificate_job_gets_the_event_its_log_carries(db_session: Session, seed_refs: Any) -> None:
    sender = seed_refs.ahmed.id
    moment = datetime(2026, 10, 13, 14, 9, 8)
    job = _old_job(db_session, sender, EmailJobsType.MANUAL_CERTIFICATE, moment, moment, moment)
    event = Events(**make_event(semester_id=get_semester_by_hijri_code(db_session, 475).id))
    db_session.add(event)
    db_session.commit()
    log = _old_log(db_session, sender, EmailLogsEmailType.MANUAL_CERTIFICATE, moment, event_id=event.id)

    _backfill(db_session)

    assert log.job_id == job.id
    assert job.event_id == event.id


def test_backfill_can_run_twice(db_session: Session, seed_refs: Any) -> None:
    moment = datetime(2026, 8, 1, 12, 0, 0)
    log = _old_log(db_session, seed_refs.ahmed.id, EmailLogsEmailType.DIRECT, moment)

    _backfill(db_session)
    job_id, count = log.job_id, db_session.scalar(select(func.count()).select_from(EmailJobs))
    _backfill(db_session)

    assert job_id is not None and log.job_id == job_id
    assert db_session.scalar(select(func.count()).select_from(EmailJobs)) == count
