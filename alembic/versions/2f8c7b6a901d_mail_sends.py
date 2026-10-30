"""Link email logs to sends and retain subjects and recipient failures.

Revision ID: 2f8c7b6a901d
Revises: 194e7997ddc9
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Connection

revision: str = "2f8c7b6a901d"
down_revision: str = "194e7997ddc9"
branch_labels: None = None
depends_on: None = None

LOG_TYPES = ("event-certificate", "manual-certificate", "event_announcement", "acceptance", "blast", "direct")
JOB_TYPES = ("event-certificate", "manual-certificate", "custom-email", "direct-email", "blast", "acceptance")
JOB_TYPE_FOR_LOG = {"event_announcement": "custom-email", "direct": "direct-email"}


def backfill(connection: Connection) -> None:
    """Only unlinked logs create jobs, so rerunning never duplicates a send.

    Read log metadata rather than the potentially large message bodies. Job
    timestamps historically mixed UTC creation with local finish times. Use
    the specified time window as stored, with the closest finish as the
    deterministic tie breaker for overlapping notification jobs.
    """
    connection.execute(
        sa.text("""
        UPDATE email_logs SET email_type = 'team-notification'
        WHERE email_type = 'blast' AND JSON_CONTAINS_PATH(data, 'one', '$.pipeline')
    """)
    )
    jobs: dict[tuple[str, int | None, int], list[Any]] = defaultdict(list)
    for job in connection.execute(
        sa.text("SELECT id, job_type, event_id, created_by, created_at, finished_at FROM email_jobs")
    ):
        jobs[(job.job_type, job.event_id, job.created_by)].append(job)

    logs = connection.execute(
        sa.text("""
        SELECT id, email_type, event_id, sent_by, sent_at, recipient_count,
               NULLIF(JSON_UNQUOTE(JSON_EXTRACT(data, '$.subject')), 'null') AS subject
        FROM email_logs WHERE job_id IS NULL ORDER BY sent_at, id
    """)
    ).all()
    historical: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    links: list[dict[str, int]] = []
    for log in logs:
        job_type = JOB_TYPE_FOR_LOG.get(str(log.email_type), str(log.email_type))
        candidates = list(jobs.get((job_type, log.event_id, log.sent_by), []))
        if job_type == "team-notification":
            candidates += jobs.get(("blast", log.event_id, log.sent_by), [])
        matching = [
            job
            for job in candidates
            if job.created_at - timedelta(seconds=5)
            <= log.sent_at
            <= (job.finished_at or job.created_at) + timedelta(seconds=60)
        ]
        if matching:
            match = min(
                matching,
                key=lambda job: (abs(((job.finished_at or job.created_at) - log.sent_at).total_seconds()), job.id),
            )
            links.append({"log_id": log.id, "job_id": match.id})
        else:
            historical[(job_type, log.event_id, log.sent_by, log.subject, log.sent_at.date())].append(log)

    for (job_type, event_id, sent_by, subject, _), group in historical.items():
        total = sum(log.recipient_count for log in group)
        started: datetime = min(log.sent_at for log in group)
        finished: datetime = max(log.sent_at for log in group)
        result = connection.execute(
            sa.text("""
            INSERT INTO email_jobs
                (job_type, status, created_by, event_id, subject, total, succeeded, failed, created_at, started_at, finished_at)
            VALUES (:job_type, 'succeeded', :created_by, :event_id, :subject, :total, :total, 0, :started, :started, :finished)
        """),
            dict(
                job_type=job_type,
                created_by=sent_by,
                event_id=event_id,
                subject=subject[:255] if subject is not None else None,
                total=total,
                started=started,
                finished=finished,
            ),
        )
        job_id = result.lastrowid
        assert job_id is not None
        links.extend({"log_id": log.id, "job_id": job_id} for log in group)
    if links:
        connection.execute(
            sa.text("UPDATE email_logs SET job_id = :job_id WHERE id = :log_id AND job_id IS NULL"), links
        )

    connection.execute(
        sa.text("""
        UPDATE email_jobs j JOIN (
            SELECT job_id FROM email_logs WHERE job_id IS NOT NULL GROUP BY job_id
            HAVING SUM(email_type <> 'team-notification') = 0
        ) l ON l.job_id = j.id
        SET j.job_type = 'team-notification'
    """)
    )
    connection.execute(
        sa.text("""
        UPDATE email_jobs j JOIN (
            SELECT job_id, MIN(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(data, '$.subject')), 'null')) AS subject
            FROM email_logs WHERE job_id IS NOT NULL GROUP BY job_id
        ) l ON l.job_id = j.id
        SET j.subject = LEFT(l.subject, 255) WHERE j.subject IS NULL
    """)
    )


def upgrade() -> None:
    op.add_column("email_logs", sa.Column("job_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.create_index("ix_email_logs_job_id", "email_logs", ["job_id"])
    op.create_foreign_key("fk_email_logs_job", "email_logs", "email_jobs", ["job_id"], ["id"], ondelete="SET NULL")
    op.add_column("email_jobs", sa.Column("subject", sa.String(255), nullable=True))
    op.add_column("email_jobs", sa.Column("failures", sa.JSON(), nullable=True))
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum(*LOG_TYPES),
        type_=sa.Enum(*LOG_TYPES, "team-notification"),
        nullable=False,
    )
    op.alter_column(
        "email_jobs",
        "job_type",
        existing_type=sa.Enum(*JOB_TYPES),
        type_=sa.Enum(*JOB_TYPES, "team-notification"),
        nullable=False,
    )
    backfill(op.get_bind())


def downgrade() -> None:
    # Preserve historical jobs and logs. Re-upgrading can relink these jobs,
    # including synthesized history, without creating duplicate sends.
    op.execute("UPDATE email_logs SET email_type = 'blast' WHERE email_type = 'team-notification'")
    op.execute("UPDATE email_jobs SET job_type = 'blast' WHERE job_type = 'team-notification'")
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum(*LOG_TYPES, "team-notification"),
        type_=sa.Enum(*LOG_TYPES),
        nullable=False,
    )
    op.alter_column(
        "email_jobs",
        "job_type",
        existing_type=sa.Enum(*JOB_TYPES, "team-notification"),
        type_=sa.Enum(*JOB_TYPES),
        nullable=False,
    )
    op.drop_constraint("fk_email_logs_job", "email_logs", type_="foreignkey")
    op.drop_index("ix_email_logs_job_id", table_name="email_logs")
    op.drop_column("email_logs", "job_id")
    op.drop_column("email_jobs", "failures")
    op.drop_column("email_jobs", "subject")
