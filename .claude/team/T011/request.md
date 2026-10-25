# T011 - M11: auth secure by default for every /api route (user-approved)

HARD CONSTRAINT: do NOT run `git commit`, `git push`, `git stash`, `git checkout`, `git switch`, `git restore`,
`git reset`, `gh` commands, or anything that changes git history, branches or discards working-tree changes. Only edit
local files; the user verifies the code and commits. Never print values from `.env`. Use precise edits. Run pytest as
`python -X faulthandler -m pytest -o faulthandler_timeout=120`; leave no python/node/postgres process you started.

Context: T010 added API-key auth. Read CLAUDE.md (the "Authentication (T010...)" paragraph and known issue 7) and
`.claude/team/T010/` (spec.xml, impl/iteration-1.xml, scoring/checker-1.md finding C1-01).

## Problem
In `backend/app/main.py` `create_app`, the key check is attached per router:
`app.include_router(symbols.router, dependencies=auth)` (same for rules and alerts). A future `/api` router included
without `dependencies=auth` would be public and no test would notice.

## User decision: option (a) + the route-walk test from (b)
1. One parent router for the API that carries `Depends(require_api_key)`; symbols, rules and alerts routers are
   included into it, and the app includes only the parent. Read how each router declares its paths today (full
   `/api/...` paths vs prefixes) and choose the arrangement that keeps every existing URL exactly the same. Make the
   parent the obvious single place to add future API routers (short comment).
2. A test that walks `app.routes` and fails if any route whose path starts with `/api` does not carry
   `require_api_key` in its dependencies (check the dependant tree, not just names in source). It must also fail
   when a router is included outside the parent: prove that in the test itself (e.g. build an app, include a dummy
   `/api/x` router directly, assert the walker flags it) rather than by editing production code.
3. Unchanged behaviour (existing tests must keep passing without edits to their assertions):
   - 401 `{"detail": "invalid or missing API key"}` for missing/wrong key, before body validation (401 not 422);
   - `/health`, `/docs`, `/redoc`, `/openapi.json` open; CORS preflight works without the key;
   - `/ws` keeps its own Origin + `?key=` check (it is not under the parent router);
   - every existing URL, method and status code unchanged; OpenAPI paths unchanged.

## Out of scope
Everything else (other medium items, websocket changes, new endpoints, docs other than a one-line CLAUDE.md note,
which the orchestrator writes).

## Done when
Offline pytest passes; real-Postgres pytest on a throwaway PG17 cluster ("C:/Program Files/PostgreSQL/17/bin",
initdb trust auth, temp dir outside the repo, port 55521) passes, 0 skipped, then stopped and deleted. Report to
`.claude/team/T011/impl/iteration-1.xml`.
