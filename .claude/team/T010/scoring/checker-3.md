Checker: 3
Ran product: partly (pytest offline: 122 passed, 19 skipped (real-Postgres, no TEST_DATABASE_URL); npm test: 12 passed). No uvicorn/docker run.
Score: 88/100

Criterion scores
  Conformance to the brief          23/25
  Cost of ownership                 17/20
  Extensibility                     18/20
  Risk to the business              18/20
  Time-to-value vs over-engineering 12/15

Findings
C3-01 | Conformance (docs accuracy) | minor | README.md:77-83, Authentication section
  Problem: README says a bad key or origin "closes it with code 1008". ws.py closes before accept(); Starlette answers the handshake with HTTP 403, so a browser sees 1006 and a test client sees 1008 only at the TestClient layer. The code comment in ws.py:17 says 403, README says 1008.
  Fix: say "the handshake is refused (HTTP 403 / close code 1008 in clients that expose it)". Points: -1
C3-02 | Conformance (docs usability) | minor | README.md:79-82
  Problem: Examples use $APP_API_KEY / $env:APP_API_KEY without saying the shell variable must be set (the .env file is not loaded into the shell). On Windows PowerShell 5.1 `curl` is an alias for Invoke-WebRequest, so the curl line fails there with "-H" error. Brief names Windows PowerShell as a focus.
  Fix: one line: set $env:APP_API_KEY="..." first; use curl.exe in PowerShell. Points: -1
C3-03 | Conformance (.env.example) | minor | .env.example:27 (VITE_BACKEND_URL=http://localhost:8000)
  Problem: Orchestrator resolution changed the vite default to 127.0.0.1 for IPv6 reasons, but .env.example still shows localhost:8000, and the new DATABASE_URL comment states the localhost-IPv6 rule. Inconsistent (a local user copying this to .env gets the problem the change avoids). Not in the request list, so arguably left alone deliberately.
  Fix: use 127.0.0.1 or note it. Points: -1
C3-04 | Time-to-value / usability | minor | frontend/src/components/Dashboard.tsx:10-28
  Problem: Key prompt has only a bare "API key" label. After a wrong key (401) the prompt silently reappears with no message that the key was rejected, so a user may think Save did nothing. A websocket auth failure alone is invisible (no 401 on WS). Request asked for minimal, so impact is small.
  Fix: a short "Key rejected, enter it again" line when shown via onUnauthorized. Points: -2
C3-05 | Over-engineering (mild) | nit | backend/app/security.py:41-62; frontend/src/api/client.ts:16-47
  Problem: install_key_mask walks handlers, handles dict/tuple args, three URL-quote forms and a try/except; client.ts adds a listener Set/pub-sub. Both are justified by the brief (mask in uvicorn logs; clear key and re-prompt on any 401) but are the heaviest parts of the diff. Backend style matches the codebase (docstrings, type hints); React style matches (function components, inline form).
  Points: -2
C3-06 | Verifiability | minor | backend/tests (pytest summary)
  Problem: 19 tests skipped here; the real-Postgres edits to test_rules_postgres.py (AUTH headers added to ~10 sites) were not exercised in my run. Edits are mechanical (headers=AUTH / app_api_key=TEST_KEY) and preserve each test's assertions; no weakening found. conftest/Dashboard.test changes likewise only add the key.
  Points: -1 (unverified)
C3-07 | Risk | minor | backend/app/main.py:239-244 + startup order
  Problem: Missing key makes lifespan raise RuntimeError (good, fail-loud, nothing started, tested for both persistence modes), but create_app() with empty key still installs the mask with an empty secret and returns normally, so failure appears only at lifespan. Matches the request ("lifespan raises"); noted only. Points: 0 (informational, folded into -1 under risk for no live uvicorn run to confirm masking end to end).

Scope discipline: all 13 requested items present and no unrequested file or feature found in the diff. Extra: test_auth.py asserts .env.example content (harmless). Ports statements in README/.env.example/compose match (host 5433 -> container 5432, 127.0.0.1 binds, DATABASE_URL default 5432, CORS origin localhost:5173 vs API at 127.0.0.1:8000 correct). vite.config host "localhost" and proxy 127.0.0.1 match the resolutions. Compose change is exactly the four lines requested.

CLAUDE.md should say (orchestrator): APP_API_KEY required, startup fails without it regardless of RULES_PERSISTENCE_ENABLED; /api/* needs X-API-Key (401), /health, /docs, /redoc, /openapi.json open; /ws needs Origin in CORS_ORIGINS (or none) and ?key=; security.py + key masking in uvicorn/app loggers; localhost-only compose bindings, Postgres host port 5433; new test commands unchanged; secrets invariant now includes APP_API_KEY.

Not checked: real uvicorn log masking, docker compose, real-Postgres test run, browser behavior of the 403 handshake, npm run build.
