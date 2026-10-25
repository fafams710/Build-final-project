Checker 1 (T009). Ran product: partially (offline pytest 82 passed/17 skipped; code read). Score 84/100.
Criteria: Architecture 17/20, Error model/testability 17/20, Interface 13/15, Adversarial 12/15, Operational 11/15, Deps 15/15 (unchanged).
C1-01 major Operational docker-compose.yml healthcheck: `pg_isready` with no -h checks the unix socket; the postgres image's first-boot init temp server listens on the socket only, so the check can pass before the final restart. Backend then connects over TCP, fails loud and exits (no restart policy). Fix: add `-h 127.0.0.1`. -3
C1-02 minor Adversarial db.py:256 / main.py:200: a malformed row in watchlist/alert_rules (normalize_symbol or Rule validation ValueError) aborts startup permanently; log shows only the exception type. -2
C1-03 minor Operational main.py:193-203 + db.py:114: startup-failed message always says "unreachable or DATABASE_URL empty/wrong" even for schema/backfill failures; db.py log still says "persistence disabled"; no exception detail. -2
C1-04 minor main.py:174-179: only Exception caught; db.close() inside except could itself raise and mask the original error; cancellation during startup does not close db. -1
C1-05 nit CLAUDE.md note says flag=false with valid URL leaves endpoints working; code (_require_rules_pool) returns 503 in that case, so doc/verification note is wrong. -1
Verified OK: lifespan raises before upstream.factory/start; db.close() awaited on failure; opt-out skips loads; empty URL + flag true fails; runtime 503 and ingestion kept.
Not checked: docker compose (not run), real PG tests.
