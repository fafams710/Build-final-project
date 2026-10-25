# Checker 3 - T011 (scope, readability)

(Saved by the orchestrator from the scorer's reply.)

Score: 90/100. Ran product: yes (offline pytest 130 passed / 19 skipped). No servers started.

Criterion scores: Conformance 24/25; Cost of ownership 18/20; Extensibility 18/20; Risk 19/20; Time-to-value 11/15.

Notes: main.py diff minimal (parent APIRouter with Depends(require_api_key)); URLs/statuses unchanged; new routers secure by default; good foundation for M5.

## Findings

- C3-01 (minor, test_api_auth_coverage.py:16-37): compatibility code for two FastAPI route layouts; only one branch exercised. -3
- C3-02 (nit, tests:101-135): two extra tests (repeated create_app, docs open) beyond the brief; the latter overlaps existing tests. -1
- C3-03 (minor, main.py:261-269): guard covers only paths starting with `/api`; a future router outside `/api` is not covered; `/health`/`/ws` exemption only in a comment. -2
- C3-04 (nit, main.py:262-264): comment slightly wordy. -1
- C3-05 (nit): real-Postgres run not verified by this checker. -1

Not checked: real-Postgres run, frontend, CLAUDE.md note.
