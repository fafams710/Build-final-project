Checker:        2
Ran product:    yes (pytest only; TEST_DATABASE_URL unset, 58 passed / 15 skipped; real-PG paths not executed; frontend not run)
Score:          80/100

Criterion scores
  Fault enumeration and containment            20/25
  Defined behavior over input domain           16/20
  External data treated as hostile             16/20
  Commandability                               12/15
  Verifiability and structure                  16/20

Findings

ID: C2-01
Criterion: Fault enumeration and containment
Severity: minor
Location: backend/app/main.py on_tick (enqueue inside the shared try/except, ~line 100-110)
Problem: enqueue_alert sits in the same loop as the logging. If it raises for one fired rule, the remaining events on that tick lose their alerts (the broadcast is still delivered).
Evidence: the try wraps the whole `for rule, event` loop; the except only logs.
Fix: Wrap each enqueue in its own try/except.
Points: -2

ID: C2-02
Criterion: Fault enumeration and containment
Severity: minor
Location: backend/app/main.py:_up_unsubscribe / watch_symbol
Problem: A browser unsubscribe has passed its watchlist check and is awaiting upstream.unsubscribe. A rule then adds the same symbol with ref_count 0 and sends a subscribe. Wire order of the unsubscribe and subscribe is unspecified, so the symbol can end up unsubscribed upstream although it is watched. The tests cover subscribe-side interleaving only.
Evidence: tests/test_watchlist_alerts.py:152,163 cover only subscribe interleaving.
Fix: Serialize rule-path subscribe with the SubscriptionManager lock, or re-reconcile after the unsubscribe.
Points: -3

ID: C2-03
Criterion: Fault enumeration and containment
Severity: minor
Location: backend/app/db.py:_alert_writer
Problem: The writer uses self._pool directly. If the pool is closed or None mid-run, every batch logs errors and is dropped. The writer does survive, which is acceptable best-effort behaviour, but there is no backoff, so it can spin on repeated failures.
Evidence: the exception branch retries row by row per batch with no delay.
Fix: Add a short sleep after a failed batch.
Points: -2

ID: C2-04
Criterion: Defined behavior over input domain
Severity: minor
Location: backend/app/db.py:create_rule_with_watchlist
Problem: The new `WatchlistFull` path is covered only by fakes in the fast suite. The concurrency, rollback and FK-absence checks (AC 1, 3, 4, 9, 10, 12, 13) are real-PG only and were skipped in my run, so I cannot confirm them.
Evidence: pytest reported 15 skipped.
Fix: Run in CI/locally with TEST_DATABASE_URL.
Points: -4

ID: C2-05
Criterion: External data treated as hostile
Severity: minor
Location: backend/app/db.py:_backfill_watchlist and startup load
Problem: Startup swallows a backfill failure (logged by type only) and load_watchlist failures. This is a deliberate spec choice, but a persistent failure leaves rule symbols unsubscribed with no loud signal. The cap is not applied to the backfill (spec-sanctioned).
Evidence: `except Exception ... log.error`.
Fix: Accept, or surface the failure in /health.
Points: -2

ID: C2-06
Criterion: External data treated as hostile
Severity: minor
Location: backend/app/routers/rules.py:create_rule
Problem: A bare `except Exception` after WatchlistFull maps every DB error to 503. That is fine for the generic body. A duplicate-id collision is not distinguished, but it is practically impossible with uuid ids.
Points: -2

ID: C2-07
Criterion: Commandability
Severity: minor
Location: whole project
Problem: No single documented command runs backend and frontend checks together (pytest and npm test are separate). The new setting is documented and works.
Points: -3

ID: C2-08
Criterion: Verifiability and structure
Severity: minor
Location: backend/tests/test_watchlist_alerts.py
Problem: One file mixes watchlist, upstream, alerts and API tests, which is hard to navigate. Real-PG tests skip silently when the variable is unset, so a green local run does not prove the headline guarantees (cap race safety, rollback).
Evidence: 15 skipped.
Points: -4

Positives: cleanup X1/X2/X4 confirmed (grep for _spawn, broadcast_symbol, .broadcast(, store.add returns nothing). Tick path uses synchronous put_nowait. The advisory lock gives a race-safe cap. 503 and 409 bodies are generic. The limit range 1..500 is enforced by Query. WATCHLIST_MAX_SYMBOLS is in .env.example. Interleaving tests exist for the subscribe side.

Not checked: real-Postgres tests, concurrency test result, frontend vitest/build, ws and symbols router behaviour.
