"""Reading a Google Form's schema and its responses, behind one interface.

The Forms client used to be built inline in `app/routers/submissions.py`, so
nothing could stand in for it. The only part of the sync a test could reach was
`extract_email_answer`; the matching, updating and committing around it went
unexercised, which is where the bugs actually live.

Two adapters satisfy `FormResponses`, which is what makes this a real seam
rather than a hypothetical one:

- `GoogleFormResponses` talks to the Forms REST API. What the app runs with.
- `RecordedFormResponses` replays payloads captured from that API. What the
  tests run with.

Failures are raised, never returned as `None`. `fetch_form_responses` used to
catch everything and return `None`, which left `sync_form_submissions` able to
report only a generic `RuntimeError` that no log line traced back to a cause.
The mapping onto `app/exceptions.py` mirrors `app/services/email_gateway.py`:
this module owns it, so callers never see a `googleapiclient` error.

Every form is read with the same club-owned credentials
(`app/services/google_client.py`) - there is no per-form token anymore (see
docs/GOOGLE_FORMS.md). `FormAccess` still exists and still travels through
`app/services/form_sync.py` because a form id is what both adapters key off
of; it just no longer carries a secret.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Any, NoReturn, Protocol

from fastapi import Depends
from google.auth.exceptions import GoogleAuthError, RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.exceptions import BadGateway, GoogleFormAuthExpired, NotFound, ServiceUnavailable
from app.services.google_client import get_google_credentials

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormAccess:
    """Which Google form to read. Just an id - see the module docstring."""

    google_form_id: str


class FormResponses(Protocol):
    """What the sync needs from Google Forms.

    Two methods, both keyed by a `FormAccess`. Errors are the other half of the
    interface: a missing form raises `NotFound`, dead credentials raise
    `GoogleFormAuthExpired`, and anything else upstream raises `BadGateway` or
    `ServiceUnavailable`.
    """

    def list_responses(self, access: FormAccess) -> list[dict]: ...

    def get_schema(self, access: FormAccess) -> dict: ...


class GoogleFormResponses:
    """The production adapter, over the Google Forms REST API."""

    def _service(self) -> Any:
        return build("forms", "v1", credentials=get_google_credentials())

    def list_responses(self, access: FormAccess) -> list[dict]:
        try:
            service = self._service()
            result = service.forms().responses().list(formId=access.google_form_id).execute()
        except Exception as exc:
            _reraise_mapped(exc, access.google_form_id)
        responses = result.get("responses", [])
        logger.info("form %s returned %d responses", access.google_form_id, len(responses))
        return responses

    def get_schema(self, access: FormAccess) -> dict:
        try:
            service = self._service()
            return service.forms().get(formId=access.google_form_id).execute()
        except Exception as exc:
            _reraise_mapped(exc, access.google_form_id)


@dataclass
class RecordedFormResponses:
    """Replays captured Google payloads. The adapter tests run against.

    Anything not recorded raises `NotFound`, the same way Google would for a
    form id it does not know, so a test cannot pass on a form it never set up.
    """

    responses: dict[str, list[dict]] = field(default_factory=dict)
    schemas: dict[str, dict] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def list_responses(self, access: FormAccess) -> list[dict]:
        self.calls.append(access.google_form_id)
        if access.google_form_id not in self.responses:
            raise NotFound("Google form", access.google_form_id)
        return self.responses[access.google_form_id]

    def get_schema(self, access: FormAccess) -> dict:
        if access.google_form_id not in self.schemas:
            raise NotFound("Google form", access.google_form_id)
        return self.schemas[access.google_form_id]


def _reraise_mapped(exc: Exception, google_form_id: str) -> NoReturn:
    """Turn a Google client failure into one of this app's exceptions.

    Anything unrecognised is re-raised untouched rather than flattened into a
    generic 502 - a bug in this module should not come back looking like Google
    having a bad day.
    """
    if isinstance(exc, RefreshError):
        raise GoogleFormAuthExpired(google_form_id) from exc

    if isinstance(exc, HttpError):
        status_code = getattr(exc.resp, "status", None)
        if status_code == 404:
            raise NotFound("Google form", google_form_id) from exc
        if status_code in (401, 403):
            raise GoogleFormAuthExpired(google_form_id) from exc
        raise BadGateway(detail=f"Google Forms API returned error: {status_code}") from exc

    if isinstance(exc, TransportError):
        raise ServiceUnavailable(detail="Failed to connect to the Google Forms API") from exc

    if isinstance(exc, GoogleAuthError):
        raise GoogleFormAuthExpired(google_form_id) from exc

    raise exc


def get_form_responses() -> FormResponses:
    """The adapter the app runs with.

    A FastAPI dependency so tests substitute it through
    `app.dependency_overrides`, the same shape as `R2Client` in
    `app/clients.py`. The sync itself runs in `BackgroundTasks`, where
    dependency injection no longer reaches, so the route resolves the adapter
    and passes it into the task - which is also what lets a test drive the
    whole webhook-to-submission path through one override.
    """
    return GoogleFormResponses()


FormResponsesClient = Annotated[FormResponses, Depends(get_form_responses)]
