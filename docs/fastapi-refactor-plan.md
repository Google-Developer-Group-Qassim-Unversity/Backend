# Backend refactor plan: adopting FastAPI properly

Audit of `Backend/app` (≈7,700 lines of app code, 13,400 with tests and scripts).
The code works, but almost every route hand-rolls machinery that FastAPI already
provides. The result is boilerplate in every handler, an untestable auth/DB layer,
and 3,600+ lines of routers with zero test coverage.

---

## Part 1 — What's wrong

### 1. No dependency injection for the DB session

96 occurrences of `with SessionLocal() as session:` inline in route bodies
([members.py:39](../app/routers/members.py#L39), [emails.py](../app/routers/emails.py) alone has 29).
Every route opens its own session, and 39 of them call `session.commit()` by hand.

The engine and `SessionLocal` are built at **import time** in
[app/DB/main.py:29](../app/DB/main.py#L29), reading `config.DATABASE_URL` as a module
side effect. Because nothing is injected, tests can't override anything, so
`tests/conftest.py` resorts to mutating the sessionmaker in place:

```python
original_bind = db_main.SessionLocal.kw["bind"]
db_main.SessionLocal.configure(bind=connection)   # global monkey-patch
```

…and every app import has to be deferred inside fixture bodies, with a comment
in conftest apologising for it. That is the single biggest testability blocker.

**FastAPI already has this:** a `get_db` generator dependency plus
`app.dependency_overrides[get_db]`.

### 2. Import-time side effects in config and auth

`config.CLERK_GUARD` is a `@property` that constructs a **new** `ClerkHTTPBearer`
on every access ([config.py:38](../app/config.py#L38)). The guards in
[helpers.py:103-133](../app/helpers.py#L103) then use it as a mutable default
argument (`credentials=Depends(config.CLERK_GUARD)`), evaluated once at import.

Consequences: `CLERK_JWKS_URL` must exist before any module is imported (hence the
env-var block at the top of conftest), three independent JWKS clients exist, and
config errors surface as import crashes rather than startup errors.

### 3. Exception handling copy-pasted into every route

40+ `except Exception:` blocks across the routers and **36** hand-written
`HTTP_500_INTERNAL_SERVER_ERROR` raises. The shape is identical everywhere:

```python
except HTTPException:
    raise
except Exception as e:
    session.rollback()
    write_log_exception(e)
    write_log_traceback()
    raise HTTPException(status_code=500, detail="An error occurred while ...")
```

There's already a well-designed exception hierarchy in
[app/exceptions.py](../app/exceptions.py) (`KnownHttpException`, `NotFound`,
`Conflict`, `MemberNotFound`…) but **no exception handler is registered for it**.
[main.py](../app/main.py) registers handlers only for `OperationalError` and
SQLAlchemy `TimeoutError`.

### 4. Auth dependencies threaded through as unused parameters

**50 of 72** routes declare `credentials: Annotated[HTTPAuthorizationCredentials, Depends(admin_guard)]`
and never reference `credentials` in the body — it's pure gate-keeping noise in the
signature, and it leaks into the OpenAPI schema.

The other 22 immediately do `member = resolve_member(session, credentials)`, which is
itself a repeated three-line dance that should be one dependency.

**FastAPI already has this:** `dependencies=[Depends(admin_guard)]` on the route or
router for the gate-only case, and a `CurrentMember` dependency for the rest.

### 5. Bare `APIRouter()` — prefixes, tags and shared responses live in main.py

All 16 routers are constructed as `APIRouter()` with no arguments, so
[main.py:54-71](../app/main.py#L54) carries 16 lines of `prefix=`/`tags=` wiring,
and `responses={404: {...}}` is copy-pasted onto individual routes
(5× in members.py, 4× in events.py).

`APIRouter(prefix=..., tags=..., dependencies=..., responses=...)` handles all of it.

### 6. `async def` routes doing blocking synchronous work

Six endpoints in [emails.py](../app/routers/emails.py) are `async def` yet open a
**synchronous** SQLAlchemy session inside (lines 710, 759, 800, 1113, 1218, 1316).
[upload.py:44](../app/routers/upload.py#L44) is `async def` and calls blocking
`boto3.put_object`. Each of these stalls the event loop for the duration of the
query or upload.

A plain `def` route would have been run in a threadpool automatically. This is
strictly worse than not using `async` at all.

### 7. A bespoke logging framework instead of `logging`

[app/routers/logging.py](../app/routers/logging.py) is 139 lines implementing
per-request log directories, ANSI colour codes written into files, a `ContextVar`
for the "current" log file, and paired `write_log_x` / `write_log_x_to` variants.
Every route opens it manually: `with LogFile("create member"), SessionLocal() as session:`.
There are also 26 bare `print()` calls in `app/`.

This is middleware's job. Sentry is already wired up and gets none of this context.

### 8. Half the endpoints have no response contract

99 route decorators, **48** `response_model=` declarations, and **53** bare
`return {…}` dict literals. Endpoints like `POST /upload` and the entire certificate
flow publish `{}` as their OpenAPI schema, so the frontend has no generated types and
no response validation runs.

### 9. Business logic lives in the routers

[emails.py](../app/routers/emails.py) is 1,482 lines; [custom.py](../app/routers/custom.py)
is 667. Routes mix HTTP concerns, transaction management, logging, external API calls,
and domain rules. Background jobs are defined as **closures nested inside the route
functions** (emails.py:460, 563, 630, 756, 1215), which makes them unreachable from a test.

### 10. External clients aren't injectable

`get_r2_client()` ([upload.py:22](../app/routers/upload.py#L22)),
`call_certificate_api()`, `call_blast_api()`, and `reset_leaderboard_cache()` are
module-level functions called directly. Nothing can be substituted via
`dependency_overrides`, so tests must reach for `moto` and `unittest.mock.patch`.

Direct consequence — routers with **no tests at all**:

| Router | Lines |
|---|---|
| `emails.py` | 1,482 |
| `custom.py` | 667 |
| `wallet.py` | 427 |
| `submissions.py` | 386 |
| `points.py` | 200 |
| `action.py` | 145 |
| `submissions_manual.py` | 132 |
| `health.py` | 106 |
| `cache.py` | 33 |
| `departments.py` | 29 |

### 11. Long-running email jobs on `BackgroundTasks`

`send_certificates` iterates every attendee, calls an external certificate API
per member (120s timeout each), and commits per iteration — inside a
`BackgroundTasks` callback. It raises `HTTPException` from there, which goes
nowhere. There's already a `TODO` in the code acknowledging this
([emails.py:501](../app/routers/emails.py#L501)). No job record, no status, no retry.

### 12. Hand-rolled settings

[config.py](../app/config.py) is a class with 20 `@property` methods each calling
`env_or_except`. Values are read lazily on every access, never validated, and never
typed beyond `str`. `pydantic-settings` (Pydantic is already a dependency) gives
startup validation, real types, and trivial test overrides.

### 13. No lifespan

No `lifespan` handler anywhere. The engine is never disposed, `httpx` clients are
created and torn down per call rather than pooled, and misconfiguration is
discovered on first request instead of at boot.

---

## Part 2 — The plan

Seven phases. Each is independently shippable and leaves the suite green.
Phases 1–3 are the ones that unlock everything else.

### Phase 1 — Dependency-injected DB session ✅ DONE

**Goal:** delete all 96 inline `SessionLocal()` blocks and the conftest monkey-patch.

**Correction made during implementation.** The original plan had `get_db` commit
on success. That is unsafe on FastAPI 0.136: a `yield` dependency's exit code runs
*after* the response has been sent. Verified empirically — a dependency raising
after `yield` still left the client with `200 {"ok":true}`, so a failed commit
would be silently invisible. `get_db` therefore owns session lifecycle and
rollback only, and write handlers keep an explicit `session.commit()`.

Exceptions *are* thrown into the generator (both `HTTPException` and plain
exceptions), so rollback-on-error works as intended.

What shipped:

1. `app/dependencies.py` — `get_db` plus `DB = Annotated[Session, Depends(get_db)]`.
   The dependency param is named `session`, so route bodies were untouched.
2. `app/DB/main.py` — engine and sessionmaker are now behind `@lru_cache`
   (`get_engine()`, `get_sessionmaker()`), so importing the module has no side
   effects and needs no `DATABASE_URL`. Added a `db_session()` context manager
   for code outside the request cycle.
3. **82 route handlers** converted to take `session: DB`.
4. **19 non-route sites** (background-task closures, SSE generators, module-level
   helpers, 5 backfill scripts) moved to `db_session()` — these correctly must
   *not* share the request session.
5. `tests/conftest.py` — the `SessionLocal.configure(bind=...)` monkey-patch is
   gone, replaced by `app.dependency_overrides[get_db]` over a `db_bind` fixture
   that owns the connection and outer transaction
   (`join_transaction_mode="create_savepoint"`, so route commits work and still
   roll back). The deferred imports are gone; conftest imports `app.main` at
   module level.

**Verified:** `SessionLocal` no longer appears anywhere in `app/`, `scripts/` or
`tests/`. Test suite 144 passed / 1 xfailed before and after. Ruff 4 pre-existing
errors before and after. Pyright 71 errors before and after. No new undefined
names (`ruff --select F821` clean), and an AST check confirmed no nested closure
captures a request-scoped session.

**Left for Phase 2:** the 49 `session.commit()` calls in routers stay (they are
correct and explicit); the `session.rollback()` calls inside `except` blocks are
now redundant with `get_db` and get deleted along with those blocks.

### Phase 2 — Exception handlers ✅ DONE

**Goal:** delete the per-route `except Exception` → `HTTPException(500)` boilerplate.

What shipped:

1. `app/error_handlers.py` with `register_exception_handlers(app)`, covering
   `KnownHttpException`, `OperationalError`, SQLAlchemy `TimeoutError`,
   `IntegrityError` → 409, `SQLAlchemyError` → 500, and a catch-all `Exception`.
   `main.py` calls it and no longer defines handlers inline.
2. **43 handlers removed** across 7 routers and **21 `try` blocks unwrapped**
   entirely. The 19 `except HTTPException: raise` / `session.rollback(); raise`
   guards existed only to let intentional errors escape the `except Exception`
   below them, so they went too.
3. **3 background-task handlers rewritten** to log and re-raise rather than
   construct an `HTTPException` that nothing will ever receive (the code's own
   `TODO` flagged this). Proper job management is still Phase 7.
4. Two 500 wrappers in `upload.py` that returned `f"File upload failed: {str(e)}"`
   are gone — the raw exception message no longer reaches the client.
5. Six "this should never happen" 500s now raise the `DataIntegrityError` the
   codebase already defined for exactly that case.
6. New `tests/test_error_handlers.py` — 9 tests, one per handler, including
   assertions that the driver's message and the original exception text do not
   leak into the response body.

Router `except` blocks went from 100 to 50; hand-written 500s from 36 to 2
(both in `error_handlers.py`); `session.rollback()` in routers from ~40 to 4.

**What deliberately stayed:**

- The four remaining `session.rollback()` calls are in background-task closures
  that *swallow* the exception. Since nothing propagates, `db_session()` never
  rolls back for them — the explicit call is load-bearing. Phase 7 territory.
- `httpx` handlers mapping upstream failures to `GatewayTimeout` / `BadGateway` /
  `ServiceUnavailable`. Those carry real meaning.
- Deliberate swallows, e.g. per-row failure counting in `batch_create_members`
  and the best-effort cache reset.

**Two behaviour changes worth knowing:**

- Unexpected errors now reach the client as `{"detail": "Internal server error"}`
  rather than a route-specific message like "An error occurred while updating
  member". No test or frontend code depended on those strings.
- Starlette re-raises after the catch-all handler runs, so in tests using
  `TestClient(raise_server_exceptions=True)` an unexpected error now surfaces as
  the real exception instead of a silent 500. That is an improvement: the stack
  trace is the actual failure, not a generic wrapper.

**Verified:** 153 passed / 1 xfailed (was 144/1 — the 9 new handler tests).
Ruff 4 errors and pyright 71 errors, both unchanged from baseline. No newly
unused imports (the 7 the strip orphaned were removed; the 33 pre-existing ones
were left alone). `ruff --select F821` clean.

### Phase 3 — Auth as dependencies, not parameters ✅ DONE

**Goal:** remove `credentials` from the signatures that never read it; make
`resolve_member` a dependency.

What shipped:

1. `config.CLERK_GUARD` now returns a cached singleton via `_clerk_bearer()`.
   It was a `@property` building a fresh `ClerkHTTPBearer` on every access, and
   the five guards each read it once at import - five bearers, five JWKS clients
   fetching Clerk's signing keys separately. Now two (one per `auto_error` mode).
   A side effect: `admin_guard` and `authenticated_guard` finally share one
   bearer object, so FastAPI's per-request dependency cache verifies the token
   once instead of twice when both are present.
2. **50 gate-only guards moved** from parameters into `dependencies=[...]` on
   the route decorator. Routes still declaring `credentials`: 71 → 12.
3. **3 routers hoisted** to `APIRouter(dependencies=[Depends(admin_guard)])` -
   `cache.py`, `custom.py` and `upload.py`, where every route is admin-only.
   `emails.py` is 23 admin + 1 authenticated, so it keeps per-route decorators.
4. **10 routes take `member: CurrentMember`**, backed by `get_current_member` in
   `app/helpers.py` composing `authenticated_guard` + `resolve_member`. The
   parameter is named after the local variable it replaced, so bodies read the
   same.
5. conftest's admin/super-admin overrides now return real Clerk-shaped
   credentials (a new `FAKE_SUPER_ADMIN_CREDENTIALS` alongside the others)
   rather than a bare `HTTPAuthorizationCredentials` with no `decoded` payload,
   which would have broken any test of the routes that still read the token.

**A security regression was caught here, not by the tests.** Swapping an
`admin_guard` parameter for `CurrentMember` silently downgraded four `/emails`
routes to `authenticated_guard` — any signed-in member could have triggered
certificate and blast sends. The suite stayed green throughout, because the
fixtures override the guards. It was caught by diffing each route's *resolved
dependency tree* against the pre-phase baseline, and fixed by putting
`dependencies=[Depends(admin_guard)]` back on those four decorators.

That check is now a permanent test: **`tests/test_route_auth.py`** pins the
strictest guard for all 100 routes, so any future change to a route's auth fails
loudly and shows the direction of the change.

**Verified:** every one of the 100 routes enforces a superset of its pre-phase
guards, and none got stricter either. 255 passed / 1 xfailed (was 153/1, plus
101 auth-inventory tests and the parametrised cases). Ruff 4 and pyright 71,
both unchanged. No newly unused imports.

### Phase 3b — Close the auth gaps Phase 3 surfaced ✅ DONE

Both items flagged at the end of Phase 3, fixed in their own commit.

**Six unauthenticated write endpoints, now guarded:**

| Endpoint | Guard | Why |
|---|---|---|
| `POST /actions` | `admin_points_guard` | the frontend gates `/points` to `["admin_points", "super_admin"]` |
| `PUT /actions/reorder` | `admin_points_guard` | same |
| `PUT /actions/{id}` | `admin_points_guard` | same |
| `DELETE /actions/{id}` | `admin_points_guard` | same |
| `POST /submissions_manual/google/{id}` | `admin_guard` | admin-triggered Forms backfill |
| `POST /submissions_manual/google/run/{id}` | `admin_guard` | same |

`admin_points_guard` had been defined in `helpers.py` and used by nothing.
`is_admin_points or is_super_admin` is exactly the frontend's
`["admin_points", "super_admin"]`, so backend and frontend now agree.

`GET /actions` and `GET /actions/all` stay public - the leaderboard app reads them.

**Frontend:** no call-site change was needed. `app/points/manage/page.tsx`
already passed `getToken` to all five mutations, so the token was being sent and
the backend simply never checked it. What did change is `lib/api.ts`, where
`getToken` on those mutations went from `getToken?: GetTokenFn` to
`getToken: GetTokenFn` - forgetting it is now a compile error rather than a
runtime 403. (`deleteAction`'s `replacementId` became
`number | null | undefined` so a required parameter does not follow an optional
one.) `tsc --noEmit` passes; eslint has the same 4 pre-existing warnings.

**Caller resolution:** `send_custom_email`, `send_blast` and
`create_email_template` used
`get_member_by_uni_id(get_uni_id_from_credentials(...))`, which fails for any
member without a `uni_id`. All three now take `requesting_member: CurrentMember`,
with `dependencies=[Depends(admin_guard)]` moved onto the decorator so the admin
gate survives - the exact trap Phase 3 fell into.

`wallet.py`'s `_resolve_authenticated_member` had the same weakness behind a
uni_id → email fallback chain. It now tries `clerk_user_id` first, additively;
both existing fallbacks are untouched.

**Verified:** no route lost a guard; 9 gained one. Public routes 32 → 26. The
auth inventory in `tests/test_route_auth.py` was regenerated (and its strictness
ordering corrected to include `admin_points_guard`, which is narrower than
`admin_guard`). 255 passed / 1 xfailed, ruff 4, pyright 71.

### Phase 4 — Router configuration and response contracts

1. `APIRouter(prefix="/members", tags=["members"], responses={...})` in each
   router file; strip the wiring from `main.py` down to `app.include_router(members.router)`.
2. Add `response_model` to the ~51 endpoints missing it; replace the 53 bare
   `return {...}` with typed Pydantic models.
3. Add a `lifespan` that validates settings and disposes the engine on shutdown.

**Payoff:** the frontend can generate a typed client from the OpenAPI schema.

### Phase 5 — Logging via middleware

1. Replace `app/routers/logging.py` with stdlib `logging` + a `structlog`-style
   JSON formatter (or plain `logging` with a `RequestIdFilter`).
2. Add one middleware that assigns a request id, logs method/path/status/duration,
   and pushes the request id into the Sentry scope.
3. Remove the 26 `print()` calls and every `with LogFile(...)`.
4. Keep the file-per-request behaviour only if it's actually used for debugging;
   if so, implement it as a logging `Handler`, not as a call in each route.

### Phase 6 — Settings and injectable clients

1. Port `config.py` to `pydantic-settings` `BaseSettings`, keeping the same
   attribute names so nothing else changes. Instantiate once at startup.
2. Wrap the external clients as dependencies: `get_r2_client`, `get_certificate_client`,
   `get_leaderboard_client`. Tests then override them instead of patching modules.
3. Create the `httpx.AsyncClient` once in `lifespan` and inject it, instead of
   opening a new client per call in 5 places.

### Phase 7 — Extract services, fix async, add tests

1. Move the nested background-job closures out of `emails.py` into
   `app/services/emails.py` as top-level, injectable functions. Split the
   1,482-line file by concern (certificates / blasts / logs / stats).
2. Fix the async lie: either make the six blocking `async def` endpoints plain
   `def`, or make their DB access genuinely async. `def` is the smaller change
   and is correct.
3. Same for `upload.py` — blocking `boto3` in `async def`.
4. Now that everything is injectable, write route tests for the ten untested
   routers, starting with `emails.py` and `wallet.py`.
5. Replace `BackgroundTasks` for the certificate/blast jobs with a real job
   record (a `jobs` table with status, or a task queue) so failures are visible.

---

## Suggested order and effort

| Phase | Unlocks | Rough size |
|---|---|---|
| 1. DB dependency ✅ | everything else | large, mechanical |
| 2. Exception handlers ✅ | −50 except blocks, −34 manual 500s | medium |
| 3. Auth dependencies ✅ | −59 signature params, auth inventory test | medium |
| 4. Router config + response models | typed frontend client | medium |
| 5. Logging middleware | −139 lines, Sentry context | small |
| 6. Settings + clients | testable externals | small |
| 7. Services + async fix + tests | coverage on 3,600 untested lines | large |

Phases 1–3 are worth doing back to back; they're the ones that make the rest cheap.
