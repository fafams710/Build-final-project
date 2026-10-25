Checker:        1
Ran product:    yes (pytest only: 58 passed, 15 skipped; real-PG tests not run; no server/frontend run)
Score:          80/100

Criterion scores
  Architecture                      16/20
  Error model and testability       16/20
  Interface and reusability         12/15
  Adversarial input                 13/15
  Operational                       11/15
  Dependency hygiene                12/15

Findings

ID:        C1-01
Criterion: Architecture
Severity:  minor
Location:  backend/app/main.py:56-86, backend/app/db.py:94-100
Problem:   Watchlist coordination is split between a Services in-memory set and the browser SubscriptionManager. The invariant "upstream set equals watchlist" relies on subtle ordering comments. Legacy browser path (ws/symbols/connection_manager) is still mixed in. db.py also grows into a larger module (schema, ticks, rules, watchlist, alerts) and does the backfill inside connect().
Evidence:  _up_subscribe / watch_symbol / upstream_symbols in main.py; backfill in Database.connect.
Fix:       Split alerts/watchlist repos out of db.py later; drop browser path when migration finishes.
Points:    -4

ID:        C1-02
Criterion: Error model and testability
Severity:  minor
Location:  backend/app/main.py:130-134, backend/app/db.py:_alert_writer
Problem:   Errors are mostly broad `except Exception` with type-name-only logs. Watchlist load failure at startup is swallowed to an empty set (so upstream subscribes nothing), contradicting the fail-loud invariant. Alerts dropped on a full queue only log a warning. Only WatchlistFull is a typed error.
Evidence:  main.py startup: `except Exception ... self.watchlist = set()`.
Fix:       Let load_watchlist failure propagate (or surface it in /health).
Points:    -4

ID:        C1-03
Criterion: Interface and reusability
Severity:  minor
Location:  backend/app/db.py:_WATCHLIST_LOCK_KEY, backend/app/routers/rules.py
Problem:   Router calls services.watch_symbol and Database directly; create_rule_with_watchlist returns a bool that the caller ignores. Alert rows are built from untyped event dict keys in on_tick. Watchlist endpoints promised in CLAUDE.md are not part of this change (rules implicitly add to the watchlist).
Evidence:  rules.py create_rule ignores return value.
Fix:       Drop unused return or use it to decide the subscribe.
Points:    -3

ID:        C1-04
Criterion: Adversarial input
Severity:  minor
Location:  backend/app/routers/alerts.py:12, backend/app/db.py:_backfill_watchlist
Problem:   limit is bounded 1..500 (good). Backfill bypasses the cap (documented) so a legacy DB can exceed WATCHLIST_MAX_SYMBOLS with all symbols subscribed upstream. Alert queue of 1000 drops silently under load.
Evidence:  db.py backfill comment "cap not applied".
Fix:       Apply the cap on backfill or log a warning when over cap.
Points:    -2

ID:        C1-05
Criterion: Operational
Severity:  minor
Location:  backend/app/db.py:_alert_writer, close()
Problem:   Shutdown cancels the alert worker without draining the queue, so queued alerts are lost on stop. Alert worker retries row by row with no backoff or cap on a down DB, and every failure logs at error. No health indication of alert-writer failure or queue depth. Pool acquisition has no explicit timeout in new code.
Evidence:  close() calls worker.cancel() for both workers.
Fix:       Drain with a bounded timeout on close; expose queue depth in /health.
Points:    -4

ID:        C1-06
Criterion: Dependency hygiene
Severity:  minor
Location:  backend/requirements.txt, frontend/
Problem:   Python pins are all `==` and used; no Python lockfile with hashes. Pytest shows a deprecation warning about httpx with starlette's TestClient (suggests httpx2). Frontend lockfile not verified. No new deps added by T008 (good).
Evidence:  pytest warning output.
Fix:       Optional: pip-compile with hashes; track the httpx2 migration.
Points:    -3

Not checked: real-Postgres tests (15 skipped; relied on the implementer's claim of 73 passed on PG17), concurrency/advisory-lock behaviour, frontend tests/build, docker compose, frontend lockfile consistency.
