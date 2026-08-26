"""Progress tracking for background email sends.

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
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from app.DB import email_jobs as job_queries
from app.DB.main import db_session

logger = logging.getLogger(__name__)


class JobTracker:
    """Records progress against one email_jobs row.

    Writes go through their own session, so a rollback in the job's working
    session can never undo the record of what happened.
    """

    def __init__(self, session, job_id: int):
        self._session = session
        self.job_id = job_id

    def success(self, count: int = 1) -> None:
        job_queries.record_success(self._session, self.job_id, count)

    def failure(self, error: str) -> None:
        job_queries.record_failure(self._session, self.job_id, error)

    @contextmanager
    def recipient(self, label: str) -> Iterator[None]:
        """Wrap one recipient's send. A failure is recorded, not raised.

        The loop continues, so a single unroutable address cannot silently
        cancel everyone after it.
        """
        try:
            yield
        except Exception as exc:
            logger.exception("email job %s failed for %s", self.job_id, label)
            self.failure(f"{label}: {type(exc).__name__}: {exc}")
        else:
            self.success()


class NullTracker(JobTracker):
    """Used when a job runs without a row to write to, so callers need no branch."""

    def __init__(self):
        super().__init__(None, 0)

    def success(self, count: int = 1) -> None:
        return

    def failure(self, error: str) -> None:
        return


@contextmanager
def track(job_id: int | None) -> Iterator[JobTracker]:
    """Mark a job running, hand back a tracker, and close it out at the end.

    An exception escaping the body marks the whole job failed and is re-raised;
    otherwise the final status is derived from the success and failure counts.
    """
    if job_id is None:
        yield NullTracker()
        return

    with db_session() as session:
        job_queries.mark_running(session, job_id)
        tracker = JobTracker(session, job_id)
        try:
            yield tracker
        except Exception as exc:
            job_queries.finish(session, job_id, error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            job_queries.finish(session, job_id)
