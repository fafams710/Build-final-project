Checker: 1
Ran product: partial (offline pytest: 76 passed, 15 skipped; code read of main.py reconcile logic; relied on verifier's PG/uvicorn/fuzz evidence, not re-run)
Score: 86/100

Criterion scores
  Architecture 17/20
  Error model and testability 17/20
  Interface and reusability 13/15
  Adversarial input 12/15
  Operational 13/15
  Dependency hygiene 14/15

Findings
ID: C1-01  Criterion: Architecture  Severity: minor
Location: backend/app/main.py:127-132 (_symbols_for_connect) vs FinnhubClient resubscribe loop
Problem: Connect-time resubscribe sets _upstream_sent = snapshot immediately, but the client sends the subscribes one by one. A browser-only symbol unsubscribed during that loop gets its unsubscribe sent first, then the stale subscribe: leaked upstream subscription until next reconnect (verifier bug 1). Narrow window; wasted ticks/symbol budget, no wanted symbol lost.
Evidence: verification/iteration-3.xml bug 1 (wire order sub A, unsub B, sub B, unsub A).
Fix: Mark sent per symbol as the client sends it, or run the resubscribe under _upstream_lock.
Points: -2

ID: C1-02  Criterion: Error model and testability  Severity: minor
Location: main.py:101-108 and reconnect
Problem: A late-completing old-connection unsubscribe can discard a symbol from sent after reset, causing a second subscribe (verifier bug 2). Contrived with real websockets (old send raises). Severity low: duplicate subscribe is harmless on Finnhub.
Fix: Tag sent state with a connection generation.
Points: -1

ID: C1-03  Criterion: Error model and testability  Severity: minor
Location: tests/test_watchlist_alerts.py (~521) 
Problem: logging propagate assertion is conditional (`if count:`); no test drives reconnect with the real FinnhubClient loop, so C1-01/02 are untested.
Points: -2

ID: C1-04  Criterion: Operational / Adversarial  Severity: minor
Location: main.py:90-108
Problem: _reconcile awaits upstream.subscribe/unsubscribe while holding the global lock with no timeout; a stalled send blocks all rule creation and browser (un)subscribes. Lock order manager lock -> upstream lock is consistent and rule path takes only the upstream lock, so no deadlock, but a hang is not bounded.
Fix: asyncio.wait_for around the upstream call.
Points: -2

ID: C1-05  Criterion: Operational  Severity: nit
Location: main.py:179-181
Problem: Startup still not fail-loud on Postgres (documented as pending in CLAUDE.md); watchlist load failure silently falls back to empty set.
Points: -2

ID: C1-06  Criterion: Adversarial input  Severity: nit
Location: remaining boundary
Problem: Not re-audited this pass; sent/wanted set growth from many distinct symbols bounded only by watchlist cap plus browser subs.
Points: -3 (shared with caps above)

Assessment of requested focus
- Can a watched or browser-held symbol end up unsubscribed: no. Unsubscribe only fires when sent and not wanted, evaluated under one lock against live state; watchlist never shrinks. Verifier fuzz (500 seeds) shows alternating calls and final upstream == wanted.
- Duplicate subscribes: prevented in normal paths by the sent set; only the contrived bug 2 can duplicate (harmless).
- Reconnect findings: bug 1 is the more real one (leaked subscription, low-to-minor severity, self-heals on next reconnect); bug 2 is nit-level. Neither blocks release.
- Deadlock: none seen; single upstream lock, manager->upstream order only, ref_count read is lock-free.
- Race tests: the key test fails on the iteration-3 baseline (assert 0 == 1) and passes 30/30 on current code; deterministic via explicit task/yield ordering rather than sleeps.

Not checked: re-running the PG-backed tests, uvicorn run, fuzz, and 30x loop (relied on verification report); frontend; FinnhubClient internals beyond the verifier's description.
