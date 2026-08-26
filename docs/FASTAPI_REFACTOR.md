# The FastAPI refactor

What changed in the backend across the `refactor/fastapi-idioms` branch, and
why. Written after the fact as a reference — for the plan as it evolved during
the work, see the branch's commit history (`git log main..refactor/fastapi-idioms`).

## Why

The backend worked, but it wasn't using FastAPI's own machinery: every route
opened its own database session by hand, auth guards were threaded through as
unused parameters, errors were caught and re-wrapped in every handler instead
of centrally, and half the endpoints had no declared response shape. The result
was boilerplate repeated in every file, an auth layer that couldn't be tested,
and roughly 3,600 lines of routers with zero test coverage.

17 commits, 74 files, +6,937/−3,768 lines.

## Before → after

| | Before | After |
|---|---|---|
| Inline `SessionLocal()` in routes | 96 | 0 |
| Hand-written `except Exception → HTTPException(500)` | 36 | 2 |
| Routes declaring `credentials` at all (50 of them unused) | 71 | 12 |
| Routes with a declared `response_model` | 48/100 | 95/103 |
| Endpoints with **no auth at all** | 6 | 0 |
| Lines in `app/routers/logging.py` (bespoke file logger) | 139 | 0 (deleted) |
| `emails.py` | 1,479 lines, 1 file | 800 lines, split across 6 files |
| Async route handlers blocking the event loop | 8 | 0 |
| `ruff check` errors | 4 | 0 |
| `ruff format` violations | 4 files | 0 |
| pyright errors | 71 | 0 |
| Tests | 145 | 510 |

Routes, tags, and status codes were verified byte-identical against the
pre-refactor baseline after every phase that could have touched them — this
was a behavior-preserving refactor, not a rewrite, except where a phase's own
commit says otherwise.

## What changed, phase by phase

**1. Dependency-injected DB session.** Added `app/dependencies.py` (`get_db` /
`DB`) and `app/DB/main.py`'s lazy `get_engine()`/`get_sessionmaker()`. Deleted
96 inline `with SessionLocal() as session:` blocks and the `tests/conftest.py`
hack that monkey-patched the sessionmaker in place to make tests work.
`get_db` does not commit on success — a `yield` dependency's exit code runs
*after* the response is sent on this FastAPI version, verified empirically, so
a failed commit there would be invisible to the client. Write handlers commit
explicitly.

**2. Central exception handling.** Added `app/error_handlers.py`. Deleted 43
per-route `except Exception: ... raise HTTPException(500, ...)` blocks and the
`except HTTPException: raise` guards that existed only to let real errors
escape them. Unexpected errors now return a generic `Internal server error`
rather than 36 different hand-written messages.

**3. Auth as dependencies.** Moved 50 gate-only `credentials` parameters into
`dependencies=[Depends(admin_guard)]`; added `CurrentMember` for the 10 routes
that need the caller, not just the gate. `config.CLERK_GUARD` was rebuilding a
`ClerkHTTPBearer` (and its JWKS client) on every property access — now a cached
singleton. **Caught here:** swapping a guard parameter for `CurrentMember`
silently downgraded four `/emails` routes from `admin_guard` to
`authenticated_guard` — any signed-in member could have triggered certificate
and blast sends. The test suite stayed green because its fixtures override the
guards; the regression was found by diffing each route's *resolved* dependency
tree, not by a test. `tests/test_route_auth.py` now pins the guard on every
route so this class of bug fails loudly. Also fixed six pre-existing endpoints
that had no auth at all (`/actions` writes, the `/submissions_manual/google/*`
sync routes) and two callers that resolved the acting member by `uni_id`, which
fails for any Clerk signup that isn't uni_id/password.

**4. Router config and response contracts.** Every router now owns its own
`prefix`/`tags` instead of that living in `main.py`. Added `response_model` to
44 endpoints (48/100 → 92/100 at the time). `response_model` *silently drops*
undeclared fields — several of these endpoints are consumed by the leaderboard
app, a separate repository — so `tests/test_response_models.py` parses every
handler's `return {...}` literals and asserts the model covers them. That test
caught a real bug: the model the obvious choice would have reused for
`POST /submissions/{form_id}` was dead code missing `is_invited`, which would
have silently vanished from the response.

**5. Stdlib logging.** Replaced the 139-line bespoke `app/routers/logging.py`
(per-request log directories, a `ContextVar`, ANSI codes written to disk) with
`app/logging_config.py` + `app/middleware.py`: stdout, one line per request
with a request id and the worker pid, `X-Request-ID` echoed on the response and
set as a Sentry tag. Sentry's `LoggingIntegration` is on by default at
`level=INFO`, so every one of these lines is now a breadcrumb on any error
event — under the old system that was zero, because Sentry never saw a file on
disk. See [LOGGING.md](LOGGING.md) for the operational side (where the VPS
logs live, how to trace one request, log rotation).

**6. Settings and injectable clients.** `app/config.py` is now backed by
`pydantic-settings`; `Config` stays as a facade so no call site changed. Pulled
in 11 Wallet environment variables that `wallet_signer.py` and `wallet.py` were
reading directly via `os.getenv`, bypassing the module that claims to own all
configuration. Added `app/clients.py`: `get_r2_client` is a FastAPI dependency,
the shared `httpx.AsyncClient` is opened/closed by the app's `lifespan`. Wallet
identity defaults (`APPLE_TEAM_ID`, `APPLE_PASS_TYPE_ID`,
`GOOGLE_WALLET_ISSUER_ID`) still fall back to real production values if unset —
see the `TODO` in `app/config.py` for why that should eventually change and
what removing it requires.

**7. Split `emails.py`, fixed async, added job visibility.** The 1,479-line
file held 24 routes, their background jobs (as closures capturing route
variables — unreachable from anything outside the handler), request/response
models, and two upstream HTTP clients, all in one file. Split into
`app/routers/emails.py` (routes, 800 lines), `app/routers/email_models.py`,
and `app/services/{email_jobs,email_gateway,email_capacity,email_recipients}.py`.
Verified as a pure move: every definition's AST was diffed against the
pre-split version and confirmed structurally identical.

Separately, 8 async route handlers did blocking database or S3 work directly on
the event loop — strictly worse than declaring them synchronous, since FastAPI
would have run a plain `def` handler in a threadpool automatically.
`tests/test_async_handlers.py` now fails any async handler whose own body
touches a session or query module without `run_in_threadpool`, and any async
handler with nothing to `await` at all.

Finally: every background email job ended in `except Exception:
logger.exception(...)`, with **no per-recipient handler** — one bad address
aborted the whole loop, silently skipping everyone after it. Added an
`email_jobs` table and `app/services/job_tracker.py`: each send creates a job
row, records success/failure per recipient, and closes out as `succeeded` /
`partial` / `failed`. `tracker.recipient(...)` is what stops one failure from
cancelling the rest. Verified against a real MySQL container rather than with
tests (by request): one-fails-rest-succeed correctly reports `partial` with the
other recipients actually sent.

## Code quality pass (after the 7 phases)

- `ruff`'s `select` was `["FAST", "TID252"]` — pyflakes (`F`) was never
  actually enforced, despite ruff running in CI on every push. Added it; fixed
  the 28 dead imports/locals it surfaced (checked individually — two turned out
  to be validation-only calls whose return value was never meant to be used,
  not genuinely dead).
- Fixed a real bug pyright found: `create_member`'s `finally` block read
  `new_member`/`already_exist`, which were never assigned if
  `credentials_to_member_model` raised first — an unrelated `NameError` would
  have masked the actual error.
- Took pyright from 39 errors to 0. Not by suppressing — by fixing the
  underlying mismatch at each site: enum columns assigned a raw string instead
  of the enum member (5 sites — works today because these enums subclass
  `str`, but a bad value would only fail at flush, not at assignment); two
  dual-return-type DB query functions split into four properly-typed ones;
  `MemberProfiles.social_links` was typed `Mapped[Optional[dict]]` when every
  real usage is a list; a PKCS12 loader in `wallet_signer.py` could hand
  `add_signer` a `None` key/cert, or a key type PKCS7 doesn't support, with no
  guard.

## Deliberately left alone

- **Wallet identity defaults.** See the `TODO` in `app/config.py`.
- **Six routers still have no dedicated tests**: `custom.py`, `emails.py`,
  `submissions_manual.py`, and thinner coverage on `health.py`, `wallet.py`,
  `submissions.py` (their historically-risky logic — Google Wallet JWT
  signing, Google Forms email extraction — is covered by
  `test_google_wallet_signer.py` / `test_submissions_sync.py` specifically).
  Decided against writing route-level tests for these: the bugs this refactor
  actually caught came from structural checks (dependency-tree diffs, AST
  field-coverage, auth inventories), not route tests: `git log` shows what
  found what, phase by phase.
- **The three async email jobs still call synchronous SQLAlchemy from
  `async def`.** Fixing this needs sync variants of the gateway calls — a
  behavior change, not a refactor.
- **Stranded background jobs.** `email_jobs` rows left `queued`/`running`
  after a worker restart are never resumed — these are Starlette
  `BackgroundTasks`, not a real queue. `GET /emails/jobs/unfinished` surfaces
  them for manual re-sending. A real task queue is a bigger decision than this
  branch made.
