Checker: 2
Ran product: yes (pytest only; no servers)
Score: 84/100

Evidence: full suite 130 passed, 19 skipped; new file 8 passed. Installed fastapi 0.162.0, but requirements.txt pins fastapi==0.159.1 (not tested on the pin). On 0.162 app.routes contains Route objects plus an `_IncludedRouter` node.

Criterion scores
  Fault enumeration and containment   21/25
  Defined behavior over input domain  17/20
  External data treated as hostile    17/20
  Commandability                      14/15
  Verifiability and structure         15/20

Findings
ID: C2-01
Criterion: Verifiability and structure
Severity: major
Location: backend/tests/test_api_auth_coverage.py:16-24, 27-32
Problem: Walker uses duck typing on private FastAPI internals (`effective_candidates`, `starlette_route`). Unknown node types fall through and are skipped silently. Any node with no path and no `effective_candidates` yields path "" and is skipped as non-/api. The test-suite guards (positive count, named paths, dummy-flagged tests) catch total breakage, but a partial change would not be caught. Example: a new wrapper that hides a subset of routes.
Evidence: `_resolve` returns path "" for unknown nodes and `_is_api_path("")` is False, so they are dropped with no error.
Fix: In `_effective_routes`/`_resolve`, raise on any node that is neither a recognised route nor a candidate holder.
Points: -4

ID: C2-02
Criterion: Verifiability and structure
Severity: minor
Location: backend/requirements.txt vs installed env
Problem: The pin is fastapi 0.159.1 but the tests ran against 0.162.0. I could not verify the walker on the pinned version. The docstring claims to handle both old (copied routes) and new (lazy) layouts, and only one was exercised.
Evidence: `pip list` shows 0.162.0; requirements.txt says 0.159.1.
Fix: Run the suite in a clean venv from requirements.txt; or pin to what was verified.
Points: -3

ID: C2-03
Criterion: Fault enumeration and containment
Severity: minor
Location: test_api_auth_coverage.py:41-49 (`_has_key_check`)
Problem: The identity check `dependant.call is require_api_key` only covers the direct function. A wrapped/partial or class-based replacement of the check, or a router-level override, would be reported as unprotected (fails safe, false positive) and the walker never checks a security-scheme dependency or that the check runs before body validation. The 401-before-422 ordering relies on existing test_auth.py only.
Fix: Acceptable; note it, or add a behavioural 401 test per walked path.
Points: -2

ID: C2-04
Criterion: Defined behavior over input domain
Severity: minor
Location: test_api_auth_coverage.py:96-103
Problem: The tests use an `/api/x` dummy only for GET at a single path. No Mount under /api case, no websocket route under /api, and no `/api` exact or `/apiary` boundary case. The spec lists these as edge cases, but no test pins them.
Evidence: Spec edge_cases lists the Mount case; no test creates a Mount.
Fix: Add a test with `app.mount("/api/m", ...)` expecting it flagged, and one for `/apiary` not walked.
Points: -3

ID: C2-05
Criterion: External data treated as hostile
Severity: nit
Location: test_docs_and_schema_open_without_key
Problem: The only boundary behavior pinned here is that docs are open. Hostile-key cases (empty, non-ASCII) rely on test_auth.py, which is outside this diff and is fine. Minor lack of cross-link.
Points: -3

Answers to the focus questions
- Deterministic: yes. All 8 are in-process, use fakes, no clock, no network, no ordering dependence. Each calls `FakeFinnhubClient.instances.clear()`.
- Meaningful: yes. Real-app, negative (outside parent, non-key dependency), positive control, nested dependency, open routes, idempotency and docs-open tests each map to a spec criterion.
- Private internals: yes (`_IncludedRouter` is reached only via the public-looking `effective_candidates` attribute, plus `starlette_route`). It fails loudly for total breakage (zero routes inspected, the named-paths assertion, and the dummy tests would fail) but not for partial change (C2-01).
- Repeated create_app: safe. The parent `APIRouter` is built per call and the idempotency test (three apps, equal counts) passes. The child routers are module-level but are only read, so there is no accumulation.

Not checked: real-Postgres run (19 skipped); behaviour on fastapi 0.159.1; before/after OpenAPI byte diff; test_auth.py content in detail.
