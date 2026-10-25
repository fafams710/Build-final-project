Checker 1 (iteration 2). Ran product: yes (pytest offline 70 passed/15 skipped; scratch repro, since deleted).
Score: 72/100

C1-I2-01 major, main.py:76-81 + 100: watched symbol can still end up not subscribed upstream (residual of the race).
 Repro: watch_symbol("X") holds _upstream_lock in a blocked subscribe; browser subscribes AAPL (ref_count=1, _up_subscribe waits on lock, AAPL not yet watched); watch_symbol("AAPL") adds to watchlist, sees ref_count>0 and returns on the fast path; lock released, browser's _up_subscribe re-checks "in watchlist" and returns. Nobody subscribed AAPL: final upstream state {'X'}; scratch test failed with AssertionError {'X'}. Self-heals only on reconnect. Both paths defer to each other, so the pending one can be skipped.
 Fix: in _up_subscribe, under the lock, skip only if watched AND already sent upstream (track an upstream-sent set), or have watch_symbol not rely on ref_count (track actual upstream state).
 Points -14

C1-I2-02 minor, main.py:77,95-101: lock-free fast paths are the cause of 01 and contradict the spec (spec said lock unconditionally); the deadlock they avoid indicates the design relies on inline awaits. No documented invariant. Points -4

C1-I2-03 minor, tests: race test (test_watchlist_alerts.py:323-336) is deterministic (Events, no timing sleeps; _yield only yields), covers the stated scenario, and reportedly fails on baseline. But no test covers the independent-lock interleaving above (blocked upstream on another symbol). Points -5

C1-I2-04 nit, main.py:22-31: _configure_logging skips handler when root has handlers; under uvicorn root typically has none so fine. Handler format hardcoded. Test depends on global logger state. Points -2

Verified OK: lock order manager->upstream is consistent (watch_symbol takes only upstream lock; no reverse acquisition), no deadlock found; failure inside lock releases it; no duplicate subscribe in the tested cases; the in-flight-unsubscribe scenario itself is fixed (watch_symbol waits and subscribes after unsubscribe). db.py drain/backoff not deeply graded (focus on race).
Not checked: real-Postgres run; db.py drain details; frontend.
