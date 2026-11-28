"""Event lifecycle shim for iOS app builds shipped before the lifecycle refactor (#49).

Those builds (1.1.3 and older, on TestFlight) know the old four-value
``events.status``: ``draft | open | active | closed``. The API now says
``draft | published | cancelled`` and derives ``phase`` and the registration
state. An old build shows only events whose status it knows, so without this
shim it would show no events at all.

The two models use the same field name, so the API answers depend on who is
asking. Every app build sends ``X-App-Version`` (``gdg-app`` ``src/api/config.ts``),
and the Website and Admin never do:

- **Responses** (``LegacyAppMiddleware``): for a request from an app build
  older than ``FIRST_LIFECYCLE_APP_VERSION``, every event object in a JSON
  answer gets its ``status`` back in the old values (``legacy_event_status``),
  and ``GET /events/open`` keeps only the rows the old ``open_events`` view
  held. The new fields stay in the answer; old builds ignore them.
- **Requests** (the validators in ``app/routers/models.py``, for every caller):
  the old values are accepted on ``PUT /events/{id}/status``, ``POST /events/``
  and ``PUT /events/{id}`` and mapped onto the new model, keeping what each
  old action did (closing an event before its start cancels it).

Remove this module, its middleware line in ``app/main.py`` and the
``legacy_status`` validators once no old build is left (TestFlight builds
expire 90 days after upload). See docs/EVENT_LIFECYCLE_COMPAT.md.
"""

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services import event_lifecycle

logger = logging.getLogger(__name__)

LegacyEventStatus = Literal["draft", "open", "active", "closed"]
LEGACY_ONLY_STATUSES = ("open", "active", "closed")
NEW_STATUSES = ("draft", "published", "cancelled")

# The first app version that reads the new lifecycle. Older builds get the shim.
FIRST_LIFECYCLE_APP_VERSION = (1, 2, 0)
APP_VERSION_HEADER = b"x-app-version"

_FORMS_WITH_REGISTRATION = ("registration", "google")

# An ended event stays `active` this long, as events did when nobody closed
# them: the old admin portal hides attendance on `closed` events, and late
# check-ins and backfills still count after the end.
ENDED_GRACE = timedelta(days=1)


def is_legacy_app(app_version: str | None) -> bool:
    """True for an app build older than ``FIRST_LIFECYCLE_APP_VERSION``.

    No header means not the app (the Website, the Admin, scripts). A header
    that is not a version ("0" from Expo Go or a test build) counts as old.
    """
    if app_version is None:
        return False
    parts = []
    for part in app_version.strip().split(".")[:3]:
        digits = re.match(r"\d+", part)
        if digits is None:
            return True
        parts.append(int(digits.group()))
    version = tuple(parts + [0] * (3 - len(parts)))
    return version < FIRST_LIFECYCLE_APP_VERSION


def legacy_event_status(
    *,
    status: str,
    start_datetime: datetime,
    end_datetime: datetime,
    form_type: str | None,
    registration_status: str | None,
    local_now: datetime,
) -> LegacyEventStatus:
    """The old four-value status an event would have had.

    - ``draft`` stays ``draft``; ``cancelled`` is ``closed``, and so is an event
      that ended more than ``ENDED_GRACE`` ago (``active`` until then).
    - With a registration or Google form: ``open`` while registration is open,
      else ``active`` (the old "registration closed").
    - Without one: ``open`` before the start, ``active`` once it started.
    """
    if status == "draft":
        return "draft"
    if status == "cancelled" or local_now > end_datetime + ENDED_GRACE:
        return "closed"
    if local_now > end_datetime:
        return "active"
    if form_type in _FORMS_WITH_REGISTRATION:
        return "open" if registration_status == "open" else "active"
    return "active" if local_now >= start_datetime else "open"


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _is_event(node: dict) -> bool:
    return (
        node.get("status") in NEW_STATUSES
        and "start_datetime" in node
        and "end_datetime" in node
        and "location_type" in node
    )


def _legacy_status_of(node: dict, form: dict | None, local_now: datetime) -> str:
    start, end = _parse(node.get("start_datetime")), _parse(node.get("end_datetime"))
    if start is None or end is None:
        return node["status"]
    # The registration state rides on the event (lists, /events/{id}, /events/open)
    # or next to it (`/events/{id}/details`). The mutation answers carry neither:
    # those fall back to the schedule, and the old app refetches the details after.
    source = node if "form_type" in node else form
    return legacy_event_status(
        status=node["status"],
        start_datetime=start,
        end_datetime=end,
        form_type=source.get("form_type") if source else None,
        registration_status=source.get("registration_status") if source else None,
        local_now=local_now,
    )


def _rewrite(node: Any, local_now: datetime, form: dict | None = None) -> None:
    if isinstance(node, list):
        for item in node:
            _rewrite(item, local_now)
    elif isinstance(node, dict):
        sibling_form = node.get("form") if isinstance(node.get("form"), dict) else None
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                _rewrite(value, local_now, sibling_form if key == "event" else None)
        if _is_event(node):
            node["status"] = _legacy_status_of(node, form, local_now)


def _in_old_open_view(row: Any) -> bool:
    """The old ``open_events`` view: open events, and running online events without a form."""
    if not isinstance(row, dict):
        return True
    status = row.get("status")
    return status == "open" or (
        status == "active" and row.get("location_type") == "online" and row.get("form_type") == "none"
    )


def to_legacy(payload: Any, path: str, local_now: datetime | None = None) -> Any:
    """Rewrite a decoded JSON answer for an old app build, in place, and return it."""
    now = event_lifecycle.riyadh_now() if local_now is None else local_now
    _rewrite(payload, now)
    if path.rstrip("/") == "/events/open" and isinstance(payload, list):
        payload = [row for row in payload if _in_old_open_view(row)]
    return payload


def _header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class LegacyAppMiddleware:
    """Hands old app builds the old event ``status`` values (see the module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_legacy_app(_header(scope["headers"], APP_VERSION_HEADER)):
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def capture(message: Message) -> None:
            nonlocal start, passthrough
            if message["type"] == "http.response.start":
                content_type = _header(message.get("headers", []), b"content-type") or ""
                if content_type.startswith("application/json"):
                    start = message
                    return
                passthrough = True
            if passthrough or message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = b"".join(chunks)
            try:
                body = json.dumps(
                    to_legacy(json.loads(body), scope["path"]), ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            except Exception:
                logger.warning(
                    "Legacy app rewrite failed for %s; sending the answer unchanged", scope["path"], exc_info=True
                )
            assert start is not None
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode("latin-1")))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, capture)
