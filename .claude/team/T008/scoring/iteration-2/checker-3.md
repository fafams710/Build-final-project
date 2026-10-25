# Checker 3 - T008 iteration 2 (scope, maintainability, logging)

(Saved by the orchestrator from the scorer's reply.)

Score: 90/100. Ran product: yes (pytest 70 passed / 15 skipped; baseline diffed outside the repo). Real-Postgres tests not run.

Criterion split: Scope 27/30; Minimality/style 21/25; Logging 17/20; Comments/CLAUDE.md 15/15; Test quality 10/10.

All four items implemented; only main.py, db.py and the test file touched (+241 test lines); no git state changes.

## Findings

- C3-01 (minor, main.py:79-86, 98-101): lock-free fast paths in `_up_subscribe`/`watch_symbol` deviate from "serialize under one lock"; added to avoid a deadlock in two existing tests; re-checks keep it correct (note: checker 1 found a case where they do not). -3
- C3-02 (minor, db.py:303-329): writer body re-indented under nested try/finally + `failed` flag; suggest a `_write_alert_batch(rows) -> bool` helper. -2
- C3-03 (nit, main.py:21-33): one blank line after `_configure_logging` (PEP 8 wants two); placement splits the module header. -1
- C3-04 (minor, db.py:326-329): backoff fires even when every row retry succeeds; a poison row costs 1s per batch. Set `failed` only if a row also fails. -2
- C3-05 (nit, main.py:23-31): hand-copied uvicorn format; duplicate lines if root gains a handler later (set `propagate = False` when adding the handler); a user level above INFO is silently overridden. -3
- C3-06 (nit, main.py:72-76): stale comment "Neither decision has an await between its check and the state it reads". -1

Not checked: real-Postgres tests, live uvicorn run, full read of the added tests.
