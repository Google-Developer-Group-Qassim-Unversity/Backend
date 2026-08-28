"""Progress tracking for background jobs that run after their response is sent.

Two problems this exists to solve.

A background task runs after the response is sent, so an exception has nowhere
to go - the caller already holds its 200. Every job here used to end in
`except Exception: logger.exception(...)`, which meant a failed blast was
invisible unless somebody read the logs.

And the per-recipient loops had no inner handler, so one bad address aborted the
whole run. A blast to 400 people that failed on number 50 silently skipped the
other 350.

`tracker.recipient(...)` fixes the second: a failure is recorded against the job
and the loop moves on.

Built for email_jobs.py originally, but nothing here is email-specific - the
only email-shaped thing was importing `app.DB.email_jobs` directly. `track()`
and `job_boundary()` now take a `JobQueries` adapter instead, so any table
with the same four operations (mark_running / record_success / record_failure
/ finish) can use this. `app.DB.form_sync_jobs` is the second one; see
`FORM_SYNC_JOB_QUERIES` below and `app.routers.submissions`.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

from sqlalchemy.orm import Session

from app.DB import email_jobs, form_sync_jobs
from app.DB.main import db_session

logger = logging.getLogger(__name__)

MarkRunning = Callable[[Session, int], None]
RecordSuccess = Callable[..., None]
RecordFailure = Callable[[Session, int, str], None]
Finish = Callable[..., None]


class _JobQueriesModule(Protocol):
    def mark_running(self, session: Session, job_id: int) -> None: ...
    def record_success(self, session: Session, job_id: int, count: int = 1) -> None: ...
    def record_failure(self, session: Session, job_id: int, error: str) -> None: ...
    def finish(self, session: Session, job_id: int, error: str | None = None) -> None: ...


@dataclass(frozen=True)
class JobQueries:
    """The four operations `track()`/`job_boundary()` need from a job table.

    An adapter, not a table: `EMAIL_JOB_QUERIES` and `FORM_SYNC_JOB_QUERIES`
    below just wrap the matching `app.DB.*` module. Two real adapters is what
    justifies this being a parameter instead of the hardcoded import it used
    to be.
    """

    mark_running: MarkRunning
    record_success: RecordSuccess
    record_failure: RecordFailure
    finish: Finish

    @classmethod
    def from_module(cls, module: _JobQueriesModule) -> "JobQueries":
        return cls(
            mark_running=module.mark_running,
            record_success=module.record_success,
            record_failure=module.record_failure,
            finish=module.finish,
        )


EMAIL_JOB_QUERIES = JobQueries.from_module(email_jobs)
FORM_SYNC_JOB_QUERIES = JobQueries.from_module(form_sync_jobs)


class JobTracker:
    """Records progress against one job row.

    Writes go through their own session, so a rollback in the job's working
    session can never undo the record of what happened.
    """

    def __init__(self, session, job_id: int, queries: JobQueries):
        self._session = session
        self.job_id = job_id
        self._queries = queries

    def success(self, count: int = 1) -> None:
        self._queries.record_success(self._session, self.job_id, count)

    def failure(self, error: str) -> None:
        self._queries.record_failure(self._session, self.job_id, error)

    @contextmanager
    def recipient(self, label: str) -> Iterator[None]:
        """Wrap one item's work. A failure is recorded, not raised.

        The loop continues, so one bad item cannot silently cancel everyone
        after it. Named for its original caller (one email recipient); the
        item can be anything with a label.
        """
        try:
            yield
        except Exception as exc:
            logger.exception("job %s failed for %s", self.job_id, label)
            self.failure(f"{label}: {type(exc).__name__}: {exc}")
        else:
            self.success()


class NullTracker(JobTracker):
    """Used when a job runs without a row to write to, so callers need no branch."""

    def __init__(self):
        super().__init__(None, 0, EMAIL_JOB_QUERIES)  # queries is never called: success/failure below are no-ops

    def success(self, count: int = 1) -> None:
        return

    def failure(self, error: str) -> None:
        return


@contextmanager
def track(job_id: int | None, queries: JobQueries) -> Iterator[JobTracker]:
    """Mark a job running, hand back a tracker, and close it out at the end.

    An exception escaping the body marks the whole job failed and is re-raised;
    otherwise the final status is derived from the success and failure counts.
    """
    if job_id is None:
        yield NullTracker()
        return

    with db_session() as session:
        queries.mark_running(session, job_id)
        tracker = JobTracker(session, job_id, queries)
        try:
            yield tracker
        except Exception as exc:
            queries.finish(session, job_id, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            queries.finish(session, job_id)


@contextmanager
def job_boundary(job_id: int | None, queries: JobQueries) -> Iterator[tuple[JobTracker, Session]]:
    """Pair `track()` with a working session and a uniform failure policy.

    Every job function in `email_jobs.py` used to hand-roll
    `with track(job_id) as tracker, db_session() as session: try: ... except
    Exception as e: session.rollback(); logger.exception(e); raise` itself -
    duplicated five times, and two of those copies dropped the final `raise`,
    which left a job that failed before touching a single recipient recorded
    as SUCCEEDED with nothing sent. Collapsing the boilerplate here means
    there is exactly one place that policy can be gotten wrong.

    `db_session()`'s own `except Exception: session.rollback(); raise` still
    runs on the way out; this only adds the diagnostic log every job function
    used to write for itself.
    """
    with track(job_id, queries) as tracker, db_session() as session:
        try:
            yield tracker, session
        except Exception:
            logger.exception("job %s failed", job_id)
            raise
