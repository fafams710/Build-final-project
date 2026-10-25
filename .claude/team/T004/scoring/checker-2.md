Checker: 2 (report returned inline; saved by orchestrator)
Ran product: yes (pytest 22 passed; AC3 grep clean; read main.py, db.py diff, tests). uvicorn, npm, docker not run.
Score: 86/100

Criterion scores
  Fault enumeration and containment   21/25
  Defined behavior over the input domain   16/20
  External data treated as hostile   17/20
  Commandability   13/15
  Verifiability and structure   19/20

C2-01 minor  main.py:73-82  rule-fired log shares one try with enqueue_tick/evaluate; a raising log skips remaining fired rules in that tick. Broadcast has its own try, invariant holds. -2
C2-02 minor  db.py:125-132  old volumes keep webhook_url NOT NULL -> every save_rule fails (swallowed). Fix: ALTER TABLE ... DROP COLUMN IF EXISTS webhook_url, or README note. -2
C2-03 minor  test_api_rules.py  POST with stray webhook_url (ignored, 201) untested. -3
C2-04 nit    ws.py/symbols.py  snapshot + GET /price removed; /price 404 untested; Dashboard still handles "snapshot". -1
C2-05 minor  db.py load_rules  Rule(**row) tolerating stale webhook_url key untested. -3
C2-06 minor  compose/CI  only YAML-parsed; docker compose config and CI never run. SQL placeholders re-read: correct. -2
C2-07 nit    backend/.pytest_cache  stale ids hit raw grep; git grep clean. -1

Not checked: frontend build/tests, live uvicorn, docker, real Postgres.
