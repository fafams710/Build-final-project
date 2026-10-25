# Checker 2 - T008 iteration 3 (alert writer reliability)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 88/100. Ran product: partly (offline pytest 76 passed / 15 skipped; code reading). No python left running.

Criterion scores: Fault containment 22/25; Defined behavior 18/20; External data 17/20 (unchanged area); Commandability 13/15; Verifiability 18/20.

Iteration-2 findings: C2-01 fixed (one summary line per batch, tested); C2-02 fixed (no backoff when rows recover, tested); C2-03 fixed (drain skipped when worker done, tested); C2-04 partly; C2-05 not addressed by design.

## Findings

- C2-I3-01 (minor, db.py:293-301): row construction and batch drain sit outside the try/finally; an unexpected exception kills the writer silently (no task_done, queue keeps filling, 3s drain wait at shutdown). Fix: move into the try or log.exception around the loop body. -2
- C2-I3-02 (minor, db.py:313-337): failed alerts are dropped, not requeued, during an outage; queue-full warning not rate-limited; undocumented. -1
- C2-I3-03 (minor, tests 477-491): backoff test mixes a real sleep with yield counts and never asserts the delay. -1
- C2-I3-04 (minor, tests): no test for mid-write cancellation, close() with a raising pool and non-empty queue, Services.shutdown ordering, dead writer; stub pool only. -2
- C2-I3-05 (nit, main.py:127-132): `_symbols_for_connect` resets `_upstream_sent` without `_upstream_lock`; an in-flight reconcile can make it briefly differ from the wanted set. -1
- C2-I3-06 (nit, main.py:225, db.py:47-50): import-time `_configure_logging`; timeouts are constants (out of scope). -2

Not checked: real-Postgres tests, live uvicorn/docker, frontend.
