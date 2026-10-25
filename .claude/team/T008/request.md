# Request (T008)

Fix L1 (rules only fire for symbols a browser watches) and L2 (fired alerts are invisible), using the target design's `watchlist` and `alerts` tables, plus remove dead code X1, X2, X4. Branch: feat/T008-watchlist-alerts (from fix/T005-rules-postres).

User decisions (binding):
1. Approach A: Postgres `watchlist` and `alerts` tables (not an in-memory patch).
2. Creating a rule automatically adds its symbol to the watchlist.
3. Deleting a symbol's last rule leaves the symbol on the watchlist.
4. Watchlist cap: setting WATCHLIST_MAX_SYMBOLS (default 50); a rule for a NEW symbol beyond the cap -> 409, nothing saved.
5. Alerts reach the user via GET /api/alerts + a dashboard Alerts section with a Refresh button (loads once on mount). No websocket push for alerts.
6. D1: alerts are kept when their rule is deleted: NO foreign key; each alert stores its own rule_type and threshold.
7. D2: NOT in scope - do not add any /api/watchlist endpoints.
8. Cleanup: remove X1 (`Services._spawn`, `_tasks` in main.py), X2 (`ConnectionManager.broadcast`, `broadcast_symbol`, `connection_ids` if unused after; adjust tests), X4 (`RuleStore.add`; tests use `put(Rule(...))`). No behaviour change from cleanup.

Backend requirements:
- Schema (CREATE TABLE IF NOT EXISTS at startup): watchlist(symbol TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL); alerts(id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, symbol TEXT NOT NULL, rule_type TEXT NOT NULL, threshold DOUBLE PRECISION NOT NULL, price DOUBLE PRECISION NOT NULL, triggered_at DOUBLE PRECISION NOT NULL) + index on alerts(triggered_at DESC).
- POST /api/rules: one transaction = cap check (race-safe, e.g. lock) + INSERT watchlist ON CONFLICT DO NOTHING + INSERT rule. 409 when cap would be exceeded; 503 on DB failure; on success, if symbol is newly watched, add to the in-memory watchlist and send ONE upstream subscribe (unless a browser already holds it upstream). Upstream subscribe failure does not fail the request (log; reconnect resubscribes).
- Upstream symbol set = watchlist UNION browser subscriptions. FinnhubClient's get_symbols (used on every (re)connect) returns that union; watchlist loaded at startup (if DB unavailable at startup, watchlist is empty, app keeps running).
- No duplicate upstream subscribe: a browser subscribing to a watched symbol sends none; a rule watching a symbol a browser holds sends none.
- No wrong unsubscribe: when the last browser leaves a symbol that is on the watchlist, do NOT unsubscribe upstream.
- Services.on_tick: for each fired rule, enqueue an alert (non-blocking, queued + background worker like ticks; queue full -> drop + warning). Keep the INFO log line. Tick path must never await Postgres; an enqueue failure must not stop the broadcast.
- GET /api/alerts?limit=: newest first (ORDER BY triggered_at DESC, id DESC), default 50, 1..500 else 422, 503 on DB failure, generic 503 body. Same error-handling pattern as routers/rules.py (do not change that pattern in this task).
- Settings in config.py and .env.example together.
- Symbols normalized via models.normalize_symbol at every boundary.

Frontend requirements:
- api/client.ts: Alert type + listAlerts(limit).
- components/AlertList.tsx (new): Refresh button, "last updated" time, alerts newest first (symbol, rule type, threshold, price, time); loads once on mount; on error shows an error message (NOT an empty list).
- Dashboard.tsx renders AlertList. Do not remove the websocket code (out of scope).

Tests:
- Fast (fake DB + fake Finnhub): new-symbol rule -> exactly 1 upstream subscribe; second rule same symbol -> 0; browser subscribe to watched symbol -> 0 extra; that browser leaving -> 0 unsubscribe; 51st symbol (with cap 50, or a small cap in test settings) -> 409 and nothing saved; crossing tick -> 1 alert enqueued, staying above -> 0, below then above -> 1 more; enqueue failure doesn't stop broadcast; GET /api/alerts ordering/limit/422/503.
- Real Postgres (TEST_DATABASE_URL, skipped if unset): transaction rollback (rule insert fails -> no watchlist row), alert worker persists rows readable by list_alerts, restart reloads watchlist and subscribes it on startup, cap enforced.
- Frontend: Refresh fetches and shows a new alert; error shows a message.

Out of scope: /api/watchlist endpoints, latest_prices, removing browser websockets / routers/symbols.py / snapshot code, hardening (timeouts, narrowing except, SELECT *, test-DB name guard, reconnect), auth, rate limiting, persisting edge-trigger state. Do NOT edit CLAUDE.md or revised-planning.md (orchestrator updates CLAUDE.md).

Acceptance:
1. pytest passes with TEST_DATABASE_URL unset, and with it set to a real empty Postgres (real-DB tests run, 0 skipped).
2. npm run build and npm test pass.
3. Live: rule on a symbol no browser watches -> exactly one upstream subscribe; restart -> symbol subscribed again at startup.
4. Live: a crossing tick -> exactly one alerts row, visible via GET /api/alerts; re-arm works.
5. Browser leaving a watched symbol does not unsubscribe it upstream; no duplicate subscribes.
6. Cap: new symbol beyond cap -> 409, nothing saved.
7. No DB: POST rule and GET alerts -> 503; /health 200; ticks still flow.
8. X1, X2, X4 removed; grep finds no references.

User constraint (binding for every stage): change local files only. Never run git commit, git push, git add, git stash, git switch/checkout, or anything that changes branches or history. The user reviews the working-tree diff.
