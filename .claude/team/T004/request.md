# Request (T004)

Cut Redis, TimescaleDB and webhooks from the project entirely (reviewer feedback item 1 of several; do ONLY this item).

In scope:
- Delete `backend/app/redis_cache.py` and `backend/app/webhooks.py`, every import/usage of them (e.g. in `main.py` `Services`), and their tests.
- Remove the related settings from `backend/app/config.py` and `.env.example` together (redis_url, all webhook_* settings, WEBHOOK_SIGNING_SECRET, anything Timescale-specific).
- Remove `webhook_url` from the rule model, the rules router payload, `db.py` schema/queries, and the frontend (`RuleForm.tsx`, `api/client.ts`, types).
- `db.py`: remove the TimescaleDB extension / hypertable statements so it runs on plain Postgres. Keep everything else in db.py as is.
- Remove the `redis` (and any hiredis) dependency from requirements.
- `docker-compose.yml`: drop the redis service; replace the TimescaleDB image with a plain Postgres 16 image; fix depends_on/env accordingly.
- CI workflow under `.github/workflows/`: drop the redis service; use plain Postgres instead of Timescale.
- README / docs references to Redis, TimescaleDB, webhooks: remove or update. Do NOT edit CLAUDE.md (the orchestrator updates it).
- When a rule fires, it must still be observable: keep any existing browser broadcast of the trigger; if the only effect was the webhook, log the trigger at INFO level instead.

Out of scope (later feedback items, do not touch):
- Browser websockets: `routers/ws.py`, `connection_manager.py`, `subscription_manager.py`, `useWebSocket.ts` stay.
- No new tables (no alerts/watchlist/latest_prices), no change to how rules are persisted or read (memory-first stays for now), no auth, no dependency pinning, no real-DB tests.

Acceptance:
1. `pytest` passes offline (from backend/).
2. `npm run build` and `npm test` pass (from frontend/).
3. `grep -rniE "redis|webhook|timescale|hypertable"` over backend/, frontend/src, docker-compose.yml, .github/, .env.example, requirements files returns nothing.
4. The app still starts (`uvicorn app.main:app`) with no Postgres configured, and a fired rule is still observable (broadcast or log).
