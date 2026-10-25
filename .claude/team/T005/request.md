# Request (T005)

Make Postgres the source of truth for rules (reviewer feedback: "POST saves to memory first; persistence is best-effort; GET reads memory with no ordering/limit"; plus "point the tests at a real test Postgres, CI already starts one"). Do ONLY this item.

User decisions (binding):
1. Scope: POST, GET and DELETE on /api/rules all go through Postgres.
2. No DB configured / Postgres unreachable: the app keeps running (tick ingestion unaffected), and the rule endpoints return 503.
3. Tests: include real tests against a Postgres database.
4. Old volumes: add a startup schema fix `ALTER TABLE alert_rules DROP COLUMN IF EXISTS webhook_url` so existing rules are kept.

Required behaviour:
- POST /api/rules: INSERT into Postgres first. Only on success add to the in-memory store and return 201. On any DB failure (or no DB) return 503 and leave memory unchanged.
- GET /api/rules: read from Postgres, ORDER BY created_at DESC, `limit` query param (default 50, min 1, max 500; out-of-range -> 422). Keep the optional `symbol` filter (normalized via models.normalize_symbol). DB failure -> 503.
- DELETE /api/rules/{id}: delete in Postgres first; 404 if no row; on success remove from memory, 204; DB failure -> 503.
- db.py: rule methods must raise (not swallow/log) so the routers can map failures to 503. load_rules at startup: if DB unavailable, log and start with an empty in-memory store (app keeps running per decision 2).
- The in-memory RuleStore remains the evaluation copy only (RuleEngine reads it); it is no longer the API's read path.
- Tick path (Services.on_tick) unchanged: must not block on Postgres.

Tests:
- Keep fast offline API tests using an in-memory fake DB injected via create_app(db=...); cover 201, 503 on failing fake (memory unchanged), GET ordering + limit + 422, DELETE 204/404/503.
- Add real-Postgres tests (db.py rule methods + the startup DROP COLUMN fix on a table that still has `webhook_url NOT NULL`, + at least one API round-trip POST->GET->DELETE) reading the URL from env var TEST_DATABASE_URL. When TEST_DATABASE_URL is unset they are skipped with a clear reason; each test starts from clean tables.
- CI (.github/workflows/ci.yml): set TEST_DATABASE_URL to the existing postgres service so the real-DB tests run (not skipped) in CI.
- Tests must never touch Finnhub (keep fake_finnhub).

Out of scope: watchlist/latest_prices/alerts tables, browser websockets, rate limiting, auth, dependency pinning, fail-loud startup. Do NOT edit CLAUDE.md or revised-planning.md (orchestrator updates CLAUDE.md).

Acceptance:
1. `pytest` passes from backend/ with TEST_DATABASE_URL unset (real-DB tests skipped, everything else passes).
2. `pytest` passes with TEST_DATABASE_URL pointing at a real empty Postgres, with the real-DB tests actually running (not skipped).
3. With the backend running and no DB: POST/GET/DELETE /api/rules return 503; /health still 200.
4. With a real DB: POST then restart then GET returns the rule; GET honours ORDER BY created_at DESC and limit.
5. A pre-existing alert_rules table with `webhook_url TEXT NOT NULL` and a stored row: after startup the column is gone and the row is still returned by GET.
6. npm run build and npm test still pass (frontend unchanged unless needed for 503 handling).
