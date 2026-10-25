# Checker 2 - T010 (reliability, tests)

(Saved by the orchestrator: the scorer could not write the file and returned its report in its reply.)

Score: 84/100. Ran product: partly (offline pytest 122 passed / 19 skipped; npm test 12 passed). Live uvicorn run was blocked; relied on the verifier's evidence.

Criterion scores: Fault containment 21/25; Defined behavior 17/20; External data 17/20; Commandability 13/15; Verifiability 16/20.

Strengths: startup key check first in lifespan (no db connect, no upstream); auth as one router dependency, 401 before 422, preflight works; mask filter never drops records, idempotent; tests deterministic.

## Findings

- C2-01 (minor, Dashboard.tsx:80-84 / useWebSocket.ts): ws reconnect has no auth-failure branch; a rejected key retries forever (capped 15 s backoff) until a REST 401 clears the key. -2
- C2-02 (minor, test_auth.py:222-263): masking tests mutate global uvicorn loggers without restore; no repeatable subprocess test of real uvicorn. -3
- C2-03 (minor, security.py:10,61): mask not on `websockets`/`asyncio` loggers (debug level could log `?key=`); handlers added after create_app not covered; percent-encoded param name not matched. -3
- C2-04 (nit, main.py:233-244 vs security.py:19): startup validates `strip()` but compare is raw; a key with surrounding whitespace starts but will not match. Strip once in Settings. -2
- C2-05 (nit, .env.example): `VITE_BACKEND_URL=http://localhost:8000` still uses localhost. -2
- C2-06 (nit, ws.py:17-24): close(1008) before accept becomes HTTP 403 for real clients; docs should say 403. -1
- C2-07 (minor, ApiKey.test.tsx:55-72): no test of re-entering a key after a 401 and retrying with the new key; no ws-header assertion. -2

Not checked: live uvicorn / --reload, real-Postgres tests, real browser, Docker.
