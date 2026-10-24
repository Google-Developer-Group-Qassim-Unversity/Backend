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
from app.services import pipeline_calendar
from app.services import pipeline_history as history
from app.services import pipeline_notion as notion
from app.services import wallet_push
from app.services import pipeline_notifications as notifications
from app.services.pipeline_notifications import PendingEmail, send_pipeline_email_job
from app.services.push import cases as push_cases
from app.services.push import delivery as push_delivery
from app.services.push import rank as push_rank
from app.services.push import triggers as push_triggers

logger = logging.getLogger(__name__)

SWEEP_EVERY_SECONDS = 300
LOCK_NAME = "sweep"


@dataclass
class SweepResult:
    expired_holds: int = 0
    penalties_grown: int = 0
    emails: list[PendingEmail] = field(default_factory=list)
    # Requests whose Notion card still has to be written; pushed after the commit.
    notion_requests: list[str] = field(default_factory=list)
    notion_synced: int = 0
    # Requests whose calendar entry still has to be written or removed; pushed after the commit.
    calendar_requests: list[str] = field(default_factory=list)
    calendar_synced: int = 0
    # The member app's pushes (app/services/push/triggers.py): queued here, sent after the commit.
    push_reminders: int = 0
    push_new_events: int = 0
    push_event_live: int = 0
    push_registration_closing: int = 0
    push_weekly_summaries: int = 0
    push_rank: int = 0
    push_cleaned: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "expired_holds": self.expired_holds,
            "penalties_grown": self.penalties_grown,
            "notion_synced": self.notion_synced,
            "calendar_synced": self.calendar_synced,
            "push_reminders": self.push_reminders,
            "push_new_events": self.push_new_events,
            "push_event_live": self.push_event_live,
            "push_registration_closing": self.push_registration_closing,
            "push_weekly_summaries": self.push_weekly_summaries,
            "push_rank": self.push_rank,
            "push_cleaned": self.push_cleaned,
        }


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
        pipeline_calendar.mark(session, request)
        # Sent on behalf of whoever booked it, though nobody clicked anything.
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


def collect_notion_cards(session: Session, now: datetime, result: SweepResult) -> None:
    """A Notion card whose last write failed, or that never got written: retried after the commit."""
    result.notion_requests = notion.pending_request_ids(session)


def collect_calendar_entries(session: Session, now: datetime, result: SweepResult) -> None:
    """A calendar entry whose last write failed, or that a rule above just marked: pushed after the commit."""
    result.calendar_requests = pipeline_calendar.pending_request_ids(session)


RULES = [
    expire_holds,
    grow_penalties,
    collect_notion_cards,
    collect_calendar_entries,
    *push_triggers.SWEEP_RULES,
    *push_cases.SWEEP_RULES,
    *push_rank.SWEEP_RULES,
]


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
    # Outside the sweep's session: each sync has its own, and talks to Notion or Google.
    result.notion_synced = sum(1 for request_id in result.notion_requests if notion.sync_request(request_id))
    result.calendar_synced = sum(
        1 for request_id in result.calendar_requests if pipeline_calendar.sync_request(request_id)
    )
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
        # Every worker, whether or not it swept: delivery claims its own rows (FOR UPDATE SKIP LOCKED).
        await push_delivery.kick()
        # Apple Wallet pass update pushes still owed (a retry, or one whose kick never ran), the same way.
        await wallet_push.kick()
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
