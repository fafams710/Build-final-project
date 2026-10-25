# Checker 3 - T009 (scope, accuracy, tests)

(Saved by the orchestrator from the scorer's reply.)

Score: 87/100. Ran product: yes (offline pytest 82 passed / 17 skipped). Real-Postgres and docker not rerun.

Criterion scores: Conformance 22/25; Cost of ownership 17/20; Extensibility 17/20; Risk 17/20; Time-to-value 14/15.

Scope clean: only items 1, 3, 4; revised-planning.md untouched; compose +8/-1 (healthcheck, depends_on); no new settings.

## Findings

- C3-01 (minor, main.py `_load_state`): "startup failed" line always blames reachability/URL, even for schema or backfill failures; db.py:114 logs "persistence disabled" although the app aborts. -3
- C3-02 (minor, CLAUDE.md): claims opt-out 503 only holds without a usable DB. ORCHESTRATOR NOTE: incorrect - `_require_rules_pool` (db.py:183-185) raises when `_persist_rules` is False regardless of the pool, so rule/alert endpoints return 503 in opt-out mode; checker 1 (C1-05) agrees. -2
- C3-03 (nit, CLAUDE.md): backoff sentence omits the sleep after an unexpected writer error. -1
- C3-04 (nit, rules.py:11): `log` and `import logging` now unused. -1
- C3-05 (nit, main.py `Services.startup`): a raising `db.close()` masks the original error; cancellation skips cleanup. -1
- C3-06 (minor, test_rules_engine.py): `pytest.raises(RuntimeError)` is broad; mid-batch task_done only indirectly covered. -2

Checked OK: conftest client_no_db, test_api_symbols, replaced startup tests, item 1 and 4 tests, compose defaults.
Not checked: real-Postgres, docker compose config.
