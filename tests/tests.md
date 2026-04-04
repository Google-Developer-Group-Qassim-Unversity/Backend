

| Aspect | Pattern Used |
|---|---|
| Test DB | Real MySQL 8.0 via testcontainers (or `DATABASE_URL` env var for CI) |
| Schema setup | Alembic migrations (`command.upgrade`), not `create_all` |
| Test isolation | Per-function transaction + rollback; `SessionLocal` is reconfigured in-place |
| Auth bypass | FastAPI `app.dependency_overrides[admin_guard]` with fake credentials |
| Test client | `fastapi.testclient.TestClient` (synchronous) |
| Data factories | Simple `make_*()` functions with **overrides pattern** |
| Custom assertions | `assert_2xx()`, `assert_forbidden()`, `assert_existing()` in `tests/utils.py` |
| Seed data | Session-scoped fixture inserting 2 Actions + 2 Departments (IDs 1-2 / 1-2) |
| Test structure | `tests/routers/` mirrors `app/routers/`; `tests/unit/` exists but is empty |
| No mocking | No `unittest.mock`, `patch`, or `monkeypatch` used anywhere — all tests hit real routes with a real DB |