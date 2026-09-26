"""Keeping Google Forms watches alive.

A watch is what makes a form notify us at all: `forms.watches.create` in
`app/routers/forms.py::attach_form` registers one against the Pub/Sub topic,
and every submission sync in this app starts as one of its notifications.

**Watches expire after seven days.** `watches.renew` extends one by another
seven and resets its `expireTime`; once it has actually lapsed, renewing
fails with `NOT_FOUND` and only a fresh `watches.create` brings it back.
Nothing in this app renewed them, so a form
attached more than a week before its event stops notifying with no error
anywhere - strictly worse than the credential expiring, because a dead
credential at least raises. A silent watch just looks like nobody submitted.

That is the whole reason this module exists. `renew_form_watches` is meant to
run daily (`scripts/renew_form_watches.py`, or POST /forms/watches/renew),
comfortably inside the seven-day window so a single missed run is survivable.

Two behaviours worth knowing before changing anything here:

- **An expired watch is recreated, not reported as a failure.** Renew answers
  `NOT_FOUND` once the seven days are up, and the fix is a fresh
  `watches.create` plus writing the new id back to `forms.google_watch_id`.
  Treating that as an error would leave the form permanently silent while the
  sweep kept "successfully" reporting a problem nobody can act on.
- **A dead credential aborts the sweep.** There is one club-wide refresh token
  (see docs/GOOGLE_FORMS.md), so the first `GoogleFormAuthExpired` guarantees
  every remaining form fails the same way. Walking the rest would turn one
  real problem into N identical log lines and N pointless API calls.

The `FormWatches` protocol is the same seam shape as
`app/services/form_responses.py`: a Google adapter for production, a recording
fake for tests, errors mapped onto `app/exceptions.py` so callers never see a
`googleapiclient` type.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.DB import forms as form_queries
from app.config import config
from app.exceptions import GoogleFormAuthExpired, NotFound
from app.services.form_responses import reraise_mapped
from app.services.google_client import get_google_credentials

logger = logging.getLogger(__name__)


class FormWatches(Protocol):
    """What the renewal sweep needs from the Forms API.

    `renew` raises `NotFound` for a watch that has already expired - that is
    the signal to call `create`, not an error to report.
    """

    def renew(self, google_form_id: str, watch_id: str) -> None: ...

    def create(self, google_form_id: str) -> str: ...


class GoogleFormWatches:
    """The production adapter, over the Google Forms REST API."""

    def _service(self) -> Any:
        return build("forms", "v1", credentials=get_google_credentials())

    def renew(self, google_form_id: str, watch_id: str) -> None:
        try:
            self._service().forms().watches().renew(formId=google_form_id, watchId=watch_id).execute()
        except HttpError as exc:
            # An expired watch and a deleted one are both 404 here, and the
            # caller does the same thing for either.
            if getattr(exc.resp, "status", None) == 404:
                raise NotFound("Google form watch", watch_id) from exc
            reraise_mapped(exc, google_form_id)
        except Exception as exc:
            reraise_mapped(exc, google_form_id)

    def create(self, google_form_id: str) -> str:
        try:
            response = (
                self._service()
                .forms()
                .watches()
                .create(
                    formId=google_form_id,
                    body={
                        "watch": {
                            "target": {"topic": {"topicName": config.GOOGLE_FORMS_TOPIC_NAME}},
                            "eventType": "RESPONSES",
                        }
                    },
                )
                .execute()
            )
        except Exception as exc:
            reraise_mapped(exc, google_form_id)
        return response["id"]


@dataclass
class RecordedFormWatches:
    """A fake that records what the sweep did to it. What the tests run against.

    `expired` lists watch ids that answer `renew` with `NotFound`, the way
    Google does once the seven days are up. `fail_renew_with` raises an
    arbitrary exception instead, for the abort and per-form-failure paths.
    """

    expired: set[str] = field(default_factory=set)
    fail_renew_with: Exception | None = None
    next_watch_id: str = "new-watch"
    renewed: list[tuple[str, str]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def renew(self, google_form_id: str, watch_id: str) -> None:
        if self.fail_renew_with is not None:
            raise self.fail_renew_with
        if watch_id in self.expired:
            raise NotFound("Google form watch", watch_id)
        self.renewed.append((google_form_id, watch_id))

    def create(self, google_form_id: str) -> str:
        self.created.append(google_form_id)
        return f"{self.next_watch_id}-{len(self.created)}"


def get_form_watches() -> FormWatches:
    """The adapter the app runs with."""
    return GoogleFormWatches()


def _refresh_one(session: Session, client: FormWatches, form: Any) -> dict[str, Any]:
    """Renew one form's watch, or register a replacement if it has already expired.

    Commits the new id immediately rather than batching to the end of the
    sweep: a watch that is live at Google but unsaved here is the one state
    this must never leave behind, because the next run would create yet
    another one and leak this one until it expires.
    """
    try:
        client.renew(form.google_form_id, form.google_watch_id)
        return {"outcome": "renewed"}
    except NotFound:
        pass

    new_watch_id = client.create(form.google_form_id)
    form_queries.set_form_watch_id(session, form.id, new_watch_id)
    session.commit()
    logger.warning(
        "watch %s for form %s had already expired; registered %s in its place",
        form.google_watch_id,
        form.google_form_id,
        new_watch_id,
    )
    return {"outcome": "recreated", "watch_id": new_watch_id}


def renew_form_watches(session: Session, watches: FormWatches | None = None) -> dict[str, Any]:
    """Renew every registered watch, recreating any that already expired.

    Meant to run daily - well inside the seven-day window, so one missed run
    costs nothing.
    """
    client = watches or get_form_watches()
    renewed = recreated = failed = 0
    aborted_reason: str | None = None
    results: list[dict[str, Any]] = []

    watched = [f for f in form_queries.get_forms(session) if f.google_form_id and f.google_watch_id]
    logger.info("renewing %d google form watches", len(watched))

    for form in watched:
        entry: dict[str, Any] = {"form_id": form.id, "google_form_id": form.google_form_id}
        try:
            entry.update(_refresh_one(session, client, form))
        except GoogleFormAuthExpired as exc:
            # One club-wide credential: every remaining form fails identically,
            # so walking the rest buys nothing but noise and API calls.
            aborted_reason = str(exc.detail)
            entry["outcome"] = "aborted"
            results.append(entry)
            break
        except Exception as exc:
            session.rollback()
            failed += 1
            entry["outcome"] = "failed"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)
            logger.exception("renewing watch for form %s failed", form.google_form_id)
            continue

        results.append(entry)
        if entry["outcome"] == "renewed":
            renewed += 1
        else:
            recreated += 1

    if aborted_reason:
        logger.error(
            "watch renewal aborted after %d forms: %s. Re-mint GOOGLE_REFRESH_TOKEN "
            "(scripts/setup_google_oauth.py) and run this again.",
            len(results) - 1,
            aborted_reason,
        )

    logger.info("watch renewal finished: %d renewed, %d recreated, %d failed", renewed, recreated, failed)
    return {
        "renewed": renewed,
        "recreated": recreated,
        "failed": failed,
        "aborted_reason": aborted_reason,
        "results": results,
    }
