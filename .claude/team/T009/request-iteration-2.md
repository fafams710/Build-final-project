# T009 iteration 2 - healthcheck, startup log wording, skip malformed rows (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout -- <file>`, `git restore`, `git reset`,
or anything that changes git history or discards working-tree changes. Only edit local files; the user reviews.
Do not edit `.claude/team/*/baseline*` folders, `revised-planning.md` or `CLAUDE.md` (the orchestrator edits docs).
Use precise edits. Run pytest as `python -X faulthandler -m pytest -o faulthandler_timeout=120`; leave no python or
postgres process you started running.

Background: `.claude/team/T009/request.md`, `spec.xml`, `impl/iteration-1.xml`, `verification/iteration-1.xml`,
`scoring/checker-{1,2,3}.md`. Files before this iteration: `.claude/team/T009/baseline-2/`.

Do exactly these three items. Nothing else (the user explicitly deferred: unused `log` in rules.py, db.close masking
errors, CLAUDE.md wording, extra writer/warning tests).

## 1. Healthcheck must use TCP (checker 1, C1-01)
`docker-compose.yml`: change the postgres healthcheck to `pg_isready -h 127.0.0.1 -U ... -d ...` (keep the existing
variable defaults), so the official image's socket-only init server cannot report healthy before the real server
listens on TCP. No other compose change.

## 2. Startup log lines must name the real cause (C1-03, C3-01)
- `db.py` `Database.connect` except branch currently logs "database unavailable, persistence disabled: <Type>".
  Reword so it is true whether or not the app then aborts, and include the exception type AND message (str(exc),
  never the DATABASE_URL or password; asyncpg error messages do not contain the password, but check that the
  logged text cannot contain the URL).
- `Database` keeps the last connect error (e.g. `self.last_error: Optional[str]`, type + message) so
  `Services._load_state` can log "startup failed: Postgres unavailable: <that error>" instead of always blaming
  reachability/URL. An empty DATABASE_URL gets its own clear message ("DATABASE_URL is empty").
- The "loading rules/watchlist" failure line also includes the message, not only the type.
- Keep the hint to set RULES_PERSISTENCE_ENABLED=false.

## 3. Malformed stored rows are skipped with a warning (C1-02; user chose "skip the bad rows with a warning")
At startup, a row that fails validation must not stop startup:
- `Database.load_rules`: build each `Rule` separately; a row that raises (pydantic ValidationError / ValueError)
  is skipped and logged at WARNING with its id (and symbol if present) and the error message. Valid rows load.
- Watchlist load (`Services._load_state` normalizes with `normalize_symbol`): a symbol that fails normalization is
  skipped with a WARNING naming it; the rest load.
- Backfill (`_backfill_watchlist`): must not copy a symbol that fails `normalize_symbol` into the watchlist; skip it
  with a WARNING. (Check what it does today first and report.)
- Skipped rows stay in Postgres untouched (no deletes).
- A database/query error (not a bad row) still fails startup loudly as in iteration 1.
- Tests: offline tests with the fake DB for a bad rule row and a bad watchlist symbol (startup succeeds, valid ones
  load, a WARNING is logged); real-Postgres tests (TEST_DATABASE_URL) inserting a malformed `alert_rules` row and a
  malformed `watchlist` symbol directly with SQL and asserting startup succeeds and only valid rows load. Check what
  `normalize_symbol` and `Rule` reject so the malformed rows really are invalid.

## Done when
Offline pytest passes; real-Postgres pytest on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port 55497) passes with 0 skipped, then stopped and deleted. Report in
`.claude/team/T009/impl/iteration-2.xml`.
