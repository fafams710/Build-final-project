# Request (T007)

Pin dependencies (reviewer feedback: "Dependencies aren't pinned (>=, timescale:latest-pg16)"). Do ONLY this item.

Required:
- Split backend requirements:
  - `backend/requirements.txt`: runtime packages only, pinned with `==` (fastapi, uvicorn[standard], websockets, httpx if used at runtime, asyncpg, pydantic, pydantic-settings).
  - `backend/requirements-dev.txt` (new): `-r requirements.txt` plus pinned test tools (pytest, pytest-asyncio, and httpx if only used by tests).
- Pin to the exact versions currently installed in the project venv (`.venv` at repo root; check with `pip freeze` / `pip show`), since the suite passes with them. Do not upgrade anything.
- Decide httpx placement by grepping backend/app vs backend/tests.
- `.github/workflows/ci.yml`: backend installs `requirements-dev.txt`; frontend uses `npm ci` (package-lock.json is committed) instead of `npm install` if not already.
- Backend Dockerfile (if present) installs only `requirements.txt`. Frontend Dockerfile uses `npm ci` if it installs deps.
- `docker-compose.yml` and ci.yml: pin the Postgres image to an exact minor tag (e.g. `postgres:16.x`, pick a current 16 minor; both files use the same tag).
- Update README / CLAUDE.md commands that say `pip install -r requirements.txt` for development to use `requirements-dev.txt` - except do NOT edit CLAUDE.md (orchestrator does it); README only.

Out of scope: upgrading versions, frontend package.json ranges (the lockfile pins them), anything else.

Acceptance:
1. No `>=`, `~=`, `^` or unpinned names in backend requirement files; every line `name==x.y.z` (or `-r`).
2. A fresh venv (outside the repo) installing `requirements-dev.txt` runs `pytest` green from backend/ (33 passed, 6 skipped without TEST_DATABASE_URL).
3. Runtime requirements do not include pytest / pytest-asyncio.
4. ci.yml and docker-compose.yml use the same exact postgres tag; ci.yml parses as YAML and installs requirements-dev.txt; npm ci used.
5. `npm ci && npm run build && npm test` pass in frontend/.
