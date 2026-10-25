# API Price & Stock Change Tracker

Live stock prices from Finnhub, streamed to a React dashboard, with threshold rules that are logged the moment a price target or percentage move is hit.

## How it works

```
Finnhub --(1 websocket)--> FastAPI --> Postgres (optional: tick history, rules)
                              |------> Rule engine --> INFO log line per fired rule
                              '------> Connection manager --(websocket)--> React dashboard
```

- **One upstream connection.** Finnhub's free tier allows a single websocket. The client starts and stops with the FastAPI lifespan.
- **Shared subscriptions.** Browsers request symbols; the backend ref-counts them and only sends upstream subscribe/unsubscribe when the first browser joins or the last leaves.
- **Rules** (`price_above`, `price_below`, `percent_change` over a window) are held in memory for fast evaluation and persisted to Postgres. A fired rule is logged at INFO level.

## Quick start

Requirements: Docker, plus a free [Finnhub](https://finnhub.io) API key.

```
cp .env.example .env     # set FINNHUB_API_KEY, POSTGRES_PASSWORD, APP_API_KEY
docker compose up --build
```

Generate `APP_API_KEY` (a shared secret; the backend refuses to start without it):
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- Dashboard: open http://localhost:5173 (must match `CORS_ORIGINS`) and enter the `APP_API_KEY` when asked. It is kept in the browser session only and asked for again if the API rejects it.
- API: http://127.0.0.1:8000 (health at `/health`)
- All ports are published on `127.0.0.1` only (not the LAN). Postgres is on host port 5433.

Without a `FINNHUB_API_KEY` the backend still starts, but no market data flows.

## Local development

Backend:
```
cd backend
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

Frontend:
```
cd frontend
npm install
npm run dev
npm test
```

Tests run fully offline using a fake Finnhub server.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /api/symbols` | Active symbols and subscriber counts |
| `POST /api/rules` | Create a rule |
| `GET /api/rules?symbol=` | List rules |
| `DELETE /api/rules/{id}` | Delete a rule |
| `WS /ws?key=` | Send `{"action": "subscribe" \| "unsubscribe", "symbol": "AAPL"}`; receive `tick` messages |

Example rule:
```json
{"symbol": "AAPL", "type": "price_above", "threshold": 200}
```
`percent_change` rules also require `window_seconds`.

## Authentication

Every `/api/*` route needs the header `X-API-Key: <APP_API_KEY>`; otherwise the answer is `401 {"detail": "invalid or missing API key"}`. `/health` is open. The browser websocket is `ws://host/ws?key=<APP_API_KEY>` and also checks the `Origin` header against `CORS_ORIGINS`; a bad key or origin closes it with code 1008. Uvicorn log lines mask `key=` values.

```
curl -H "X-API-Key: $APP_API_KEY" http://127.0.0.1:8000/api/rules
```
```
Invoke-RestMethod -Headers @{ "X-API-Key" = $env:APP_API_KEY } http://127.0.0.1:8000/api/rules
```

## Configuration

All settings are environment variables; see `.env.example`. Secrets live in `.env` only and are never committed.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs backend `pytest` against a Postgres service, and the frontend build and tests, on every push and pull request.
