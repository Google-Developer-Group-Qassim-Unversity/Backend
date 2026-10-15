"""Record the HTTP requests this service makes to its neighbours.

The backend talks to two other deployables over HTTP:

- **``send-certificates``** (``CERTIFICATE_API_URL``) - blasts, certificate
  emails, certificate generation.
- **the leaderboard app** (``MEMBER_APP_URL``) - the Next.js data-cache reset
  in ``app/leaderboard_cache.py``.

Neither runs under pytest, and the existing suite works around that with
``monkeypatch.setattr(email_jobs, "call_blast_api", ...)``. That replaces the
request-building code along with the transport, so the thing most likely to
break between two repositories - the wire format - is exactly what those tests
cannot see. ``email_gateway.call_blast_api`` already carries a comment about a
422 caused by httpx serializing ``None`` into an empty string; a
function-level stub would never have caught it.

This intercepts one level lower, at the httpx transport, so the request that
*would* have gone out is recorded whole and can be asserted on.

Three interception points, because the outbound calls are made three ways:

===========================================  ==============================
call                                         client
===========================================  ==============================
``call_acceptance_api``, ``call_blast_api``  the process-wide async client
``call_custom_email_api``,                   in ``app/clients.py``
``call_direct_email_api``
``call_certificate_api``,                    a fresh ``httpx.Client`` per
``call_certificate_download``,               call
``reset_leaderboard_cache``
``download_certificate``'s file fetch        module-level ``httpx.get``
===========================================  ==============================

That table is the whole outbound surface of the service. If a new call
appears that none of the three covers, ``_default_response`` raises rather
than letting the test pass on a request nobody looked at.

The default responses mirror what ``send-certificates`` actually returns
today (``app/routers/{blasts,emails,generations}.py`` in that repository).
When those payloads change they have to change here too - and the requests
recorded by these tests are the fixtures that repository's own suite should
be fed, which is what makes the two halves of the contract meet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RecordedRequest:
    """One outbound request, captured before it left the process."""

    method: str
    url: httpx.URL
    headers: httpx.Headers
    content: bytes

    @property
    def path(self) -> str:
        return self.url.path

    @property
    def params(self) -> httpx.QueryParams:
        """Query parameters. Use ``params.get_list(name)`` for repeated keys -
        ``emails`` is sent once per recipient."""
        return self.url.params

    @property
    def text(self) -> str:
        return self.content.decode()

    @property
    def json(self) -> Any:
        return json.loads(self.content)

    def __repr__(self) -> str:  # shows up in failure output, so make it readable
        return f"<{self.method} {self.url}>"


@dataclass
class _Stub:
    status_code: int = 200
    json_body: Any = None
    content: bytes | None = None
    exc: Exception | None = None


@dataclass
class OutboundRecorder:
    """Records every outbound request and answers it with a canned response.

    Assert with :meth:`to` / :meth:`one`; override a response with
    :meth:`stub` when the test is about what happens when a neighbour fails.
    """

    calls: list[RecordedRequest] = field(default_factory=list)
    _stubs: dict[str, _Stub] = field(default_factory=dict)

    # ---------- inspection ----------

    def to(self, path: str) -> list[RecordedRequest]:
        """Every recorded request to ``path``, in order."""
        return [call for call in self.calls if call.path == path]

    def one(self, path: str) -> RecordedRequest:
        """The single recorded request to ``path``; fails if there isn't exactly one."""
        matches = self.to(path)
        assert len(matches) == 1, "\n".join(
            [
                "\nAssertion failed:",
                f"\tExpected: exactly one outbound request to {path}",
                f"\tActual:   {len(matches)}",
                f"\tAll outbound requests: {self.calls or '(none)'}",
            ]
        )
        return matches[0]

    @property
    def paths(self) -> list[str]:
        return [call.path for call in self.calls]

    # ---------- stubbing ----------

    def stub(
        self,
        path: str,
        *,
        status_code: int = 200,
        json: Any = None,
        content: bytes | None = None,
        exc: Exception | None = None,
    ) -> None:
        """Answer ``path`` with something other than the default.

        ``exc`` raises instead of responding, for the connection-failure paths
        ``email_gateway`` maps onto ``ServiceUnavailable``.
        """
        self._stubs[path] = _Stub(status_code=status_code, json_body=json, content=content, exc=exc)

    # ---------- transport ----------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            RecordedRequest(method=request.method, url=request.url, headers=request.headers, content=request.content)
        )

        stub = self._stubs.get(request.url.path)
        if stub is None:
            return _default_response(request)
        if stub.exc is not None:
            raise stub.exc
        if stub.content is not None:
            return httpx.Response(stub.status_code, content=stub.content)
        return httpx.Response(stub.status_code, json=stub.json_body)


# Bytes that stand in for a rendered certificate. Not a real PDF - nothing
# under test parses it, it only has to survive the round trip intact.
CERTIFICATE_FILE_BYTES = b"%PDF-1.4 fake certificate"

# Where the stubbed generation endpoint claims it put the file. Tests assert
# the backend fetched exactly this.
CERTIFICATE_FILE_URL = "https://certificates.test/generated/certificate.pdf"


def _body(request: httpx.Request) -> dict:
    try:
        return json.loads(request.content or b"{}")
    except json.JSONDecodeError:
        return {}


def _default_response(request: httpx.Request) -> httpx.Response:
    """What ``send-certificates`` and the leaderboard app really return."""
    path = request.url.path

    # send-certificates: app/routers/blasts.py
    if path == "/blasts":
        emails = request.url.params.get_list("emails")
        if not emails:
            # `emails` is a required Query param over there, and httpx omits an
            # empty list from the query string entirely rather than sending an
            # empty value - so "send a blast to nobody" arrives as a request
            # with no `emails` key at all and is rejected before the handler
            # runs. Reproduced here because the backend has no guard against
            # making that call (see the repeat-send step in
            # tests/journeys/test_member_lifecycle.py).
            return httpx.Response(
                422, json={"detail": [{"type": "missing", "loc": ["query", "emails"], "msg": "Field required"}]}
            )
        return httpx.Response(200, json={"status": "sent", "recipients": len(emails)})

    # send-certificates: app/routers/emails.py
    if path == "/emails/certificate":
        return httpx.Response(200, json={"status": "sent", "email": _body(request).get("member", {}).get("email")})
    if path in ("/emails/custom", "/emails/direct"):
        return httpx.Response(200, json={"status": "sent", "email": _body(request).get("recipient_email")})

    # send-certificates: app/routers/generations.py
    if path == "/generations/certificate":
        return httpx.Response(200, json={"url": CERTIFICATE_FILE_URL})

    # the generated file itself, fetched by emails.download_certificate
    if str(request.url) == CERTIFICATE_FILE_URL:
        return httpx.Response(200, content=CERTIFICATE_FILE_BYTES, headers={"content-type": "application/pdf"})

    # the leaderboard app: app/api/revalidate/route.ts
    if path == "/api/revalidate":
        return httpx.Response(200, json={"revalidated": True})

    # Notion (app/services/notion_client.py): the design team's board.
    if request.url.host == "api.notion.com":
        return _notion_response(request)

    # Google (app/services/google_meet.py): Meet links on the club's events calendar.
    if request.url.host in ("oauth2.googleapis.com", "www.googleapis.com", "meet.googleapis.com"):
        return _google_meet_response(request)

    raise AssertionError(
        f"Unstubbed outbound request: {request.method} {request.url}\n"
        "Add it to tests/outbound.py:_default_response with the response the real "
        "service returns, or stub it in the test with outbound.stub(...)."
    )


NOTION_PAGE_ID = "11111111-2222-3333-4444-555555555555"
NOTION_PAGE_URL = "https://www.notion.so/Intro-to-ML-11111111222233334444555555555555"


def _notion_response(request: httpx.Request) -> httpx.Response:
    """The shapes Notion's public API (version 2022-06-28) answers with, as far as the sync reads them."""
    path = request.url.path
    if request.method == "GET" and path.startswith("/v1/databases/"):
        return httpx.Response(200, json={"object": "database", "properties": {"Name": {"type": "title"}}})
    if request.method == "PATCH" and path.startswith("/v1/databases/"):
        return httpx.Response(200, json={"object": "database"})
    if request.method == "POST" and path == "/v1/pages":
        return httpx.Response(200, json={"object": "page", "id": NOTION_PAGE_ID, "url": NOTION_PAGE_URL})
    if request.method == "PATCH" and path.startswith("/v1/pages/"):
        return httpx.Response(200, json={"object": "page", "id": path.rsplit("/", 1)[1]})
    if request.method == "GET" and path.endswith("/children"):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "block-ours", "type": "synced_block", "synced_block": {"synced_from": None}},
                    {"id": "block-theirs", "type": "paragraph", "paragraph": {"rich_text": []}},
                ],
                "has_more": False,
            },
        )
    if request.method == "PATCH" and path.endswith("/children"):
        return httpx.Response(200, json={"results": []})
    if request.method == "DELETE" and path.startswith("/v1/blocks/"):
        return httpx.Response(200, json={"object": "block", "archived": True})
    raise AssertionError(f"Unexpected Notion call: {request.method} {request.url}")


MEET_EVENT_ID = "5kq3v0a7l9m2b1c8d4e6f0g2h4"
MEET_CODE = "abc-mnop-xyz"
MEET_URL = f"https://meet.google.com/{MEET_CODE}"
MEET_CALENDAR_URL = "https://www.google.com/calendar/event?eid=NWtxM3Yw"


def _google_meet_response(request: httpx.Request) -> httpx.Response:
    """What Google's OAuth, Calendar v3 and Meet v2 endpoints answer, as far as ``google_meet`` reads them."""
    path = request.url.path
    if request.url.host == "oauth2.googleapis.com" and path == "/token":
        return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3599, "token_type": "Bearer"})
    if request.url.host == "www.googleapis.com" and "/calendar/v3/calendars/" in path:
        if request.method == "POST":
            body = _body(request)
            return httpx.Response(
                200,
                json={
                    "id": MEET_EVENT_ID,
                    "status": "confirmed",
                    "htmlLink": MEET_CALENDAR_URL,
                    "hangoutLink": MEET_URL,
                    "conferenceData": {
                        "conferenceId": MEET_CODE,
                        "entryPoints": [
                            {"entryPointType": "video", "uri": MEET_URL, "label": f"meet.google.com/{MEET_CODE}"}
                        ],
                    },
                    "start": body.get("start"),
                    "end": body.get("end"),
                },
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1], "status": "confirmed"})
        if request.method == "DELETE":
            return httpx.Response(204)
    if request.url.host == "meet.googleapis.com":
        if request.method == "GET" and path.startswith("/v2/spaces/"):
            return httpx.Response(
                200, json={"name": "spaces/jQCFfuBOdN5z", "meetingUri": MEET_URL, "meetingCode": MEET_CODE}
            )
        if request.method == "PATCH" and path.startswith("/v2/spaces/"):
            return httpx.Response(200, json={"name": "spaces/jQCFfuBOdN5z"})
    raise AssertionError(f"Unexpected Google call: {request.method} {request.url}")
