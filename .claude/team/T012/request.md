# T012 - M3 honest /health, M8 database timeouts (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout`, `git switch`, `git restore`,
`git reset`, `gh` commands, or anything that changes git history, branches or discards working-tree changes.
Read-only `git diff` / `git status` / `git show` are allowed. Only edit local files; the user verifies and commits.
Never print values from `.env`. Use precise edits. Run pytest as
`python -X faulthandler -m pytest -o faulthandler_timeout=120`; leave no python/node/postgres process you started.

Context: the working tree already contains uncommitted T011 changes (`backend/app/main.py` parent /api router,
new `backend/tests/test_api_auth_coverage.py`); keep them. Snapshot before T012: `.claude/team/T012/baseline/`.
Read CLAUDE.md first (startup fail-loud, alert writer, authentication sections).

## M3 - /health reports real state (user chose option b)
- `GET /health` returns `{"status": "ok"|"degraded", "database": "ok"|"down"|"disabled",
  "upstream": "connected"|"disconnected"|"disabled"}`.
- database: live `SELECT 1` with a short timeout (about 2 s) when rules persistence is enabled and a pool exists;
  "down" on any failure or timeout; "disabled" when `RULES_PERSISTENCE_ENABLED=false` (opt-out) - decide and state
  what "disabled" vs "down" means when the flag is true but there is no pool (cannot happen after a successful
  fail-loud start, but be defined).
- upstream: from the upstream client's `connected`; "disabled" when there is no upstream (no Finnhub key).
- HTTP status: 503 when database is "down"; otherwise 200 (upstream disconnected only sets status "degraded" in
  the body, still 200). "disabled" is not a failure.
- `/health` stays open (no API key) and must not leak anything beyond these fields (no error messages, no URLs).
- Keep the existing "no-DB opt-out -> /health 200" test true.

## M8 - database timeouts (user chose 5 s / 5 s / 30 s as module constants in db.py)
- Every query: `command_timeout` = 5 s on the pool.
- Getting a connection: every `pool.acquire()` call gets a 5 s timeout (find every call site).
- Startup: schema DDL + watchlist backfill + loading rules and watchlist are bounded by one 30 s overall limit; on
  timeout startup fails loudly with a clear "startup failed: ... timed out" line (fits T009's fail-loud path and its
  credential-masked error text).
- A timeout in a request path (rules and alerts endpoints) returns 503 like other database unavailability, never 500.
  The alert and tick writers treat a timeout as a failed write (existing retry/backoff); ingestion never blocks.
- No new settings; constants only, named clearly, with a one-line comment each.

## Tests (write first, record that they fail on the current code)
- /health: ok, db down (stub pool raising / timing out), opt-out disabled, upstream connected/disconnected/disabled,
  HTTP 503 only for db down, no key required, body has only the three fields.
- Timeouts: a stub pool/connection that never returns -> request gets 503 within the limit (patch constants to small
  values in tests so they stay fast and deterministic); acquire timeout -> 503; startup overall timeout -> fail-loud.
- Real-Postgres: a test that holds a lock (e.g. `LOCK TABLE alert_rules IN ACCESS EXCLUSIVE MODE` in another
  connection) and asserts a rules request returns 503 within the timeout instead of hanging (constants may be patched
  smaller for speed).

## Out of scope
Everything else (other medium items, docker, frontend, new settings, CLAUDE.md - the orchestrator updates it; list in
your report what CLAUDE.md should say).

## Done when
Offline pytest passes; real-Postgres pytest on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port 55531) passes, 0 skipped, then stopped and deleted.
