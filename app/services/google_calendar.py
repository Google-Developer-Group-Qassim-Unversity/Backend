"""The two Google Calendar API calls the team's calendar needs: write an entry, and remove it.

Written as the account that can edit the team's calendar - a person's own
Google account, not the club's - through a refresh token of its own
(``GOOGLE_CALENDAR_REFRESH_TOKEN``, ``calendar.events`` scope only) on an OAuth
client of its own (``GOOGLE_CALENDAR_CLIENT_ID``/``SECRET``). That client is
published, so the token does not expire after 7 days the way the club's
testing client's does (docs/GOOGLE_FORMS.md); ``calendar.events`` is a
sensitive scope, not a restricted one, so publishing needs no verification for
the one account that uses it. ``scripts/setup_google_oauth.py --calendar``
mints the token. Plain REST over httpx rather than ``googleapiclient``, so every
call is a fresh ``httpx.Client`` the test suite's transport recorder sees, like
the Notion calls. Nothing here knows about requests;
``app/services/pipeline_calendar.py`` builds the payloads.

The entry's id is ours, not Google's: writing it is a ``PUT`` to that id,
which also brings back an entry deleted before (Google keeps deleted entries
as ``cancelled``), and an insert with the id only when Google has never seen
it. So a write that half-failed can always be run again.
"""

import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from app.config import config

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3"
TIMEOUT = 20.0

_credentials: Credentials | None = None


class GoogleCalendarError(RuntimeError):
    """Google refused a call. ``str()`` is Google's message, short enough to show."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Google Calendar {status}: {message}")
        self.status = status


def access_token() -> str:
    """The calendar account's access token, refreshed only when it has expired."""
    global _credentials
    if _credentials is None or _credentials.refresh_token != config.GOOGLE_CALENDAR_REFRESH_TOKEN:
        _credentials = Credentials(
            None,
            refresh_token=config.GOOGLE_CALENDAR_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.GOOGLE_CALENDAR_CLIENT_ID,
            client_secret=config.GOOGLE_CALENDAR_CLIENT_SECRET,
        )
    if not _credentials.valid:
        _credentials.refresh(GoogleRequest())
    return _credentials.token or ""


def _events_path(event_id: str | None = None) -> str:
    path = f"{API}/calendars/{quote(config.GOOGLE_CALENDAR_ID or '', safe='')}/events"
    return f"{path}/{event_id}" if event_id else path


def call(method: str, url: str, body: dict | None = None) -> httpx.Response:
    """One API call, retried once after a rate limit. Raises on anything but success or 404/410."""
    headers = {"Authorization": f"Bearer {access_token()}"}
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.request(method, url, headers=headers, json=body)
        if response.status_code in (403, 429) and "rateLimitExceeded" in response.text:
            logger.info("Google Calendar rate limit on %s %s; waiting 2s", method, url)
            time.sleep(2)
            response = client.request(method, url, headers=headers, json=body)
    if response.status_code >= 400 and response.status_code not in (404, 410):
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        raise GoogleCalendarError(response.status_code, message or response.text[:300])
    return response


def put_event(event_id: str, body: dict[str, Any]) -> dict:
    """Write the whole entry under our own id: update it if Google knows it, insert it if not."""
    response = call("PUT", _events_path(event_id), body)
    if response.status_code in (404, 410):
        response = call("POST", _events_path(), {**body, "id": event_id})
        if response.status_code in (404, 410):
            raise GoogleCalendarError(response.status_code, "calendar not found - check GOOGLE_CALENDAR_ID")
    return response.json()


def delete_event(event_id: str) -> None:
    """Take the entry off the calendar. One that is already gone is fine."""
    call("DELETE", _events_path(event_id))
