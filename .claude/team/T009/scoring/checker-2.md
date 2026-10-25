# Checker 2 - T009 (alert writer, queue-full warning)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 86/100. Ran product: yes (offline pytest 82 passed / 17 skipped). Real-Postgres and baseline run not reproduced.

Criterion scores: Fault containment 22/25; Defined behavior 17/20; External data 17/20; Commandability 14/15; Verifiability 16/20.

Checked and correct: whole iteration under `except Exception` + `log.exception`, CancelledError re-raised, backoff after unexpected error (db.py:306-339); task_done once per dequeued alert in a finally, join() returns after a bad batch, drain bounded (db.py:314-332, 125-135); per-alert row building, all-bad batch writes nothing without backoff; `_write_alert_rows` (db.py:341-370); queue-full warning non-blocking, count resets per warning (db.py:286-304); item-1 test (tests:707-733) tests the right thing.

## Findings

- C2-01 (minor, tests:707-733): no test for an unexpected error outside row building (e.g. get_nowait raising) or mid-fill cancellation with task_done asserted; the generic except branch and its backoff are untested. -3
- C2-02 (minor, tests:296-317): rate-limit test lacks the "interval not yet elapsed" case and the exact-boundary case. -3
- C2-03 (minor, db.py:331-332): a task_done mismatch ValueError would be swallowed by the generic handler. Accept, or assert unfinished tasks == 0 in tests. -2
- C2-04 (nit, db.py:299-302): a final drop burst is reported only at the next drop (documented, accepted). -1
- C2-05 (minor): docker compose config not run; real-Postgres results not reproduced. -3

Not checked: fail-loud startup, CLAUDE.md, compose YAML.
