# T008 iteration 2 - fix pass (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout -- <file>`, `git reset`, or anything
that changes git history or discards working-tree changes. Only edit local files. The user reviews the diff.

Context: T008 (watchlist + alerts, L1/L2) is implemented as uncommitted changes on branch
`feat/T008-watchlist-alerts`. Read `.claude/team/T008/spec.xml`, `impl/iteration-1.xml`,
`verification/iteration-1.xml` and `scoring/checker-*.md` for background. A snapshot of the
pre-fix state is in `.claude/team/T008/iteration-2-baseline/` (do not edit it).

Fix exactly these four items, nothing else:

1. Unsubscribe race (scorer findings C2-02, C3-01). While a browser's last unsubscribe for a
   symbol is in flight upstream, a rule creation for the same symbol (`Services.watch_symbol`)
   can send its subscribe; if the unsubscribe lands last, the watched symbol ends up
   unsubscribed upstream. Make it impossible for a watched symbol to end up unsubscribed
   (e.g. serialize upstream subscribe/unsubscribe decisions under one lock shared with
   SubscriptionManager, or re-check the watchlist after the unsubscribe await and resubscribe).
   Add a deterministic test (no sleeps-for-timing flakiness; use asyncio Events/a fake upstream
   that blocks the unsubscribe until released) that FAILS on the baseline code and passes after.
   Preserve invariant: no duplicate subscribe for a symbol that is already subscribed.
2. Drain queued alerts on shutdown: before the alert writer is cancelled / the pool closes,
   flush what is queued, bounded by a short timeout (a few seconds); never hang shutdown.
3. Backoff after a failed alert batch write (scorer C2-03): sleep briefly (e.g. 1s, capped)
   after a batch fails so a down Postgres does not cause a hot retry/log loop. Must not block
   the tick path (on_tick still only does put_nowait).
4. Logging: configure logging at INFO for the `app` loggers at startup so the existing
   "rule fired: ..." line appears in `uvicorn app.main:app` and docker output. Keep it minimal
   (e.g. a handler/level set in create_app or module level), do not reconfigure uvicorn's loggers,
   do not add a dependency or a settings knob unless trivial.

Tests: `pytest` from backend/ must pass offline; real-Postgres tests via TEST_DATABASE_URL must
still pass. Follow CLAUDE.md conventions. Keep changes minimal.
