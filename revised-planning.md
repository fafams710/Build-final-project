# Revised Plan v2: Postgres as Source of Truth, Refresh Button, Core Functions Only

## Goal

Keep only the core of the program:

1. Ingest live prices from Finnhub over a single upstream websocket.
2. Let the user choose which symbols to watch.
3. Evaluate threshold rules against incoming prices.
4. Show current prices and triggered alerts in the dashboard.

Two changes drive the redesign:

- **Postgres is the source of truth.** Watchlist, rules, latest prices and alerts all live
  in Postgres. The backend reads from it and writes to it. Nothing the user cares about
  exists only in memory.
- **The dashboard updates with a Refresh button.** There is no live push to the browser.
  Clicking Refresh fetches the current state from the API.

## Open decisions (answer before building)

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Webhooks: removed in plan v1. Still removed? | Yes. Triggered rules are written to an `alerts` table and shown on Refresh. This keeps one place for results. |
| 2 | Redis: removed in plan v1. Still removed? | Yes. Postgres is the source of truth, so a second store for latest prices is redundant. |
| 3 | TimescaleDB: plain Postgres or keep the hypertable? | Plain Postgres. The core flow stores only the latest price per symbol, so time-series features are not needed yet. |
| 4 | Without per-browser sockets, the old per-connection ref-counting has nothing to count. Who decides what is subscribed upstream? | A shared `watchlist` table. The upstream subscribes to its contents. Single-user, no accounts. |
| 5 | Keep raw tick history? | Not in the core build. Add a `ticks` table later if charts or replay are wanted. |

The steps below assume the recommendations.

## Architecture

```
Finnhub --ws--> FinnhubClient --> Services.on_tick --+--> RuleEngine --> alerts (queued write)
                                                      +--> latest_prices (queued, batched upsert)

Browser --click Refresh--> GET /api/prices, GET /api/alerts, GET /api/rules --> Postgres
Browser --Watch / Add rule--> POST /api/watchlist, POST /api/rules --> Postgres (+ upstream subscribe)
```

The Finnhub connection and the tick path stay in the backend. The browser never receives
pushed ticks. It reads Postgres through the API.

## Data model (Postgres)

| Table | Columns | Purpose |
|-------|---------|---------|
| `watchlist` | `symbol` PK, `created_at` | Symbols the upstream subscribes to |
| `latest_prices` | `symbol` PK, `price`, `volume`, `exchange_ts`, `received_ts` | One row per symbol, upserted |
| `alert_rules` | `id` PK, `symbol`, `type`, `threshold`, `window_seconds`, `created_at` | Rules (existing table, `webhook_url` dropped) |
| `alerts` | `id` PK, `rule_id`, `symbol`, `price`, `triggered_at` | One row per rule crossing |

Schema is created at startup with `CREATE TABLE IF NOT EXISTS`, as `db.py` does today.

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Unchanged |
| `GET /api/watchlist`, `POST /api/watchlist`, `DELETE /api/watchlist/{symbol}` | Manage watched symbols. POST and DELETE also subscribe or unsubscribe upstream. |
| `GET /api/prices` | Latest price for every watchlist symbol, from `latest_prices` |
| `GET /api/rules`, `POST /api/rules`, `DELETE /api/rules/{id}` | Unchanged shape, minus `webhook_url` |
| `GET /api/alerts?limit=` | Recent alerts, newest first |

Symbols pass through `models.normalize_symbol` at every boundary.

## What is removed

- `webhooks.py` and all `webhook_*` settings and the signing secret
- `redis_cache.py`, `redis_url`, the `redis` dependency and compose service
- TimescaleDB image and hypertable creation
- `routers/ws.py`, `connection_manager.py`, `subscription_manager.py`: no browser sockets means no connections to track or ref-count
- Frontend `useWebSocket.ts`

## What is kept

- `finnhub_client.py`: the single upstream connection, reconnect with backoff, resubscribe on reconnect
- `rules.py` `RuleEngine`: edge-triggered evaluation, in-memory price window for percent-change rules
- `models.py`, `config.py`, `routers/health.py`
- `db.py`: becomes the main data layer instead of a best-effort add-on

## Steps

### Step 1: Backend data layer (`db.py`)
- Add the `watchlist`, `latest_prices` and `alerts` tables to `SCHEMA`; drop `webhook_url` from `alert_rules`; remove the hypertable statement.
- Add methods: `list_watchlist`, `add_symbol`, `remove_symbol`, `latest_prices`, `upsert_prices(batch)`, `insert_alert`, `list_alerts`. Keep the rule methods.
- Keep the queue-plus-worker pattern for price upserts and alert inserts, so the tick path never awaits Postgres. The worker coalesces the queue to the newest tick per symbol before each upsert.

### Step 2: Backend wiring (`main.py`, `config.py`)
- `Services.startup`: connect Postgres, load rules into the in-memory store, load the watchlist, start the upstream, and subscribe to the watchlist symbols.
- `Services.on_tick`: enqueue the price upsert, evaluate rules, enqueue an alert row for each fired rule. Wrap this in try/except so one failure cannot affect ingestion. No broadcast step.
- Remove `Services` pieces for cache, webhooks and connections. `SubscriptionManager` is replaced by direct upstream subscribe or unsubscribe from the watchlist endpoints.
- Settings: keep `FINNHUB_API_KEY`, `DATABASE_URL`, `CORS_ORIGINS`, reconnect settings. Remove the rest. Update `.env.example` in the same change.

### Step 3: Rules store
- Postgres first: `POST /api/rules` writes the row, then adds it to the in-memory store. If the write fails, the request returns an error. Rules are no longer best-effort.
- The in-memory store remains the read path for evaluation, loaded at startup.

### Step 4: Routers
- Add `routers/watchlist.py`, `routers/prices.py`, `routers/alerts.py`.
- Update `routers/rules.py` for the new payload. Delete `routers/ws.py` and `routers/symbols.py`.

### Step 5: Frontend
- Remove `useWebSocket.ts` and its `status` display.
- `Dashboard.tsx`: add a Refresh button and a "last updated" time. Refresh calls `GET /api/prices`, `/api/alerts` and `/api/rules`. Load once on mount.
- Watch and Remove call the watchlist endpoints, then refresh.
- `PriceTicker.tsx`, `RuleList.tsx`: render from fetched data. Add an alerts list.
- `RuleForm.tsx`: remove the webhook URL field. `api/client.ts`: add the new calls and drop `wsUrl`.

### Step 6: Deployment
- `docker-compose.yml`: services are `postgres` (plain image), `backend`, `frontend`. Remove `redis` and `timescaledb`.
- Backend waits for Postgres. If the connection fails at startup, fail loudly instead of running without persistence.

### Step 7: Tests (offline and deterministic)
- Replace the Postgres layer in tests with an in-memory fake exposing the same methods. Do not connect to a real database.
- Keep `fake_finnhub`. Update tick-path tests: a tick produces a price upsert, a fired rule produces an alert, and a failing enqueue does not stop ingestion.
- Add router tests for watchlist, prices and alerts against the fake.
- Update the frontend test: Refresh triggers the fetches and updates the displayed price.
- Delete the webhook, Redis, connection-manager and subscription-manager tests.

### Step 8: Docs
Update `CLAUDE.md`: layout, commands and invariants (see below).

## Invariant changes (for CLAUDE.md)

| # | Today | After this plan |
|---|-------|-----------------|
| 1 | Exactly one Finnhub websocket, created in the lifespan | Unchanged |
| 2 | Symbols ref-counted across browsers | Replaced: the upstream subscription set equals the `watchlist` table. No duplicate subscribe. |
| 3 | Tick path never blocks on Redis, Postgres or webhooks | Unchanged, now about Postgres only |
| 4 | Rules in memory, persistence best-effort | Replaced: Postgres is the source of truth; memory is a copy for evaluation. A failed rule write fails the request. |
| 5 | Secrets in `.env` only | Secrets are `FINNHUB_API_KEY` and the DB password |
| 6 | Tests offline | Unchanged |

## Acceptance criteria

1. `pytest`, `npm test` and `npm run build` pass offline.
2. With Postgres and a Finnhub key, the stack starts with `docker compose up`.
3. Adding a symbol makes one upstream subscribe call. Removing it makes one unsubscribe call.
4. After restart, the watchlist, rules, alerts and last prices are all still present.
5. Clicking Refresh shows the newest stored price and any new alerts.
6. A rule crossing its threshold writes exactly one `alerts` row per crossing, and re-arms when the condition goes false.
7. `grep -ri "redis\|webhook\|timescale\|websocket"` over the app code returns only `finnhub_client.py`.

## Risks and tradeoffs

- **Not live.** Prices are as fresh as the last Refresh click. This is the requested tradeoff. An optional auto-refresh timer could be added later without backend changes.
- **Edge state is not persisted.** After a restart, a rule whose condition is already true may fire once again. Percent-change windows also restart empty.
- **Shared watchlist.** All users see and change the same list. There are no accounts.
- **Database is now required.** Startup fails if Postgres is unreachable.
- **Write volume.** Upserting a row per tick is heavy for busy symbols. Coalescing to the newest tick per symbol per batch keeps the load bounded.

## Limitations (as built)

These are the points where the build does not do everything that was wanted, recorded on purpose.

### No degraded start without Postgres

The first plan for the rules work (T005) was to keep the app running when Postgres is unavailable and answer rule
requests with 503. That could not be kept at startup: if the watchlist or rules cannot be loaded, the app would start
with nothing subscribed and no rules, and look healthy while doing nothing. Startup therefore fails loudly instead:
if `RULES_PERSISTENCE_ENABLED=true` (the default) and Postgres is unreachable, `DATABASE_URL` is empty, or the
database fails while loading rules, the watchlist or the watchlist backfill, the backend exits with a "startup failed"
error that names the cause. A single malformed stored row (a rule or watchlist symbol that fails validation) does not
stop startup: it is skipped with a warning and left in Postgres untouched. Docker Compose waits for a Postgres
healthcheck (`pg_isready` over TCP) before starting the backend.

What is still not managed:

- The app cannot start in a degraded mode with Postgres down. `RULES_PERSISTENCE_ENABLED=false` is the only way to
  start without a database, and then rules, alerts and the watchlist are not available (their endpoints return 503).
- Postgres going down after a successful start is handled, not prevented: rule and alert endpoints return 503,
  ingestion keeps running, and alerts raised during the outage are dropped (logged, with a rate-limited warning).
  They are not retried later.

## Verification handoff

After the build, run the offline suites. Then do a live check with a real Finnhub key: add `BINANCE:BTCUSDT`, click Refresh and see a price, create a low "price above" rule, click Refresh and see one alert, restart the stack and confirm everything persisted.
