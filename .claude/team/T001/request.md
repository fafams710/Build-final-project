# Request (T001)

Build "API Price & Stock Change Tracker": real-time/low-latency monitoring of stock prices via Finnhub, broadcast to an interactive dashboard, with custom threshold triggers held in memory that fire instant webhooks when price targets / volatility are hit. Async Python backend with stream processing; full CI/CD automation.

Limitation: Finnhub free tier allows ONE concurrent websocket connection upstream.

Stack (fixed):
- Market data: Finnhub (Alpaca mentioned as alternative; Finnhub chosen) via websocket. API key goes in .env ONLY, never committed (.env.example + .gitignore required).
- Backend: Python + FastAPI (async)
- Frontend: React
- Current-price cache: Redis
- Alert notifications: webhooks
- DB: PostgreSQL + TimescaleDB
- Must include: Pytest, GitHub Actions CI/CD

Architecture:
1. Lifespan task for upstream ingestion: Finnhub client NOT in a request endpoint; starts on FastAPI boot, stops on shutdown (lifespan).
2. FastAPI Connection Manager: lightweight class tracking connected React browser WebSockets and fanning out ticks.
3. Symbol subscription management: React sends request to FastAPI, which forwards subscribe command over the single Finnhub connection (ref-count symbols across browsers; unsubscribe when last leaves).

Project root: C:\Users\AD\Downloads\Build-final (not a git repo yet; do not run git init/commit/push).
