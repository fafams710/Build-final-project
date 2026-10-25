# Checker 3 - T008 iteration 3 (scope, maintainability, logging)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 91/100. Ran product: yes (pytest 76 passed / 15 skipped). Real-Postgres not run.

Criterion split: Scope 28/30; Minimality/style 21/25; Comments 17/20; Logging 14/15; Tests 10/10.

Scope: only main.py, db.py, test_watchlist_alerts.py changed; every requested item present; `_reconcile` design clean, fast paths gone (resolves C3-01). Two pre-existing tests restructured (inline await -> task + gather with timeout); assertions untouched; comments at tests :158/:172 explain why. Logging idempotent, propagate=False only when own handler added.

## Findings

- C3-I3-01 (nit, db.py:306,314,334): `failed` set True then reset False; nested try/finally/except remains. Suggest `failed = row_failures > 0` or a `_write_alert_batch` helper. -2
- C3-I3-02 (nit, main.py:75-85): reconcile explanation is a 10-line block above `_wanted`, not on `_reconcile`/`_upstream_sent`; loose rationale for failed-unsubscribe handling. -2
- C3-I3-03 (nit, main.py:93-111): near-duplicate subscribe/unsubscribe try blocks in `_reconcile`; test stub gained a `batch_fail` mode. Optional. -1
- C3-I3-04 (nit, main.py:23-33): hand-copied uvicorn format; level above INFO silently overridden. -1
- C3-I3-05 (nit, main.py:126-131): `_symbols_for_connect` resets `_upstream_sent` as a side effect in a getter, lock-free; undocumented. -2

Not checked: real-Postgres, live uvicorn, Docker; added tests not mutation-tested.
