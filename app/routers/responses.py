"""Response envelopes shared across routers.

`response_model` filters the response to the fields declared here, so a missing
field is silently dropped from the payload rather than raising. Every model in
this package is checked against the literal dicts its routes return by
`tests/test_response_models.py`.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic.types import JsonValue

from app.routers.models import BaseClassModel


class MessageResponse(BaseClassModel):
    """A human-readable confirmation of a completed action."""

    message: str


class DetailResponse(BaseClassModel):
    """Mirrors FastAPI's own error envelope, used by the delete endpoints."""

    detail: str


class StatusResponse(BaseClassModel):
    status: str


class CountsResponse(BaseClassModel):
    """Per-item outcome of a bulk mutation."""

    success: int
    failed: int


class CacheResetResponse(BaseClassModel):
    success: bool
    message: str
    # whatever the leaderboard app's /api/revalidate returns; not our schema
    result: Any = None


class CustomPointsCreatedResponse(BaseClassModel):
    event_id: int
    message: str


class UploadResponse(BaseClassModel):
    url: str


class AttachmentUploadResponse(BaseClassModel):
    url: str
    filename: str | None = None
    content_type: str | None = None
    size: int


class DbCheckResponse(BaseClassModel):
    database: str


class ManualSyncResponse(BaseClassModel):
    """Counts from a manual Google Forms backfill.

    `form_id` is absent when the fetch itself failed, so it defaults to None.
    """

    created: int
    skipped_existing: int
    skipped_no_member: int
    skipped_missing_email: int
    processed: int
    total_fetched: int
    form_id: int | None = None


class WebhookAckResponse(BaseClassModel):
    """Acknowledgement for Google's Pub/Sub push.

    Every field except `status` depends on how far the message got before being
    ignored, so all of them are optional. `job_id` is only set on the
    "received" path, once a sync has actually been queued - see
    GET /submissions/sync-jobs/{job_id} for how it turns out.
    """

    status: Literal["received", "ignored"]
    form_id: str | None = None
    event_type: str | None = None
    message_id: str | None = None
    reason: str | None = None
    job_id: int | None = None


class FormSyncJobModel(BaseClassModel):
    """One Google Forms webhook sync, and how it ended."""

    id: int
    google_form_id: str
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    total: int
    succeeded: int
    failed: int
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class SubmissionResponse(BaseClassModel):
    """One row of `submissions`, mirroring the ORM column for column.

    Before this endpoint declared a response model it serialized the ORM object
    directly, so `is_accepted` and `is_invited` went out as 0/1. They stay ints
    here rather than becoming booleans, to keep the payload byte-identical for
    the member app. `tests/test_response_models.py` asserts the field set still
    covers every column.
    """

    id: int
    form_id: int
    member_id: int
    is_accepted: int
    is_invited: int
    submitted_at: datetime
    submission_type: Literal["none", "partial", "google", "registration"]
    google_submission_id: str | None = None
    google_submission_value: JsonValue | None = None
