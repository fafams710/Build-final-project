# API Price & Stock Change Tracker

Live stock prices from Finnhub, stored in Postgres, with threshold rules that record an alert the moment a price target or percentage move is hit. A React dashboard shows rules and alerts.

## How it works

```
Finnhub --(1 websocket)--> FastAPI --> Rule engine --> alerts (Postgres) + INFO log line
                              |------> Postgres: rules, watchlist, alerts, tick history
                              '------> Browser websocket --> React dashboard (live ticker)
```

- **One upstream connection.** Finnhub's free tier allows a single websocket. The client starts and stops with the FastAPI lifespan.
- **Postgres is required.** Rules, the watchlist and alerts live in Postgres; the backend refuses to start if it cannot reach or load them (see "Startup" below).
- **Watchlist.** Creating a rule adds its symbol to the watchlist (at most `WATCHLIST_MAX_SYMBOLS`, default 50), and the backend keeps that symbol subscribed upstream. Browsers can also subscribe symbols over the websocket; the backend sends one upstream subscribe per symbol either way.
- **Rules** (`price_above`, `price_below`, `percent_change` over a window) are stored in Postgres and kept in memory for evaluation. A fired rule writes an alert and logs one INFO line.

## What it does today

- Ingests live prices from Finnhub over one websocket, with reconnect and resubscribe.
- Creates, lists and deletes rules, stored in Postgres (`/api/rules`).
- Records an alert each time a rule's condition becomes true, kept in Postgres and listed newest first (`/api/alerts`); the dashboard's Alerts section shows them on Refresh.
- Protects every `/api/*` route and the browser websocket with one shared key (`APP_API_KEY`).
- Reports real state on `/health`: database and Finnhub status, HTTP 503 when the database is down.
- Fails loudly at startup with a clear "startup failed" line if the key is missing or Postgres is unreachable or slow (5 s per query or connection, 30 s for the whole startup); malformed stored rows are skipped with a warning.
- Answers 503 instead of hanging when Postgres stalls or goes away after startup, and recovers when it returns.
- Publishes every Docker port on `127.0.0.1` only; the backend container restarts unless stopped by hand.
- Refuses to run the database tests against anything but a disposable `_test` database.

## Not done / limitations

**Scope.** The original brief asked for one POST and one GET. The project grew well beyond that: a Finnhub live feed, a rules engine, alerts, authentication, a React dashboard and browser websockets, with about 1,300 lines of backend code, 400 lines of frontend code and 2,500 lines of backend tests (non-empty lines).

**The migration in `revised-planning.md` is only half done.**
- Browser websockets (`/ws`, the live ticker) still run alongside the new watchlist; the plan removes them.
- The `latest_prices` table and `GET /api/prices` do not exist yet, so the dashboard cannot show prices through Refresh.
- There is no watchlist API: a watched symbol cannot be removed, so the 50-symbol cap can fill up.

**The `ticks` table grows forever.** Every price update from Finnhub is inserted as a new row, and nothing in the app reads or deletes it. At, for example, 5 updates per second for one symbol, that is about 432,000 rows a day, roughly 13 million a month. Set `TICKS_PERSISTENCE_ENABLED=false` to stop writing it.

**Timestamps are stored two ways.** `ticks` uses `TIMESTAMPTZ`; rules, the watchlist and alerts store epoch seconds as `DOUBLE PRECISION`.

**Postgres outages.** The backend cannot start without Postgres (unless `RULES_PERSISTENCE_ENABLED=false`, which disables rules and alerts). If Postgres goes down later, rule and alert endpoints answer 503 and alerts raised during the outage are dropped, not retried.

**Docker** has not been run on the development machine, so the Compose healthcheck, port bindings and restart policy are unverified.

More detail: `revised-planning.md` ("Limitations (as built)").

## Quick start

Requirements: Docker, plus a free [Finnhub](https://finnhub.io) API key.

```
cp .env.example .env     # set FINNHUB_API_KEY, POSTGRES_PASSWORD, APP_API_KEY
docker compose up --build
```

Generate `APP_API_KEY` (a shared secret; the backend refuses to start without it):
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- Dashboard: open http://localhost:5173 (must match `CORS_ORIGINS`) and enter the `APP_API_KEY` when asked. It is kept in the browser session only and asked for again if the API rejects it.
- API: http://127.0.0.1:8000 (`/health` needs no key)
- All ports are published on `127.0.0.1` only (not the LAN). Postgres is on host port 5433.

Without a `FINNHUB_API_KEY` the backend still starts, but no market data flows (`/health` reports `"upstream": "disabled"`).

## Local development

Backend (needs a reachable Postgres and `APP_API_KEY` in the root `.env`):
```
cd backend
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```
From the repository root, use `uvicorn app.main:app --app-dir backend` instead.

Frontend:
```
cd frontend
npm install
npm run dev
npm test
```

Tests run offline using a fake Finnhub server. The real-Postgres tests run only when `TEST_DATABASE_URL` is set, and they drop their tables, so it must point at a disposable database: its name must end in `_test`, and it must not be the same database as `DATABASE_URL`. Anything else stops the test run before any table is touched.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{"status", "database", "upstream"}`; 503 when the database is down |
| `GET /api/symbols` | Active browser symbols and subscriber counts |
| `POST /api/rules` | Create a rule (adds its symbol to the watchlist; 409 when the watchlist is full) |
| `GET /api/rules?symbol=&limit=` | List rules, newest first |
| `DELETE /api/rules/{id}` | Delete a rule |
| `GET /api/alerts?limit=` | List alerts, newest first |
| `WS /ws?key=` | Send `{"action": "subscribe" \| "unsubscribe", "symbol": "AAPL"}`; receive `tick` messages |

Example rule:
```json
{"symbol": "AAPL", "type": "price_above", "threshold": 200}
```
`percent_change` rules also require `window_seconds`.

## Authentication

Every `/api/*` route needs the header `X-API-Key: <APP_API_KEY>`; otherwise the answer is `401 {"detail": "invalid or missing API key"}`. `/health` is open. The browser websocket is `ws://host/ws?key=<APP_API_KEY>` and also checks the `Origin` header against `CORS_ORIGINS`; a bad key or origin refuses the connection (clients see HTTP 403). Log lines mask `key=` values.

```
curl.exe -H "X-API-Key: $env:APP_API_KEY" http://127.0.0.1:8000/api/rules
```
```
Invoke-RestMethod -Headers @{ "X-API-Key" = $env:APP_API_KEY } http://127.0.0.1:8000/api/rules
```
(Set `$env:APP_API_KEY` in the shell first. In Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`, so use `curl.exe`.)

## Configuration

All settings are environment variables; see `.env.example`. Secrets live in `.env` only and are never committed.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs backend `pytest` against its own `ticks_test` Postgres database, and the frontend build and tests, on every push and pull request.

## Recent changes

- Watchlist and alerts: rules keep their symbol subscribed upstream; fired rules are stored as alerts and listed by `GET /api/alerts` and the dashboard.
- Fail-loud startup and a resilient alert writer: the backend stops with a clear error when Postgres or the key is missing; one bad alert or row no longer stops alert writing or startup.
- API-key authentication for `/api/*` and the websocket; all Docker ports bound to `127.0.0.1`.
- One parent router carries the key check for every `/api` route, with a test that fails if a route is left open.
- Honest `/health`, database timeouts (5 s per query or connection, 30 s startup), and the root `.env` is found from any folder.
- Backend container restart policy `unless-stopped`.
- Database tests refuse any `TEST_DATABASE_URL` that is not a disposable `_test` database or that equals `DATABASE_URL`; CI uses its own `ticks_test` database.
