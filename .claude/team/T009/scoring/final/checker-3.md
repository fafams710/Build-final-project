# Checker 3 - T009 final (scope, docs accuracy, tests)

(Saved by the orchestrator from the scorer's reply.)

Score: 89/100. Ran product: yes (offline pytest 93 passed / 19 skipped). Real-PG and docker not run.

Criterion scores: Conformance 23/25; Cost of ownership 18/20; Extensibility 18/20; Risk 18/20; Time-to-value 12/15.

Scope: each iteration did exactly its items; compose diff = healthcheck + depends_on; no new settings; deferred items untouched. Changed pre-existing tests still test what they should. CLAUDE.md statements checked against code match (except below). Log wording clear, no URL leak.

## Findings

- C3-F1 (minor, CLAUDE.md project paragraph): does not mention malformed rows skipped at startup or that GET /api/rules skips them (fewer than `limit`). -2
- C3-F2 (nit, revised-planning.md ~171-172): "rules, alerts and the watchlist are not available (their endpoints return 503)" - no watchlist endpoint exists; say rules/alerts endpoints 503, watchlist stays empty. -1
- C3-F3 (nit, revised-planning.md ~173-175): "dropped (logged, with a rate-limited warning)" - only the queue-full warning is rate-limited; failed writes log one unthrottled summary per batch. -1
- C3-F4 (nit, main.py `_load_state`): defensive `getattr(self.db, "last_error", None)` only for fakes. -1
- C3-F5 (nit, db.py list_alerts): no malformed-row skip for alerts (low risk). -1
- C3-F6 (nit, CLAUDE.md): heading "Known open issues (from T008 review)" now also holds a T009 limitation. -1
- (Deferred, not deducted) CLAUDE.md says the writer sleeps only if a row still failed; it also sleeps after an unexpected writer error.

Not checked: real-PG tests, docker compose config, frontend.
