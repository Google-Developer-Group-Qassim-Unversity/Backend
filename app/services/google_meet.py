"""Google Meet links for online events, created on the club's events calendar.

One organizer account owns a Google Calendar for the club's events; a Meet
link is a calendar event on it with a Meet room attached. Logistics presses
"generate a link" on its confirmation, this creates the event and hands the
link back, and the confirmation (and so the published event) carries it.

The account is **not** the Forms account in ``app/services/google_client.py``:
its credentials are ``GOOGLE_MEET_*`` (``docs/ENVIRONMENT_VARIABLES.md``),
and the refresh token is shared with the club's Telegram meetings bot, so it
is never revoked or re-consented from here. Everything is plain HTTPS through
``httpx`` so the test suite's transport recorder sees it, like Notion.

Three Google APIs:

- **Calendar v3** creates, edits and deletes the event, with
  ``conferenceDataVersion=1`` so Calendar makes the Meet room.
- **Meet v2** sets the room up (open to anyone with the link, hosts present,
  attendance report). Best effort: the link works without it, so a refused
  setting is logged, never raised.
- The OAuth token endpoint, for an access token from the refresh token,
  cached in the worker until it is about to expire. No ``scope`` is sent:
  Google refuses the whole refresh for a scope the token was not granted.
"""

import logging
import threading
import time as time_module
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.config import config

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
MEET_API = "https://meet.googleapis.com/v2"
TIMEZONE = "Asia/Riyadh"
TIMEOUT = 20.0

# How the room is set up, as ``(updateMask, config)`` PATCHes in this order:
# moderation must be on before any restriction under it. Each mask names the
# leaf field, which is what the API accepts. Nothing is recorded or transcribed:
# an event is public, and the recording would land in the organizer's Drive.
MEET_SETTINGS: tuple[tuple[str, dict], ...] = (
    ("config.moderation", {"moderation": "ON"}),
    (
        "config.moderationRestrictions.presentRestriction",
        {"moderationRestrictions": {"presentRestriction": "HOSTS_ONLY"}},
    ),
    (
        "config.moderationRestrictions.chatRestriction",
        {"moderationRestrictions": {"chatRestriction": "NO_RESTRICTION"}},
    ),
    (
        "config.moderationRestrictions.reactionRestriction",
        {"moderationRestrictions": {"reactionRestriction": "NO_RESTRICTION"}},
    ),
    (
        "config.moderationRestrictions.defaultJoinAsViewerType",
        {"moderationRestrictions": {"defaultJoinAsViewerType": "OFF"}},
    ),
    # Anyone with the link joins without knocking: members are not in the organizer's Workspace.
    ("config.accessType", {"accessType": "OPEN"}),
    ("config.attendanceReportGenerationType", {"attendanceReportGenerationType": "GENERATE_REPORT"}),
    (
        "config.artifactConfig.recordingConfig.autoRecordingGeneration",
        {"artifactConfig": {"recordingConfig": {"autoRecordingGeneration": "OFF"}}},
    ),
    (
        "config.artifactConfig.transcriptionConfig.autoTranscriptionGeneration",
        {"artifactConfig": {"transcriptionConfig": {"autoTranscriptionGeneration": "OFF"}}},
    ),
    (
        "config.artifactConfig.smartNotesConfig.autoSmartNotesGeneration",
        {"artifactConfig": {"smartNotesConfig": {"autoSmartNotesGeneration": "OFF"}}},
    ),
)


class MeetError(RuntimeError):
    """Google refused a call. ``str()`` is Google's message, short enough to log; never shown with a token."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Google {status}: {message}")
        self.status = status


@dataclass
class Schedule:
    """When the event happens: every day from ``start_date`` to ``end_date``, ``start_time`` to ``end_time``."""

    start_date: date
    end_date: date
    start_time: time
    end_time: time

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


@dataclass
class Meeting:
    event_id: str
    meet_url: str
    calendar_url: str | None = None
    # The room settings Google refused, as "mask: reason"; empty when every one applied.
    settings_failed: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- auth

_token_lock = threading.Lock()
_token: tuple[str, float] | None = None  # (access token, expiry as time.monotonic())


def _access_token() -> str:
    global _token
    with _token_lock:
        if _token and _token[1] > time_module.monotonic():
            return _token[0]
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "client_id": config.GOOGLE_MEET_CLIENT_ID,
                    "client_secret": config.GOOGLE_MEET_CLIENT_SECRET,
                    "refresh_token": config.GOOGLE_MEET_REFRESH_TOKEN,
                    "grant_type": "refresh_token",
                },
            )
        payload = _payload(response)
        if response.status_code >= 400 or "access_token" not in payload:
            raise MeetError(response.status_code, payload.get("error_description") or payload.get("error") or "token")
        # A minute early, so a token never expires between two calls of one operation.
        _token = (payload["access_token"], time_module.monotonic() + float(payload.get("expires_in", 3600)) - 60)
        return _token[0]


def forget_token() -> None:
    """Drop the cached access token (tests, or after Google answered 401)."""
    global _token
    with _token_lock:
        _token = None


def _payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _send(method: str, url: str, body: dict | None, params: dict | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_access_token()}"}
    with httpx.Client(timeout=TIMEOUT) as client:
        return client.request(method, url, headers=headers, json=body, params=params)


def _call(method: str, url: str, body: dict | None = None, params: dict | None = None) -> dict[str, Any]:
    """One API call as the organizer, retried once with a fresh token after a 401."""
    response = _send(method, url, body, params)
    if response.status_code == 401:
        forget_token()
        response = _send(method, url, body, params)
    if response.status_code >= 400:
        error = _payload(response).get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise MeetError(response.status_code, message or response.text[:300])
    return _payload(response)


# --------------------------------------------------------------------------- calendar


def _calendar_url(tail: str = "") -> str:
    return f"{CALENDAR_API}/calendars/{quote(config.GOOGLE_MEET_CALENDAR_ID, safe='')}/events{tail}"


def _event_body(title: str, description: str | None, schedule: Schedule) -> dict:
    zone = ZoneInfo(TIMEZONE)
    start = datetime.combine(schedule.start_date, schedule.start_time, zone)
    end = datetime.combine(schedule.start_date, schedule.end_time, zone)
    if end <= start:
        end += timedelta(days=1)
    body: dict[str, Any] = {
        "summary": title,
        "description": description or "",
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
        "reminders": {"useDefault": True},
        "guestsCanModify": False,
        "guestsCanInviteOthers": False,
    }
    # Several days are one series with one link, so the event's time stays the daily time.
    body["recurrence"] = [f"RRULE:FREQ=DAILY;COUNT={schedule.days}"] if schedule.days > 1 else []
    return body


def _meet_url(event: dict) -> str:
    for entry in (event.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return event.get("hangoutLink") or ""


def create_meeting(title: str, description: str | None, schedule: Schedule) -> Meeting:
    """A calendar event with a Meet room, on the club's calendar. Nobody is invited and no email is sent."""
    body = _event_body(title, description, schedule)
    body["conferenceData"] = {
        "createRequest": {"requestId": str(uuid.uuid4()), "conferenceSolutionKey": {"type": "hangoutsMeet"}}
    }
    event = _call("POST", _calendar_url(), body, {"conferenceDataVersion": 1, "sendUpdates": "none"})
    meet_url = _meet_url(event)
    if not meet_url:
        # The event exists without a room; drop it rather than leave a stray entry on the calendar.
        delete_meeting(event.get("id", ""))
        raise MeetError(502, "Calendar created the event without a Meet link")
    meeting = Meeting(event_id=event["id"], meet_url=meet_url, calendar_url=event.get("htmlLink"))
    meeting.settings_failed = apply_settings(meet_url.rstrip("/").rsplit("/", 1)[-1])
    logger.info("Meet %s created on the calendar as event %s", meet_url, meeting.event_id)
    return meeting


def update_meeting(event_id: str, title: str, description: str | None, schedule: Schedule) -> None:
    """Move the calendar event to the confirmed title and times. The Meet link stays the same."""
    _call(
        "PATCH",
        _calendar_url(f"/{quote(event_id, safe='')}"),
        _event_body(title, description, schedule),
        {"conferenceDataVersion": 1, "sendUpdates": "none"},
    )
    logger.info("Meet calendar event %s updated", event_id)


def delete_meeting(event_id: str) -> None:
    """Remove the calendar event; one already gone is fine."""
    if not event_id:
        return
    try:
        _call("DELETE", _calendar_url(f"/{quote(event_id, safe='')}"), params={"sendUpdates": "none"})
    except MeetError as error:
        if error.status not in (404, 410):
            raise
    logger.info("Meet calendar event %s deleted", event_id)


# --------------------------------------------------------------------------- the room


def apply_settings(meeting_code: str) -> list[str]:
    """Set the room up (``MEET_SETTINGS``), best effort. Returns what Google refused, for the log."""
    failed: list[str] = []
    try:
        space = _call("GET", f"{MEET_API}/spaces/{quote(meeting_code, safe='')}")
        name = space.get("name")
        if not name:
            raise MeetError(502, "the Meet space has no name")
    except MeetError as error:
        logger.warning("Meet %s: could not look the room up, settings skipped (%s)", meeting_code, error)
        return [f"space: {error}"]
    for mask, body in MEET_SETTINGS:
        try:
            _call("PATCH", f"{MEET_API}/{name}", {"config": body}, {"updateMask": mask})
        except MeetError as error:
            failed.append(f"{mask}: {error}")
    if failed:
        logger.warning("Meet %s: %d setting(s) refused: %s", meeting_code, len(failed), "; ".join(failed))
    return failed
