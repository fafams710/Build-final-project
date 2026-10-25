# T009 - alert writer resilience, fail-loud startup, alert-loss documentation (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout -- <file>`, `git restore`, `git reset`,
or anything that changes git history or discards working-tree changes. Only edit local files; the user reviews the
diff. Do not edit `.claude/team/*/baseline*` folders. Use precise edits. Never leave background processes running;
run pytest as `python -X faulthandler -m pytest -o faulthandler_timeout=120` and stop any python you start.

Context: builds on uncommitted T008 work on branch `feat/T008-watchlist-alerts` (HEAD 68afb62). Read CLAUDE.md
(including "Known open issues"), revised-planning.md, and `.claude/team/T008/` reports for background. Baseline of
the working tree before T009: `.claude/team/T009/baseline/` (tracked.diff = `git diff HEAD` + copies of untracked files).

## Item 1 - the alert writer must not die silently (CLAUDE.md known issue 4)
In `backend/app/db.py` `_alert_writer`, the batch dequeue and row building sit outside the `try`; an unexpected
exception there ends the writer task and no alert is written again.
- TEST FIRST: write a test that puts an alert that makes row building raise (e.g. an object missing an attribute)
  followed by a valid alert, and asserts the valid alert is still written and the writer task is still alive.
  Record that it FAILS on the current code before changing db.py.
- Fix: the loop must survive any non-cancellation exception (log it with `log.exception`) and keep `task_done`
  accounting correct (drain must not hang on a bad batch). Small readability fix in the same function is allowed:
  replace the set-then-reset `failed` flag with a direct computation (e.g. `failed = row_failures > 0`).

## Item 3 - fail-loud startup (CLAUDE.md invariant 5; user chose option a)
If Postgres is unreachable at startup, or loading rules/watchlist or the watchlist backfill fails at startup, the
app must fail to start (lifespan raises, uvicorn exits non-zero) with a clear error log line, instead of running
with an empty watchlist/rule set. Runtime behaviour after a successful start is unchanged (rule/alert endpoints
still 503 when Postgres goes away later; ingestion keeps running).
- Read how `Database.connect`, `RuleStore.load`, `load_watchlist`, `_backfill_watchlist` and the
  `RULES_PERSISTENCE_ENABLED` / `TICKS_PERSISTENCE_ENABLED` flags behave today and make a clear, documented choice
  for the flags (the user has not decided anything about the flags; do not remove them; state what you did).
- Update tests that relied on starting with a failing DB; tests for runtime outage (503) must remain.
- `docker-compose.yml`: add a `pg_isready` healthcheck to `postgres` and make `backend` depend on it with
  `condition: service_healthy`, so fail-loud startup does not break `docker compose up`. No other compose changes.
- Update CLAUDE.md text that says fail-loud startup is not done (the orchestrator will review it).

## Item 4 - alert loss during an outage: document + rate-limit (user chose option a)
- Alerts that still fail after the per-row retry are dropped (unchanged). Document this in a short code comment at
  the drop point.
- Rate-limit the alert queue-full warning in `enqueue_alert` (e.g. at most one warning per N seconds, with a count
  of drops since the last warning). Must stay non-blocking (`put_nowait`); no new setting needed (module constant).
- Test: many drops in a burst produce one warning line with the count.

## Out of scope
Upstream call timeouts (known issue 3), reconnect edge cases (issues 1-2), other test gaps, frontend,
`revised-planning.md` (the orchestrator edits it), new settings.

## Done when
Offline `pytest` passes; real-Postgres tests pass on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port not 55432/55440/55461/55477), 0 skipped; the item-1 test failed
before the fix; `docker compose config` validates if docker is available (say so if not).

## User decisions (after spec)
- Decision A: ACCEPTED planner's proposal. RULES_PERSISTENCE_ENABLED=true -> Postgres required, fail-loud startup;
  false -> explicit opt-out: skip loads, start empty, rule/alert endpoints 503.
- Decision B: YES. Empty DATABASE_URL with the flag true fails startup.
- Mixed batch: a broken alert is logged and dropped; valid alerts in the same batch are still written.
- CLAUDE.md: remove known issues 4 and 6 once fixed. KEEP issue 5 (alerts dropped during an outage), reworded as an
  accepted, documented limitation with the rate-limited warning. Do not edit revised-planning.md.
