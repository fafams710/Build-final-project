# Checker 2 - T009 final (alert writer, `_build_rules`, tests)

(Saved by the orchestrator from the scorer's reply.)

Score: 88/100. Ran product: partly (offline pytest 93 passed / 19 skipped; real-PG and live runs from the verifier).

Criterion scores: Fault containment 22/25; Defined behavior 17/20; External data 17/20; Commandability 14/15; Verifiability 18/20.

Holds up: alert writer (db.py:347-376) fully inside try, task_done once per dequeued alert, cancellation re-raised, backoff; writer tests deterministic; queue-full warning (db.py:327-345) non-blocking, clock-patched test; `_build_rules` (db.py:243-256) shared, skips ValueError, query errors still 503, LIMIT caveat commented; `_describe_error` masks URL and userinfo only.

## Findings

- C2-01 (minor, db.py:228-241): LIMIT applied before skipping; if the newest N rows are malformed, `?limit=N` returns [] although valid rules exist. Untested. -2
- C2-02 (minor, db.py:327-345): trailing drops inside the warning window are never reported if no further drop happens. -1
- C2-03 (minor, db.py:347-376): the outer `except Exception` branch of the writer is untested (user deferred). -1
- C2-04 (minor, db.py:94-106): `?password=` query values and percent-decoded passwords not masked. -2
- C2-05 (minor, db.py:233): every GET /api/rules re-logs a WARNING per malformed row (log noise with a polling dashboard). -1
- C2-06 (minor, tests:533-548): T008 backoff test uses a real 0.2 s sleep (flake risk); test_watchlist_alerts.py (~900 lines) is a catch-all. -3
- C2-07 (nit, main.py): "startup failed: Postgres unavailable:" prefix also used for schema/backfill causes (cause is named after it). -1

Not checked: real-PG runs, live uvicorn, docker compose. Deferred items not deducted.
