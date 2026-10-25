# T008 iteration 3 - fix the race regression from iteration 2 (+ small scorer items)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout -- <file>`, `git restore`, `git reset`,
or anything that changes git history or discards working-tree changes. Only edit local files. Do not edit the
`iteration-2-baseline/` or `iteration-3-baseline/` folders. Edit files with precise edits (no whole-file string
replacement tricks; iteration 2 clobbered db.py that way). Never leave background processes running; if a test
run hangs, kill only the process you started.

Background: read `.claude/team/T008/request-iteration-2.md`, `spec-iteration-2.xml`, `impl/iteration-2.xml`,
`verification/iteration-2.xml`, `scoring/iteration-2/checker-{1,2,3}.md`. Pre-iteration-3 copies of the three
files are in `.claude/team/T008/iteration-3-baseline/`.

## 1. Race regression (checker 1, C1-I2-01, MAJOR, confirmed by the orchestrator)

Iteration 2 made `Services._up_subscribe` wait on `_upstream_lock`. Now:
1. `watch_symbol("X")` holds `_upstream_lock` in a slow upstream subscribe.
2. A browser subscribes AAPL: SubscriptionManager sets ref_count=1, then `_up_subscribe("AAPL")` waits on the lock.
3. `watch_symbol("AAPL")` adds AAPL to the watchlist, sees ref_count>0, returns (assumes the browser sends it).
4. Lock frees; the browser's `_up_subscribe` sees AAPL in the watchlist, returns (assumes the rule path sent it).
Result: AAPL is never subscribed upstream.

Required design (replace the "who defers to whom" guessing with reconciliation against what was actually sent):
- `Services` keeps `self._upstream_sent: set[str]` = symbols for which an upstream subscribe has been sent and
  not since unsubscribed.
- One helper, e.g. `async def _reconcile(self, symbol)`, under `_upstream_lock`: wanted = symbol in watchlist or
  `subscriptions.ref_count(symbol) > 0`. If wanted and not sent -> `upstream.subscribe`, mark sent. If not wanted and
  sent -> `upstream.unsubscribe`, unmark. Otherwise nothing. Decide what "sent" means on upstream call failure
  consistently with the existing logging-only failure behaviour and with reconnect (on reconnect, FinnhubClient
  resubscribes `get_symbols()` = the wanted set, so after a reconnect the sent set should equal the wanted set -
  pick a simple, documented approach; a failed subscribe should not mark the symbol as sent).
- `_up_subscribe`, `_up_unsubscribe` and `watch_symbol` (after adding to the watchlist) all call `_reconcile`. No
  lock-free fast paths that skip reconciliation. Keep invariants: no duplicate subscribe, a watched symbol is never
  left unsubscribed, a browser leaving a watched symbol sends no unsubscribe.
- The iteration-2 deadlock with existing tests (impl/iteration-2.xml explains it) must not come back; if a test
  awaited inline while another task held the lock in a blocked subscribe, fix the test's structure only if the
  test was relying on the broken behaviour, and explain it in the report.
- Add a deterministic test for the exact interleaving above (fails on iteration-3-baseline, passes after), using
  Events and no timing sleeps. Keep the two iteration-2 race tests passing.
- Update the comment above these methods (stale "Neither decision has an await..." text, checker 3 C3-06).

## 2. Small items
- C2-02 / C3-04: back off only if at least one row in the per-row retry also failed.
- C2-01: during the per-row retry, log one summary line per batch (e.g. "N of M alerts failed: <ExcType>") instead
  of one line per failing row.
- C2-03: `_drain_alerts` skips the drain when the alert worker is already done.
- C3-05: when `_configure_logging` adds its own handler, set `propagate = False` on the `app` logger so records are
  not printed twice if root gains a handler later. Keep pytest caplog working (caplog's handler is on root; check
  the logging test still passes and the "rule fired" record is still captured).
- C3-03: two blank lines after `_configure_logging` (PEP 8).

Out of scope: everything else (no settings knobs, no frontend, no subscription_manager.py changes unless
strictly required for item 1 - if required, explain why).

Tests: `pytest` from backend/ offline must pass; real-Postgres tests (TEST_DATABASE_URL against a throwaway PG17
cluster outside the repo, non-default port, stopped and deleted afterwards) must pass with 0 skipped.
