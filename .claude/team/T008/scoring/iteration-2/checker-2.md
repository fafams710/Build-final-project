# Checker 2 - T008 iteration 2 (alert writer reliability)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 84/100. Ran product: no (offline pytest 70 passed / 15 skipped; code and test reading).

Criterion scores: Fault enumeration/containment 20/25; Defined behavior 17/20; External data hostile 17/20 (unchanged area); Commandability 13/15; Verifiability/structure 17/20.

What works: task_done in a finally (db.py:325-327); drain bounded by 3s wait_for and skipped without pool/worker (db.py:120-130); close order drain -> cancel -> pool close, upstream stopped before db.close; close idempotent; backoff after task_done, cancel-safe; tick path still put_nowait; _configure_logging idempotent.

## Findings

- C2-01 (minor, db.py:313-329): during an outage, the per-row retry logs up to 1 + 200 error lines per batch before the 1s backoff. Fix: skip the per-row retry on connection-level errors or log one summary line; optional growing backoff. -2
- C2-02 (minor, db.py:306-329): `failed = True` even when the per-row retry succeeds, so a poison row throttles to ~1 batch/s; a failed batch is dropped, not requeued; the queue-full warning is not rate-limited. Fix: back off only if a row retry also fails. -2
- C2-03 (minor, db.py:120-130): the drain does not check `_alert_worker.done()`; a dead writer with a non-empty queue costs the full 3s at shutdown (bounded). -1
- C2-04 (minor, tests): `test_close_during_backoff_is_not_delayed` only asserts finishing within 5s; the backoff test mixes a real sleep with loop-yield counts; no test for close with a failing pool and non-empty queue, Services.shutdown ordering, mid-write cancellation, or log volume. -3
- C2-05 (nit, db.py:47-50): timeouts are module constants (acceptable per request); `create_app()` at import runs `_configure_logging` as an import side effect. -2

Not checked: real-Postgres tests, race fix in depth, uvicorn/docker log output.
