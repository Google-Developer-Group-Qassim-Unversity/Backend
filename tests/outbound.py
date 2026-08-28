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

    raise AssertionError(
        f"Unstubbed outbound request: {request.method} {request.url}\n"
        "Add it to tests/outbound.py:_default_response with the response the real "
        "service returns, or stub it in the test with outbound.stub(...)."
    )
