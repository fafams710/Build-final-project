# Checker 1 - T010 (security)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 86/100. Ran product: yes (offline pytest 122 passed / 19 skipped; two real uvicorn runs on 127.0.0.1 with curl and a websockets client; all processes killed).

Criterion scores: Architecture 17/20; Error model 17/20; Interface 13/15; Adversarial input 13/15; Operational 13/15; Dependency hygiene 13/15.

Verified: 401 for missing/wrong/non-ASCII key (UTF-8 compare_digest); 200 with key; preflight and /health open; hostile-Origin POST without key 401; HEAD 405, trailing slash 307 to authed route, unknown /api 404; /ws accepts good key with allowed or no Origin, rejects bad Origin / wrong / missing / non-ASCII key and `/ws/` with identical 403s; uvicorn logs show `key=***`, key never in logs; compose binds 127.0.0.1 only with `APP_API_KEY:?`; frontend sessionStorage, password input, 401 clears key, ws key URL-encoded.

## Findings

- C1-01 (minor, main.py:261-265): auth is opt-in per `include_router`; a future /api router included without it is open. No route-walk test. Fix: parent `APIRouter(prefix="/api")` with the dependency, or a test asserting every /api route has it. -3
- C1-02 (minor, config/lifespan): only empty/whitespace keys rejected; a 1-character key starts. No failed-attempt logging. Fix: minimum length (e.g. 16), log failures at WARNING. -2
- C1-03 (minor, security.py:57-69): masking only on uvicorn + app loggers/handlers; not root/websockets/fastapi; a custom --log-config or later dictConfig drops handler filters. -2
- C1-04 (minor, main.py:269): import-time `create_app()` installs the mask as a global side effect; a bare import succeeds with an empty key (fail-loud only in lifespan). -2
- C1-05 (nit, client.ts/Dashboard): a ws rejection with a bad stored key is not handled; only a REST 401 brings back the prompt. -1
- C1-06 (minor, requirements): Starlette TestClient httpx deprecation warning (not a T010 regression). -2
- Other: no repeatable test of real-uvicorn masking (-3); /docs, /redoc, /openapi.json open (orchestrator decision, -4 total); unpinned transitive deps (-1).

Not checked: Docker, real-Postgres pytest, npm test/build, README, browser.
