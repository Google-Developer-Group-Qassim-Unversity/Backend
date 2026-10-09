"""The events pipeline's chores that depend on time passing with nobody clicking.

The rules never wait for this: an expired hold already counts as free when
anything reads the calendar. The sweep only adds the side effects - marking
the draft and telling its team - and later rules add their own.

Running it twice changes nothing the second time: each rule selects only rows
still in the state it moves them out of, under ``FOR UPDATE``.

It runs inside the backend every few minutes (``sweep_forever``, started in
the app's lifespan). The four uvicorn workers each run the loop, and a MySQL
named lock lets only one of them sweep at a time. A super admin can also run
it once by hand: ``POST /pipeline/sweep``.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from app.DB import email_jobs as job_queries
from app.DB import event_pipeline as pipeline_queries
from app.DB.main import db_session
from app.DB.schema import (
    EmailJobsType,
    EventRequests,
    EventRequestStage,
    EventRequestUndatedReason,
    PipelineHistoryAction,
    PipelineNotificationKind,
)
from app.services import event_pipeline
from app.services import event_pipeline_clock as clock
from app.services import pipeline_history as history
from app.services import pipeline_notifications as notifications
from app.services.pipeline_notifications import PendingEmail, send_pipeline_email_job

logger = logging.getLogger(__name__)

SWEEP_EVERY_SECONDS = 300
LOCK_NAME = "sweep"


@dataclass
class SweepResult:
    expired_holds: int = 0
    penalties_grown: int = 0
    emails: list[PendingEmail] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {"expired_holds": self.expired_holds, "penalties_grown": self.penalties_grown}


def expire_holds(session: Session, now: datetime, result: SweepResult) -> None:
    """A draft whose 24-hour hold ran out loses its dates; its team is told to book again."""
    rows = session.scalars(
        select(EventRequests)
        .where(
            EventRequests.stage == EventRequestStage.DRAFT,
            EventRequests.hold_expires_at.is_not(None),
            EventRequests.hold_expires_at <= now,
        )
        .options(selectinload(EventRequests.requester), selectinload(EventRequests.department))
        .with_for_update()
    ).all()
    for request in rows:
        lost = {"start_date": str(request.start_date), "end_date": str(request.end_date)}
        request.start_date = None
        request.end_date = None
        request.hold_expires_at = None
        request.undated_reason = EventRequestUndatedReason.HOLD_EXPIRED
        notifications.notify(session, request.department_id, request, PipelineNotificationKind.HOLD_EXPIRED, lost)
        history.record(session, PipelineHistoryAction.HOLD_EXPIRED, None, request, lost, at=now)
        # Nobody clicked anything, so the trial copy goes to whoever booked it.
        email = notifications.department_email(
            session, request.department_id, request, PipelineNotificationKind.HOLD_EXPIRED, request.requester
        )
        if email:
            email.clicked = False
            result.emails.append(email)
        result.expired_holds += 1
    session.flush()


def grow_penalties(session: Session, now: datetime, result: SweepResult) -> None:
    """A returned request still not fixed after its 12 hours: its penalty grows once per late day."""
    rows = session.scalars(
        select(EventRequests)
        .where(
            EventRequests.stage == EventRequestStage.RETURNED,
            EventRequests.return_due_at.is_not(None),
            EventRequests.return_due_at < now,
        )
        .with_for_update()
    ).all()
    for request in rows:
        before = event_pipeline.get_penalty(session, request)
        days_before = before.late_days if before else 0
        penalty = event_pipeline.record_penalty(session, request, now)
        if penalty is not None and penalty.late_days != days_before:
            history.record(
                session,
                PipelineHistoryAction.PENALTY_GROWN,
                None,
                request,
                {"late_days": penalty.late_days, "points": penalty.points},
                at=now,
            )
            result.penalties_grown += 1


RULES = [expire_holds, grow_penalties]


def run_sweep(session: Session, now: datetime | None = None) -> SweepResult:
    """Apply every rule once. The caller commits."""
    result = SweepResult()
    now = now or clock.now()
    for rule in RULES:
        rule(session, now, result)
    return result


def sweep_once() -> SweepResult | None:
    """One sweep in its own session, if no other worker is sweeping. None when another one is."""
    with db_session() as session:
        # Held until the commit below; another worker sweeping skips this round.
        if not pipeline_queries.lock_pipeline(session, LOCK_NAME, wait=False):
            return None
        result = run_sweep(session)
        session.commit()
        for email in result.emails:
            job = job_queries.create_job(session, EmailJobsType.BLAST, email.sent_by_id, total=len(email.emails))
            email.data["job_id"] = job.id
        session.commit()
    logger.info("Pipeline sweep: %s", result.counts())
    return result


async def send_emails(result: SweepResult) -> None:
    for email in result.emails:
        try:
            await send_pipeline_email_job(email, email.data.get("job_id"))
        except Exception:
            logger.exception("Pipeline sweep email failed (%s)", email.subject)


async def sweep_forever() -> None:
    while True:
        try:
            result = await run_in_threadpool(sweep_once)
            if result is not None:
                await send_emails(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pipeline sweep failed")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
