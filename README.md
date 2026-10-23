# API Price & Stock Change Tracker

Live stock prices from Finnhub, streamed to a React dashboard, with threshold rules that fire webhooks the moment a price target or percentage move is hit.

## How it works

```
Finnhub --(1 websocket)--> FastAPI --> Redis (latest price)
                              |------> TimescaleDB (tick history, rules)
                              |------> Rule engine --> signed webhooks
                              '------> Connection manager --(websocket)--> React dashboard
```

- **One upstream connection.** Finnhub's free tier allows a single websocket. The client starts and stops with the FastAPI lifespan.
- **Shared subscriptions.** Browsers request symbols; the backend ref-counts them and only sends upstream subscribe/unsubscribe when the first browser joins or the last leaves.
- **Rules** (`price_above`, `price_below`, `percent_change` over a window) are held in memory for fast evaluation and persisted to Postgres. Webhooks are HMAC-signed and retried.

## Quick start

Requirements: Docker, plus a free [Finnhub](https://finnhub.io) API key.

```
cp .env.example .env     # set FINNHUB_API_KEY, POSTGRES_PASSWORD, WEBHOOK_SIGNING_SECRET
docker compose up --build
```

- Dashboard: http://localhost:5173
- API: http://localhost:8000 (health at `/health`)

Without a `FINNHUB_API_KEY` the backend still starts, but no market data flows.

## Local development

Backend:
```
cd backend
pip install -r requirements.txt
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
| `GET /api/symbols/{symbol}/price` | Last cached price |
| `POST /api/rules` | Create a rule |
| `GET /api/rules?symbol=` | List rules |
| `DELETE /api/rules/{id}` | Delete a rule |
| `WS /ws` | Send `{"action": "subscribe" \| "unsubscribe", "symbol": "AAPL"}`; receive `tick` messages |

Example rule:
```json
{"symbol": "AAPL", "type": "price_above", "threshold": 200, "webhook_url": "https://example.com/hook"}
```
`percent_change` rules also require `window_seconds`.

## Configuration

All settings are environment variables; see `.env.example`. Secrets live in `.env` only and are never committed.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs backend `pytest` against Redis and TimescaleDB services, and the frontend build and tests, on every push and pull request.
