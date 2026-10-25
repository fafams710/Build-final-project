# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Project

API Price & Stock Change Tracker: a FastAPI backend ingests live prices from Finnhub over a single upstream websocket, stores them in Postgres, evaluates user-defined threshold rules, and records triggered alerts. A React dashboard reads the current state from the API when the user clicks Refresh. There is no live push to the browser.

Postgres is the source of truth. Watchlist, rules, latest prices and alerts all live in Postgres; nothing the user cares about exists only in memory. Serving reads from Postgres, never from Finnhub, keeps the app inside the Finnhub free tier's budget of 60 API calls per minute.

The design follows `revised-planning.md`. Webhooks, Redis and TimescaleDB have been removed; the database is plain Postgres 16. The `watchlist` and `alerts` tables exist: creating a rule adds its symbol to the watchlist in the same transaction (capped by `WATCHLIST_MAX_SYMBOLS`, 409 beyond it), a fired rule is queued into `alerts` (and logged at INFO), and `GET /api/alerts` plus the dashboard Alerts section with a Refresh button show them. Alerts keep their own rule_type and threshold and survive rule deletion. At startup, symbols of existing rules are backfilled into the watchlist. The rest of the migration is pending: browser websockets (`routers/ws.py`, `connection_manager.py`, `subscription_manager.py`, `useWebSocket.ts`) and `routers/symbols.py` still exist, so during the transition the upstream subscription set is the watchlist plus browser subscriptions (a browser leaving never unsubscribes a watched symbol). The `latest_prices` table and the `/api/watchlist` and `/api/prices` endpoints do not exist yet; watched symbols cannot be removed through the API. Rules are Postgres-first: the rule endpoints write and read Postgres and return 503 when it is unavailable, while the app itself keeps running after a successful start. Startup is fail-loud: with `RULES_PERSISTENCE_ENABLED=true` (default) Postgres is required, and the app refuses to start (lifespan raises, error log line, nothing upstream started) if Postgres is unreachable, `DATABASE_URL` is empty, or loading rules/watchlist or the watchlist backfill fails. `RULES_PERSISTENCE_ENABLED=false` is the explicit opt-out: startup skips those loads, starts with an empty watchlist and rule set, and rule/alert endpoints answer 503. `TICKS_PERSISTENCE_ENABLED` only governs the tick writer and never fails startup. In `docker-compose.yml` the backend waits for the postgres `pg_isready` healthcheck.

How upstream subscriptions are decided (`main.py`): `Services._upstream_sent` records the symbols an upstream subscribe was actually sent for. Every path (browser subscribe/unsubscribe via `SubscriptionManager`, rule creation via `watch_symbol`) changes its own state and then calls `_reconcile(symbol)`, which, under `_upstream_lock`, subscribes if the symbol is wanted (in the watchlist or held by a browser) and not sent, and unsubscribes if sent and no longer wanted. A failed subscribe is not recorded (retried at the next reconcile of that symbol); a failed unsubscribe stays recorded. On every (re)connect, `FinnhubClient` resubscribes `_symbols_for_connect()`, which also resets `_upstream_sent` to that set. Lock order is `SubscriptionManager` lock, then `_upstream_lock`; never await one path inline while another task holds `_upstream_lock` in a test, it deadlocks. While disconnected, `FinnhubClient._send` silently does nothing, so a reconcile then records the symbol as sent; the reconnect resubscribe covers it.

Alert writer (`db.py`): `on_tick` only calls `enqueue_alert` (`put_nowait`, 1000-slot queue; when full it drops the alert and logs one "alert queue full" warning per `ALERT_DROP_WARN_INTERVAL_SECONDS` (10 s) with the count of drops since the previous warning). The writer inserts batches of up to 200; if a batch fails it retries row by row, logs one summary line (`N of M alerts failed`), and sleeps `ALERT_FAILURE_BACKOFF_SECONDS` (1 s) only if a row still failed. Alerts that still fail after the row retry are dropped, not requeued (accepted: an outage must not grow memory or block ingestion). An alert whose row cannot be built is logged with traceback and dropped; the rest of the batch is still written, and any unexpected writer error is logged (`log.exception`) and the loop continues, so the writer task does not die silently. `Database.close()` first waits up to `ALERT_DRAIN_TIMEOUT_SECONDS` (3 s) for queued alerts, skipped if the writer is already done. `create_app()` calls `_configure_logging()`, which sets the `app` logger to INFO and adds a handler (with `propagate = False`) only if neither `app` nor root has one, so "rule fired: ..." appears in uvicorn and Docker output.

Authentication (T010, single shared key, no accounts): `APP_API_KEY` from `.env` is required; the lifespan checks it first and refuses to start ("startup failed: APP_API_KEY is not set") when it is empty or whitespace, regardless of `RULES_PERSISTENCE_ENABLED`. Every `/api/*` route needs header `X-API-Key`, compared with `secrets.compare_digest` on UTF-8 bytes; missing or wrong gives 401 `{"detail": "invalid or missing API key"}`, before body validation. `/health`, `/docs`, `/redoc` and `/openapi.json` stay open; CORS preflight works without the key. In `create_app`, one parent `APIRouter(dependencies=[Depends(require_api_key)])` (`api`) includes the symbols, rules and alerts routers (which declare full `/api/...` paths); add every new `/api` router to `api`, never to `app`. `tests/test_api_auth_coverage.py` walks `app.routes` and fails if any `/api` route lacks `require_api_key` (T011). `/ws` checks before accepting: the `Origin` header, if present, must be in `CORS_ORIGINS`, and query `key` must match; otherwise the handshake is refused (`close(1008)` before accept, which clients see as HTTP 403) and nothing is registered or subscribed. `security.install_key_mask` (called from `create_app`) adds a logging filter to the `uvicorn`, `uvicorn.error`, `uvicorn.access` and `app` loggers and their handlers that masks `key=...` and the key value (raw and URL-encoded) as `***`. The dashboard asks for the key once, keeps it in `sessionStorage`, sends `X-API-Key` on every call and `?key=` on the websocket, and on a REST 401 clears it and asks again. Open the dashboard at `http://localhost:5173` (it must match `CORS_ORIGINS`; Vite listens on `localhost` only, which may be `::1`, so `127.0.0.1:5173` may not connect). `docker-compose.yml` publishes everything on `127.0.0.1` only: postgres `127.0.0.1:5433->5432`, backend `127.0.0.1:8000`, frontend `127.0.0.1:5173`; the backend requires `APP_API_KEY`. Docker is not installed on the development machine, so the compose changes (healthcheck, bindings) have never been run.

Health and database timeouts (T012): `GET /health` (open, no key) returns exactly `{"status": "ok"|"degraded", "database": "ok"|"down"|"disabled", "upstream": "connected"|"disconnected"|"disabled"}`. `database` is a live `Database.ping()` (`SELECT 1`, `HEALTH_DB_TIMEOUT_SECONDS` = 2 s); "disabled" only when `RULES_PERSISTENCE_ENABLED=false`, otherwise any failure or missing pool is "down". `upstream` comes from the client's `connected`; "disabled" when there is no upstream. HTTP 503 only when database is "down"; an upstream outage gives 200 with status "degraded". `db.py` constants: `DB_COMMAND_TIMEOUT_SECONDS` = 5 (`command_timeout` on the pool), `DB_ACQUIRE_TIMEOUT_SECONDS` = 5 (every `pool.acquire(timeout=...)`; test doubles of `acquire` must accept `timeout=`), `DB_STARTUP_TIMEOUT_SECONDS` = 30 (overall limit on `Services._load_state`). Request-path timeouts surface as 503 through the existing handlers. A stalled Postgres at startup hits the 5 s limit first and logs `startup failed: Postgres setup timed out (query/connect limit 5s); ...`; the 30 s backstop logs `... (startup limit 30s); ...`; other failures keep `startup failed: Postgres unavailable: ...`.

## Known open issues (from T008-T012 reviews)

1. Reconnect resubscribe bypasses `_upstream_lock`: `FinnhubClient._run` sends the resubscribe loop directly. A browser unsubscribe during that loop can be overtaken, leaving an unwanted symbol subscribed until the next reconnect. A wanted symbol is never lost. `_symbols_for_connect` also resets `_upstream_sent` without the lock.
2. A late unsubscribe from the old connection that succeeds after a reconnect can cause a duplicate subscribe (contrived; a real closed websocket raises).
3. Upstream calls in `_reconcile` have no timeout; a stalled send holds `_upstream_lock` and blocks rule creation and browser subscribes.
4. Accepted limitation: alerts that fail during a Postgres outage (after the per-row retry) are dropped, and alerts arriving while the queue is full are dropped; both are documented in code, and the queue-full warning is rate-limited. Drops after the last warning of a burst are only reported at the next drop.
5. Test gaps: no test of a reconnect through the real `FinnhubClient` loop, of `Services.shutdown` ordering, of writer cancellation mid-write, or of the backoff delay itself; the logging test asserts `propagate is False` only when a handler was added.
6. Readability nits: duplicated try blocks in `_reconcile`; hand-copied log format; a configured `app` log level above INFO is silently lowered to INFO.
7. The auth coverage test (T011) relies on FastAPI internals (`effective_candidates`, `starlette_route`) to flatten included routers; an unrecognised route node type resolves to an empty path and is skipped rather than failing, and it only inspects paths starting with `/api` (a router mounted elsewhere is not covered). It checks structure, not live 401 responses.
8. Any non-empty `APP_API_KEY` is accepted (even one character); startup validates `strip()` but the comparison uses the raw value, so a key with surrounding whitespace starts but never matches. No rate limit and no logging of failed key attempts.
9. Key masking covers only the uvicorn and `app` loggers/handlers present when `create_app` runs; root, `websockets` and loggers configured later (custom `--log-config`) are not covered, and only a real-uvicorn manual run proves it.
10. A rejected websocket key is not handled in the dashboard: `useWebSocket` keeps reconnecting (backoff capped at 15 s) until a REST 401 clears the key.
11. Docker has never been run here (not installed): the `pg_isready -h 127.0.0.1` healthcheck, the `127.0.0.1` bindings and compose fail-loud behaviour are unverified.
12. During a Postgres stall the alert writer's row-by-row retry runs each row into the 5 s timeout, so one batch of up to 200 alerts can block the writer for minutes; new alerts are dropped once the queue fills (see issue 4). The 30 s startup backstop is only reached in tests (real stalls hit the 5 s limit first). The command timeout and the 5 s startup path are proven against a stalled real Postgres; the acquire timeout only with stubs.

## Layout

- `backend/app/` - FastAPI app
  - `main.py` - `create_app()` and `Services` (wires everything; lifespan owns the upstream connection)
  - `finnhub_client.py` - the single upstream websocket client (reconnect with backoff, resubscribe on reconnect)
  - `rules.py` - in-memory `RuleStore` (copy of Postgres for evaluation) and edge-triggered `RuleEngine`
  - `db.py` - main data layer: `watchlist`, `latest_prices`, `alert_rules`, `alerts`; schema created at startup with `CREATE TABLE IF NOT EXISTS`; queued, batched writes from the tick path
  - `models.py`, `config.py`
  - `routers/` - `health`, `watchlist`, `prices`, `rules`, `alerts`
- `backend/tests/` - pytest suite; `tests/fakes/fake_finnhub.py` replaces the network, `tests/fakes/fake_db.py` replaces Postgres for fast API tests, `test_rules_postgres.py` runs against a real Postgres
- `frontend/` - React + Vite + TypeScript; `src/api/client.ts`, `src/components/` (Dashboard with Refresh button)
- `.claude/` - agents, the `master-orchestrator` skill, `build-pipeline` workflow, and task state in `.claude/team/`

## API

- `GET /health`
- `GET /api/watchlist`, `POST /api/watchlist`, `DELETE /api/watchlist/{symbol}` - POST and DELETE also subscribe or unsubscribe upstream
- `GET /api/prices` - latest price for every watchlist symbol, from `latest_prices`
- `GET /api/rules`, `POST /api/rules`, `DELETE /api/rules/{id}`
- `GET /api/alerts?limit=` - recent alerts, newest first

## Commands

Backend (run from `backend/`):
- `pip install -r requirements-dev.txt` (runtime pins in `requirements.txt`, test tools in `requirements-dev.txt`; all pinned with `==`)
- `pytest` (no network needed; real-Postgres tests are skipped unless `TEST_DATABASE_URL` points at an empty test database, which CI sets)
- `uvicorn app.main:app --reload` (requires a reachable Postgres and `APP_API_KEY` in `.env`; call the API with header `X-API-Key`)

Frontend (run from `frontend/`):
- `npm install`, `npm run dev`, `npm run build`, `npm test`

Full stack: copy `.env.example` to `.env`, fill it in, then `docker compose up --build`. Services are `postgres` (plain image), `backend`, `frontend`.

## Invariants - do not break

1. Exactly one Finnhub websocket connection (free-tier limit). It is created in the FastAPI lifespan, never in a request handler.
2. Respect the Finnhub free-tier budget of 60 API calls per minute. Request handlers and the dashboard read Postgres, never Finnhub; Refresh must not trigger upstream calls.
3. The upstream subscription set equals the `watchlist` table. No duplicate subscribe; adding a symbol makes one subscribe call, removing it makes one unsubscribe call.
4. The tick path (`Services.on_tick`) must not block on Postgres. Price upserts and alert inserts are queued and written by a worker that coalesces to the newest tick per symbol; a failure must not stop ingestion.
5. Postgres is the source of truth. The in-memory rule store is a copy for evaluation, loaded at startup. A failed rule or watchlist write fails the request. Startup fails loudly if Postgres is unreachable or its startup load fails (unless `RULES_PERSISTENCE_ENABLED=false`, the explicit opt-out).
6. Secrets (`FINNHUB_API_KEY`, `APP_API_KEY`, DB password) come from `.env` only and never appear in logs. Never commit `.env`, never hardcode keys.
7. Tests must be deterministic and never hit real Finnhub (use `fake_finnhub`). Database behaviour is tested against a real Postgres via `TEST_DATABASE_URL` (skipped when unset, always run in CI), with clean tables per test; never point it at a database holding real data.

## Conventions

- PEP 8 for Python; type hints on public functions; async throughout the backend.
- Symbols are normalized via `models.normalize_symbol` at every boundary.
- Add settings in `config.py` and `.env.example` together.
- Do not reintroduce webhooks, Redis, TimescaleDB, or browser websockets without an explicit request.
- Keep changes minimal; do not add files or abstractions the task did not ask for.

## Workflow

- Use `/master-orchestrator` for coding work. Small single-file edits go Direct; anything larger, ambiguous, or security-sensitive goes through the specify -> build -> verify pipeline.
- Do not commit or push unless the user asks. The user reviews the diff and releases it.
