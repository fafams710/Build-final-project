Checker:        3
Ran product:    yes (pytest only; TEST_DATABASE_URL unset: 58 passed, 15 skipped). Live/e2e and frontend not re-run; relied on verification file for those.
Score:          88/100

Criterion scores
  Conformance to the brief            22/25
  Cost of ownership                   17/20
  Extensibility as an asset           17/20
  Risk to the business                18/20
  Time-to-value vs over-engineering   14/15

Findings
ID: C3-01  Criterion: Conformance to the brief  Severity: minor
Location: backend/app/main.py (_up_unsubscribe / watch_symbol)
Problem: Race not covered: browser's last unsubscribe awaits upstream while rule path runs; watch_symbol sees ref_count==0 and subscribes, then the in-flight unsubscribe lands (wrong unsubscribe). Spec AC7 interleaving only mentions the subscribe direction.
Evidence: _up_unsubscribe checks watchlist before the await; watch_symbol checks ref_count after adding to watchlist.
Fix: Re-check watchlist after await or serialize with the subscription lock.
Points: -2

ID: C3-02  Criterion: Conformance to the brief  Severity: minor
Location: backend/app/main.py:~120 startup
Problem: Real-PG/e2e/frontend claims (0 skipped, 26 e2e checks, npm test) are not reproducible in the default run (15 skipped).
Evidence: pytest -> 58 passed, 15 skipped.
Fix: Document the one command to run the real-PG pass.
Points: -1

ID: C3-03  Criterion: Cost of ownership  Severity: nit
Location: backend/app/routers/rules.py, alerts.py
Problem: Duplicated try/except-503 pattern in two routers (requested by brief, but one more copy to maintain); watchlist state split between Services.watchlist and DB with no accessor.
Fix: Later, shared helper.
Points: -3

ID: C3-04  Criterion: Extensibility  Severity: minor
Location: backend/app/main.py Services
Problem: Watchlist is only-grows in memory; no removal path, so the future D2 DELETE needs reworked subscribe/unsubscribe coordination. Alerts queue lossy by design.
Points: -3

ID: C3-05  Criterion: Risk to the business  Severity: minor
Location: backend/app/routers/rules.py: broad `except Exception` -> 503
Problem: Any bug maps to a generic 503 (kept per brief, out of scope); no timeout on upstream subscribe in watch_symbol could hold request.
Evidence: await self.upstream.subscribe(symbol) with no wait_for.
Fix: asyncio.wait_for around subscribe.
Points: -2

ID: C3-06  Criterion: Time-to-value  Severity: nit
Location: startup backfill
Problem: Backfill of old rules goes slightly beyond request text (documented assumption, justified).
Points: -1

Not checked: live e2e, real-Postgres tests, frontend build/test, concurrency cap test (relied on verification/iteration-1.xml). Cleanup grep for X1/X2/X4 run: no matches in backend.
