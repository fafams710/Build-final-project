# T010 - API key auth (option B), websocket Origin + key, localhost-only bindings (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout`, `git switch`, `git restore`,
`git reset`, or anything that changes git history, branches or discards working-tree changes. Only edit local
files; the user reviews and commits. Never print or log secret values from `.env`. Use precise edits. Run pytest as
`python -X faulthandler -m pytest -o faulthandler_timeout=120`; leave no python/node/postgres process you started.

Baseline: HEAD `dde2e39` (same tree as origin/main `cd1613d`), clean working tree. Read CLAUDE.md first (it is
git-ignored and describes current behaviour, invariants and known issues) and revised-planning.md.

## User decisions
- H1 option B: a single shared key. No accounts, no JWT.
- Q1 = 1: the browser websocket passes the key as a query parameter `/ws?key=...`.
- Q2 = 1: if `APP_API_KEY` is empty/missing, the backend refuses to start with a clear error (fail-loud, like T009).
- Q3 = 2: compose publishes Postgres on host `127.0.0.1:5433` (container still 5432).
- Plus (approved with option B): localhost-only bindings and a websocket Origin check (M1).

## Backend
1. `config.py` + `.env.example`: `APP_API_KEY: str = ""` with a comment on how to generate one
   (`python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Startup fails loudly (lifespan raises, clear "startup failed: APP_API_KEY is not set" error, nothing started) when
   the key is empty. Decide and state how this interacts with `RULES_PERSISTENCE_ENABLED=false` (the key is required
   regardless - auth is not tied to persistence).
3. Every `/api/*` route requires header `X-API-Key` equal to the key, compared with `secrets.compare_digest`;
   missing or wrong -> 401 `{"detail": "invalid or missing API key"}` with no hint about which. `/health` stays open.
   Apply it once (router dependency or middleware), not per handler. CORS must still answer preflight (OPTIONS)
   without the key, and `allow_headers` must include `X-API-Key` (currently "*", fine).
4. `/ws`: before `accept()`, reject (close with policy-violation code 1008) when (a) the `Origin` header is present and
   not in `settings.cors_origin_list`, or (b) query `key` does not match (compare_digest). Missing Origin (non-browser
   clients) is allowed only if the key matches.
5. The key must never appear in logs. Uvicorn logs websocket handshakes and access lines including the query string
   (`/ws?key=...`); add a logging filter (or equivalent) so the key value is masked in uvicorn's access/error log
   records. Verify with a real uvicorn run.
6. Tests: a test key in test settings/fixtures; tests for 401 on each /api route group without/with wrong key, 200
   with the right key, /health open, OPTIONS preflight works, /ws rejected for bad Origin / bad key / missing key and
   accepted for good key, startup fails without a key. Existing tests updated to send the key; real-Postgres tests too.

## Frontend
7. `api/client.ts`: send `X-API-Key` from `sessionStorage` on every API call; `wsUrl()` appends `?key=` (URL-encoded).
8. Dashboard: if no key is stored, show a small key prompt (password-type input + Save) before the rest; on any 401,
   clear the stored key and show the prompt again. Keep it minimal and in the existing component style.
9. Frontend tests: prompt shown without a key; key sent as header; 401 clears it. `npm test` and `npm run build` pass.

## Localhost-only bindings (H3 + LAN exposure)
10. `docker-compose.yml`: `postgres` ports `"127.0.0.1:5433:5432"`, `backend` `"127.0.0.1:8000:8000"`, `frontend`
    `"127.0.0.1:5173:5173"`; backend gets `APP_API_KEY: ${APP_API_KEY:?set APP_API_KEY in .env}`. No other compose
    change. (The frontend container already runs vite with `--host 0.0.0.0`, which is fine inside the container.)
11. `frontend/vite.config.ts`: `server.host` from `true` to `"localhost"`.
12. `.env.example`: use `127.0.0.1` instead of `localhost` in `DATABASE_URL` and the `TEST_DATABASE_URL` example;
    add a comment that the compose Postgres is on host port 5433 and a local Postgres install is usually 5432.
    Keep `CORS_ORIGINS` default as is but document that the dashboard must be opened at an origin listed there.

## Docs
13. README: how to set `APP_API_KEY`, that the dashboard asks for it, how to call the API with curl/Invoke-RestMethod
    (`-H "X-API-Key: ..."`), and that ports are bound to 127.0.0.1. CLAUDE.md is edited by the orchestrator - do not
    edit it; list in your report what CLAUDE.md should say.

## Out of scope
The user's real `.env` (the orchestrator adds an empty `APP_API_KEY=` line by key name), Docker runs (not
installed), removing the browser websocket (M5), rate limiting, other known issues.

## Done when
Offline pytest passes; real-Postgres pytest on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port 55511) passes, 0 skipped, cluster stopped and deleted;
`npm test` and `npm run build` pass.

## Orchestrator resolutions after spec (apply these)
- Vite proxy default target: change `http://localhost:8000` to `http://127.0.0.1:8000` in vite.config.ts (same
  IPv6 reason as DATABASE_URL; VITE_BACKEND_URL override unchanged, compose still sets http://backend:8000).
- README Quick start must mention APP_API_KEY (generate, put in .env, enter in the dashboard) and opening the
  dashboard at http://localhost:5173 (must match CORS_ORIGINS).
- Leave /docs, /redoc, /openapi.json open; keep DATABASE_URL default port 5432 with the 5433 compose comment.
