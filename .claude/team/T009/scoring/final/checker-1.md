# Checker 1 - T009 final (startup correctness, masking, healthcheck)

(Saved by the orchestrator from the scorer's reply.)

Score: 82/100. Ran product: partly (offline pytest 93 passed / 19 skipped; live runs taken from verification/iteration-3.xml).

Criterion scores: Architecture 16/20; Error model 17/20; Interface 12/15; Adversarial input 13/15; Operational 12/15; Dependency hygiene 12/15.

Sound: fail-loud paths raise before any upstream is created, db.close() awaited; opt-out starts with 503; malformed rows skipped in load_rules/list_rules (shared `_build_rules`), watchlist load and backfill; no path logs password or URL; healthcheck uses TCP with service_healthy.

## Findings

- C1-01 (minor, docker-compose.yml): healthcheck never exercised (no docker); `pg_isready -d` does not verify the db exists; backend has no restart policy, so an exit-3 backend stays down. Suggest one live run and `restart: on-failure`. -2
- C1-02 (minor, db.py:98-110): masking misses a `?password=` query parameter or a percent-decoded password; compose builds DATABASE_URL without URL-encoding, so `@` or `/` in the password breaks the connection. -2
- C1-03 (minor, db.py list_alerts): one malformed alert row breaks GET /api/alerts (unlikely, NOT NULL columns). -2
- C1-04 (minor, db.py:117-128): only pool creation is time-bounded; schema DDL, backfill, load_rules and list_rules have no timeout (suggest `command_timeout`). -2
- C1-05 (minor, db.py ~420 lines): Database is becoming a god module; split the writers later. -3
- C1-06 (nit, requirements.txt): no lock/constraints file for transitive deps; Starlette httpx deprecation warning. -3
- C1-07 (nit, main.py `_load_state`): startup raises bare RuntimeError; suggest `StartupError`. -3

Not checked: docker compose, live uvicorn (verifier report used), real-PG run, frontend. Deferred items not deducted.
